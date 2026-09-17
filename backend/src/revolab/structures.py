"""External structure discovery + explicit import (Phase 15) application service.

This is an application/query + command sub-boundary in the same placement family
as `revolab.proteins`, `revolab.literature`, `revolab.search`, and `revolab.notes`:
it composes existing public contracts and adds no Core domain. Both the human
Objects workspace and the Agent-facing read-only structure-search Tool call the
SAME service — there is deliberately no frontend-specific and no Agent-specific
provider path.

The governing boundaries:

    Provider/resolver != durable PDB identity.
    PDB identity        != coordinate bytes.
    ContentStore custody != scientific origin.
    Structure           != Protein.
    Structure           != biological assembly / Complex.
    Import              != Evidence.
    A changed PDB snapshot never silently mutates or appends a Structure revision.

`discover_structures` is a read: it authorizes the CURRENT Project read access,
resolves the CURRENT provider/capability availability through the shared invocation
gate, invokes `StructureDiscoveryCapability.search`, re-bounds the result, and
returns a typed candidate projection. It performs **zero** durable writes and never
downloads coordinates.

`import_structure` is the explicit human command: current Project MUTATION
authority, then a CURRENT provider re-resolution of the stable `(authority,
native_id)` identity that ALSO returns the canonical PDBx/mmCIF bytes, then ONE
atomic global import bundle:

    ContentStore.put(coordinate bytes)          (immutable byte custody, OUTSIDE the DB txn)
    ArtifactReference(authority="revolab")      (content-addressed internal artifact)
    ExternalIdentity(authority="pdb")           (durable identity; not project-owned)
    ExternalReference                           (immutable resolver snapshot provenance)
    StructureSeries / StructureRevision         (ONE canonical Structure object)
    ExternalIdentity --"identity"--> StructureSeries
    ExternalReference --imported_as--> StructureRevision
    ArtifactReference --imported_as--> StructureRevision
    ProjectResourceLink + new-resource ResourceStewardship

It never trusts browser/model scientific metadata or coordinate content, never
creates Evidence or a Decision, never appends a revision to an existing imported
object, and fails closed on a partial/corrupt existing bundle or a changed external
snapshot.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from revolab import services
from revolab.capabilities import (
    DEFAULT_STRUCTURE_RESULT_LIMIT,
    MAX_STRUCTURE_AUTHORITY_CHARS,
    MAX_STRUCTURE_COORDINATE_BYTES,
    MAX_STRUCTURE_METHOD_CHARS,
    MAX_STRUCTURE_METHODS,
    MAX_STRUCTURE_NATIVE_ID_CHARS,
    MAX_STRUCTURE_QUERY_CHARS,
    MAX_STRUCTURE_RESULT_LIMIT,
    MAX_STRUCTURE_REVISION_TEXT_CHARS,
    MAX_STRUCTURE_TITLE_CHARS,
    STRUCTURE_COORDINATE_CONTENT_TYPE,
    STRUCTURE_COORDINATE_FORMAT,
    ResolvedStructureRecord,
    StructureCandidate,
    bounded_inert_text,
)
from revolab.content_store import ContentStore
from revolab.domain import persistence, provenance, scientific_object
from revolab.domain.discovery import resolve_structure, search_structures
from revolab.domain.errors import ConflictError, ValidationError
from revolab.domain.identity import (
    can_mutate,
    mutation_capable_membership,
    readable_membership,
    require_active_project,
)
from revolab.domain.types_registry import validate_payload
from revolab.drivers import DriverRegistry
from revolab.enums import CapabilityKind, ObjectType, RelationType, ResourceKind
from revolab.models import (
    ArtifactReference,
    ExternalIdentity,
    GlobalProvenanceEdge,
    ScientificObjectExternalIdentity,
    ScientificObjectRevision,
    ScientificObjectSeries,
)
from revolab.schemas import (
    StructureCandidateRead,
    StructureDiscoveryResultsRead,
    StructureImportRead,
)
from revolab.secret_store import SecretStore

# The Core-owned scientific vocabulary this slice uses.
STRUCTURE_OBJECT_TYPE = ObjectType.STRUCTURE.value
STRUCTURE_IDENTITY_KIND = "structure"
IDENTITY_QUALIFIER = "identity"

# The ONE byte-custody authority: `authority="revolab"` on an ArtifactReference
# means REvoLab can reproduce those exact bytes. It NEVER means REvoLab authored
# or scientifically originated them — origin is the `pdb:<entry>` ExternalIdentity
# plus the `imported_as` provenance. (Single-sourced from the byte-custody owner.)
INTERNAL_ARTIFACT_AUTHORITY = services.INTERNAL_ARTIFACT_AUTHORITY

# `scientific_object_series.name` is varchar(200).
MAX_SERIES_NAME_CHARS = 200

# The typed conflict returned when the CURRENT external entry no longer matches the
# snapshot the identity was imported from. Phase 15 deliberately implements NO
# refresh: it refuses rather than silently appending a revision.
SNAPSHOT_CHANGED_MESSAGE = (
    "PDB entry has changed since the imported snapshot. Refresh/re-import revision "
    "semantics are not implemented in Phase 15."
)
# The typed conflict returned when a durable external identity already exists but is
# NOT a complete, compatible Phase-15 import bundle (a manual `identity` mapping, a
# half-attached bundle, a wrong object type, a retired series, a missing
# `imported_as` edge, more than one source snapshot, or a snapshot digest that
# disagrees with the stored revision/artifact).
INCOMPATIBLE_BUNDLE_MESSAGE = (
    "the external identity is already asserted by a different or incomplete mapping; "
    "Phase 15 does not repair or augment it — explicit reconciliation is required"
)
CONCURRENT_IMPORT_MESSAGE = "structure import conflicted with a concurrent import; retry"

# The unique constraints the atomic create path can lose on. `uq_external_identity_authority_native`
# is the primary scientific-identity linearization point: because the WHOLE bundle
# is created in ONE transaction, a committed ExternalIdentity always implies a
# committed complete bundle.
_EXTERNAL_IDENTITY_CONSTRAINT = "uq_external_identity_authority_native"
_MAPPING_CONSTRAINT = "scientific_object_external_identities_pkey"


@dataclass(frozen=True)
class _ImportBundle:
    """The durable identity of one complete imported Structure bundle."""

    external_identity_id: UUID
    external_reference_id: UUID
    artifact_id: UUID
    structure_series_id: UUID
    structure_revision_id: UUID
    structure_name: str
    artifact_checksum: str
    snapshot_checksum: str


@dataclass(frozen=True)
class _Snapshot:
    """The server-built canonical normalized scientific import snapshot."""

    structure_payload: dict[str, Any]
    series_name: str
    coordinate_bytes: bytes
    coordinate_checksum: str
    checksum: str


# ---------------------------------------------------------------------------
# Discovery (read-only, zero persistence, no coordinate download)
# ---------------------------------------------------------------------------


def discover_structures(
    session: Session,
    registry: DriverRegistry,
    secret_store: SecretStore,
    actor_id: UUID,
    project_id: UUID,
    *,
    provider_key: str,
    query: str,
    limit: int = DEFAULT_STRUCTURE_RESULT_LIMIT,
) -> StructureDiscoveryResultsRead:
    """Run one bounded, authorization-aware external PDB archive search.

    Read-only: a viewer may discover structures when ordinary read policy permits
    (the capability kind is a read-only kind). Fails closed for a non-member, a
    tombstoned Project, an over-bound query/limit, an unknown/unavailable provider,
    or a typed provider failure. Nothing is persisted, and NO coordinate bytes are
    fetched — a page reload may require performing the search again, which is
    acceptable and deliberate. A `StructureCandidate` is NEVER a `SearchHit`.
    """
    readable_membership(session, actor_id, project_id)
    require_active_project(session, project_id)
    bounded_query = _bounded_query(query)
    bounded_limit = _bounded_limit(limit)
    result = search_structures(
        session,
        registry,
        secret_store,
        actor_id,
        provider_key,
        bounded_query,
        bounded_limit,
        permitted=services.project_policy_permits(
            session, actor_id, project_id, CapabilityKind.STRUCTURE_DISCOVERY
        ),
    )
    candidates = []
    for candidate in result.candidates[:bounded_limit]:
        if candidate.provider_key != provider_key:
            # A candidate attributed to a different provider is structurally
            # impossible: fail closed rather than silently degrade to a short list.
            raise ValidationError("provider returned a candidate from a different provider")
        candidates.append(_candidate_read(candidate))
    return StructureDiscoveryResultsRead(
        provider_key=provider_key,
        query=bounded_query,
        candidates=candidates,
    )


# ---------------------------------------------------------------------------
# Explicit human import (one atomic bundle + immutable byte custody)
# ---------------------------------------------------------------------------


def import_structure(
    session: Session,
    registry: DriverRegistry,
    secret_store: SecretStore,
    content_store: ContentStore,
    actor_id: UUID,
    project_id: UUID,
    *,
    provider_key: str,
    authority: str,
    native_id: str,
) -> StructureImportRead:
    """Explicitly import ONE PDB archive entry and its canonical mmCIF coordinates.

    Flow: current Project mutation authority -> current provider availability ->
    `resolve(authority, native_id)` (metadata + canonical PDBx/mmCIF bytes) ->
    verify the returned durable identity matches the request -> build the canonical
    normalized scientific snapshot SERVER-SIDE -> take ContentStore byte custody ->
    one atomic persistence bundle. Import creates no Evidence and no Decision, and
    it never mutates or refreshes an already-imported Structure.
    """
    mutation_capable_membership(session, actor_id, project_id)
    require_active_project(session, project_id)
    bounded_authority = _bounded_identity(authority, MAX_STRUCTURE_AUTHORITY_CHARS, "authority")
    bounded_native_id = _bounded_identity(native_id, MAX_STRUCTURE_NATIVE_ID_CHARS, "native_id")
    record = resolve_structure(
        session,
        registry,
        secret_store,
        actor_id,
        provider_key,
        bounded_authority,
        bounded_native_id,
        permitted=services.project_policy_permits(
            session, actor_id, project_id, CapabilityKind.STRUCTURE_DISCOVERY
        ),
    )
    # The provider must independently confirm the durable identity the caller named.
    # `structures.py` and the driver both apply the documented wwPDB alias rule
    # (`pdb_0000<legacy>` <-> `<legacy>`), so a provider that substitutes a
    # DIFFERENT archive entry fails before any durable write.
    if record.provider_key != provider_key:
        raise ValidationError("resolved structure came from a different provider")
    if record.authority != bounded_authority:
        raise ValidationError("resolved structure identity authority does not match the import request")
    confirmed_native_id = _bounded_identity(
        record.native_id, MAX_STRUCTURE_NATIVE_ID_CHARS, "native_id"
    )
    if not _structure_identity_confirmed(bounded_native_id, confirmed_native_id):
        raise ValidationError("resolved structure identity does not match the import request")
    coordinate_checksum = hashlib.sha256(record.coordinate_bytes).hexdigest()
    snapshot = _normalized_snapshot(record, confirmed_native_id, coordinate_checksum)
    bundle = _persist_bundle(
        session,
        content_store,
        actor_id,
        project_id,
        authority=bounded_authority,
        native_id=confirmed_native_id,
        snapshot=snapshot,
        provider_key=provider_key,
        record=record,
    )
    return StructureImportRead(
        structure_series_id=bundle.structure_series_id,
        structure_revision_id=bundle.structure_revision_id,
        coordinate_artifact_id=bundle.artifact_id,
        external_reference_id=bundle.external_reference_id,
        authority=bounded_authority,
        native_id=confirmed_native_id,
    )


def _structure_identity_confirmed(requested: str, confirmed: str) -> bool:
    """Did the provider confirm the SAME archive entry the caller named?

    Case-insensitive, and alias-aware for the officially documented wwPDB
    `pdb_0000<legacy>` <-> `<legacy>` alias. This mirrors the driver's own check
    (`drivers.rcsb.same_entry_identity`) so a substituted identity fails before any
    durable write even if a driver were mis-wired; the canonical form that becomes
    durable is the provider-confirmed one (TODO section 6).
    """
    if requested.casefold() == confirmed.casefold():
        return True
    return f"pdb_0000{confirmed.casefold()}" == requested.casefold() or (
        f"pdb_0000{requested.casefold()}" == confirmed.casefold()
    )


def structure_snapshot_checksum(
    structure_payload: dict[str, Any], coordinate_checksum: str
) -> str:
    """Deterministic digest of the NORMALIZED scientific import bundle.

    Conceptually `sha256({structure_payload, coordinate_checksum})` with sorted keys
    and no insignificant whitespace, so changing upstream response FORMATTING — or
    a title-only presentation edit — can never change scientific snapshot identity.
    It is snapshot identity, NOT proof of origin: origin comes from the
    ExternalIdentity + ExternalReference + `imported_as` provenance.
    """
    canonical = json.dumps(
        {"structure_payload": structure_payload, "coordinate_checksum": coordinate_checksum},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _normalized_snapshot(
    record: ResolvedStructureRecord, native_id: str, coordinate_checksum: str
) -> _Snapshot:
    """Build the canonical normalized scientific snapshot SERVER-SIDE.

    Only the two scientific fields that belong to the Structure snapshot are
    persisted. The bootstrap fields `pdb_id`, `coordinates_ref`, and `ligand_ref`
    deliberately stay null: durable identity already lives in
    `ExternalIdentity(pdb, native_id)`, the coordinate linkage already lives in the
    typed `imported_as` provenance edge to the internal `ArtifactReference`, and
    ligand semantics are a later structure-content phase. Copying any of them into
    the payload would create a second, non-authoritative representation of a fact
    that another part of the graph already owns.
    """
    resolution = _validated_resolution(record.resolution_angstrom)
    method = _normalized_method(record.experimental_methods)
    structure_payload = validate_payload(
        ObjectType.STRUCTURE,
        {
            "resolution": resolution,
            "method": method,
            "pdb_id": None,
            "coordinates_ref": None,
            "ligand_ref": None,
        },
    )
    if record.coordinate_format != STRUCTURE_COORDINATE_FORMAT:
        # Phase 15 imports PDBx/mmCIF only. A mis-wired driver returning any other
        # format (e.g. legacy PDB) must never be persisted under the canonical mmCIF
        # content type.
        raise ValidationError("resolved structure is not in the canonical PDBx/mmCIF format")
    if not coordinate_checksum or len(coordinate_checksum) != 64:
        raise ValidationError("resolved structure has no usable coordinate checksum")
    if not record.coordinate_bytes:
        raise ValidationError("resolved structure has no coordinate bytes")
    if len(record.coordinate_bytes) > MAX_STRUCTURE_COORDINATE_BYTES:
        # The driver already aborts an over-ceiling stream; re-applying the SAME
        # canonical ceiling here is defense-in-depth so a mis-wired driver cannot
        # push an unbounded blob into ContentStore. Never truncated — it fails.
        raise ValidationError("resolved structure coordinates exceed the supported size")
    title = bounded_inert_text(record.title, MAX_STRUCTURE_TITLE_CHARS)
    # A series name is governance/presentation metadata, never identity: the durable
    # identity is `(authority, native_id)`. Fall back to `PDB <native_id>` when the
    # provider exposes no usable title.
    series_name = (title or f"PDB {native_id}")[:MAX_SERIES_NAME_CHARS]
    return _Snapshot(
        structure_payload=structure_payload,
        series_name=series_name,
        coordinate_bytes=record.coordinate_bytes,
        coordinate_checksum=coordinate_checksum,
        checksum=structure_snapshot_checksum(structure_payload, coordinate_checksum),
    )


def _positive_float(value: Any) -> float | None:
    """A finite POSITIVE angstrom value, or None for anything else.

    `None` is the honest answer for a non-application structure (NMR, integrative)
    and for malformed presentation data on a candidate; the resolving path uses
    `_validated_resolution`, so malformed provider data fails closed there instead.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _validated_resolution(value: Any) -> float | None:
    """Fail closed on a malformed resolution before persistence.

    The driver already rejects NaN/infinity/zero/negative at the wire boundary and
    the Core type registry owns the same invariant; re-applying it here is
    defense-in-depth so a mis-wired driver cannot persist an invented resolution. A
    non-application structure (NMR, integrative) legitimately has NO resolution and
    stays null.
    """
    if value is None:
        return None
    number = _positive_float(value)
    if number is None:
        raise ValidationError("resolved structure reported an invalid resolution")
    return number


def _normalized_method(methods: tuple[str, ...]) -> str | None:
    """One documented deterministic representation of the experimental method(s).

    The driver already applies this rule at the wire boundary, and it is re-applied
    here as defense-in-depth: each method is re-bounded to inert text, de-duplicated
    case-insensitively, SORTED, and the count is bounded by `MAX_STRUCTURE_METHODS`.
    The persisted string therefore never depends on arbitrary provider ordering, and
    a mis-wired driver cannot persist an over-long or order-dependent method string.
    Multiple methods are joined with `"; "` rather than silently dropped or
    arbitrarily chosen.
    """
    seen: dict[str, str] = {}
    for raw in methods:
        method = bounded_inert_text(raw, MAX_STRUCTURE_METHOD_CHARS)
        if method is None:
            continue
        seen.setdefault(method.casefold(), method)
    if len(seen) > MAX_STRUCTURE_METHODS:
        raise ValidationError("resolved structure reported too many experimental methods")
    if not seen:
        return None
    return "; ".join(seen[key] for key in sorted(seen))


# ---------------------------------------------------------------------------
# Atomic persistence
# ---------------------------------------------------------------------------


def _persist_bundle(
    session: Session,
    content_store: ContentStore,
    actor_id: UUID,
    project_id: UUID,
    *,
    authority: str,
    native_id: str,
    snapshot: _Snapshot,
    provider_key: str,
    record: ResolvedStructureRecord,
) -> _ImportBundle:
    """Get-or-create the ONE global import bundle for this external identity.

    Same Project repeat import is idempotent, cross-Project import reuses the same
    global objects (linking only, never transferring stewardship), a changed
    snapshot fails closed, and a concurrent first import converges on the committed
    winner without leaking a raw `IntegrityError`.

    Byte-custody truthfulness: ContentStore and PostgreSQL are NOT one distributed
    transaction. `store.put` happens INSIDE the create path (before the single
    commit), so a later DB rollback may leave an UNREACHABLE content-addressed blob
    behind. That is not Project truth and is safely reused by a later successful
    import; no two-phase commit is pretended. The existing-identity path never
    writes a blob at all (TODO section 36).
    """
    existing = scientific_object.find_external_identity(session, authority, native_id)
    if existing is not None:
        bundle = _load_bundle(session, existing)
        _assert_snapshot_matches(bundle, snapshot.checksum)
        _link_bundle(session, project_id, bundle)
        session.commit()
        return bundle
    try:
        bundle = _create_bundle(
            session,
            content_store,
            actor_id,
            project_id,
            authority=authority,
            native_id=native_id,
            snapshot=snapshot,
            provider_key=provider_key,
            record=record,
        )
    except IntegrityError as exc:
        session.rollback()
        if not _is_import_conflict(exc):
            raise
        winner = scientific_object.find_external_identity(session, authority, native_id)
        if winner is None:
            raise ConflictError(CONCURRENT_IMPORT_MESSAGE) from exc
        bundle = _load_bundle(session, winner)
        _assert_snapshot_matches(bundle, snapshot.checksum)
        _link_bundle(session, project_id, bundle)
        session.commit()
        return bundle
    session.commit()
    return bundle


def _create_bundle(
    session: Session,
    content_store: ContentStore,
    actor_id: UUID,
    project_id: UUID,
    *,
    authority: str,
    native_id: str,
    snapshot: _Snapshot,
    provider_key: str,
    record: ResolvedStructureRecord,
) -> _ImportBundle:
    """Create the WHOLE import bundle in ONE caller-owned transaction.

    Every step below only flushes; the single `session.commit()` happens in
    `_persist_bundle`. If any step fails, ordinary rollback leaves NONE of the local
    durable import behind: no orphan series, revision, identity mapping, reference,
    artifact, link, stewardship, or provenance edge. (The ContentStore blob is
    immutable, content-addressed, and OUTSIDE that transaction; see
    `_persist_bundle`.)

    The ExternalIdentity insert is the FIRST durable write and is protected by
    `uq_external_identity_authority_native`, so it is the concurrency linearization
    point for a first import.
    """
    identity = scientific_object.create_external_identity(
        session, authority, native_id, kind=STRUCTURE_IDENTITY_KIND
    )
    # Immutable byte custody through the EXISTING internal-artifact path (never a
    # second blob store). `create_internal_artifact` performs the ContentStore `put`
    # and the shared content-addressed get-or-create, which is race-safe.
    artifact = services.create_internal_artifact(
        session,
        actor_id,
        project_id,
        content_store,
        snapshot.coordinate_bytes,
        content_type=STRUCTURE_COORDINATE_CONTENT_TYPE,
        commit=False,
    )
    if artifact.checksum != snapshot.coordinate_checksum:
        raise ConflictError("coordinate artifact checksum disagrees with the imported bytes")
    reference = provenance.create_external_reference_row(
        session,
        identity.external_identity_id,
        checksum=snapshot.checksum,
        as_of=datetime.now(UTC),
        cache_metadata=_cache_metadata(provider_key, record),
    )
    structure_series_id, structure_revision_id = scientific_object.create_spine(
        session,
        actor_id,
        STRUCTURE_OBJECT_TYPE,
        snapshot.series_name,
        description=None,
        payload=snapshot.structure_payload,
    )
    # The ExternalReference row itself is linked+stewarded by the importing Project
    # exactly like every other reference node; the ExternalIdentity is NOT (it is
    # the global identity registry, never Project-owned). The internal
    # ArtifactReference was already linked+stewarded by `create_internal_artifact`.
    persistence.link(session, project_id, reference.external_reference_id)
    persistence.steward(session, project_id, reference.external_reference_id)
    persistence.link(session, project_id, structure_series_id)
    persistence.link(session, project_id, structure_revision_id)
    # A newly created global scientific object is stewarded by the importing
    # Project — never by the user or the provider.
    persistence.steward(session, project_id, structure_series_id)
    session.flush()

    grant = can_mutate(
        session, actor_id, project_id, structure_series_id, purpose="import structure"
    )
    # ONE ExternalIdentity, one semantic target: `identity` -> StructureSeries.
    scientific_object.attach_external_identity(
        session,
        grant,
        structure_series_id,
        authority,
        native_id,
        qualifier=IDENTITY_QUALIFIER,
        kind=STRUCTURE_IDENTITY_KIND,
        is_canonical=True,
    )
    # The frozen #7 `imported_as` grammar, used TWICE for the two distinct truths:
    # the external scientific source, and the exact byte artifact it was imported as.
    services.record_imported_as(
        session, actor_id, project_id, structure_revision_id, reference.external_reference_id,
        commit=False,
    )
    services.record_imported_as(
        session, actor_id, project_id, structure_revision_id, artifact.artifact_id,
        commit=False,
    )
    return _ImportBundle(
        external_identity_id=identity.external_identity_id,
        external_reference_id=reference.external_reference_id,
        artifact_id=artifact.artifact_id,
        structure_series_id=structure_series_id,
        structure_revision_id=structure_revision_id,
        structure_name=snapshot.series_name,
        artifact_checksum=snapshot.coordinate_checksum,
        snapshot_checksum=snapshot.checksum,
    )


def _load_bundle(session: Session, identity: ExternalIdentity) -> _ImportBundle:
    """Read the existing import bundle and fail closed unless it is COMPLETE.

    An identity that is not a complete, internally consistent Phase-15 bundle — a
    manual `identity` mapping, a half-attached bundle, a wrong object type, a
    retired (archived) series, a disagreeing identity `kind`, a non-canonical
    qualifier mapping, a missing `imported_as` edge, an ambiguous source snapshot, a
    missing internal coordinate artifact, or a stored snapshot checksum that does
    not match the stored revision payload and artifact checksum — is NEVER repaired
    or augmented here.
    """
    identity_id = identity.external_identity_id
    if identity.kind != STRUCTURE_IDENTITY_KIND:
        # The identity registry is global and shared: an identity created for a
        # different semantic kind is a different assertion, not a structure bundle.
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    mapping = session.get(ScientificObjectExternalIdentity, (identity_id, IDENTITY_QUALIFIER))
    if mapping is None or not mapping.is_canonical:
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    series = session.get(ScientificObjectSeries, mapping.series_id)
    if series is None or series.object_type != STRUCTURE_OBJECT_TYPE:
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    if series.archived_at is not None:
        # A retired global object is not a live import target: never link new
        # Project context to an archived series.
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    references = provenance.find_external_references(session, identity_id)
    if len(references) != 1:
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    reference = references[0]
    stored_checksum = reference.checksum
    if stored_checksum is None:
        # No comparable snapshot provenance => not a Phase-15 bundle.
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    revision = _imported_revision(
        session, reference.external_reference_id, series.series_id
    )
    if revision is None:
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    artifact = _imported_artifact(session, revision.revision_id)
    if artifact is None:
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    if artifact.authority != INTERNAL_ARTIFACT_AUTHORITY:
        # Imported coordinates are under REvoLab byte custody; a provider-authority
        # artifact would leave the Structure dependent on live provider availability.
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    if artifact.checksum is None or artifact.native_id != artifact.checksum:
        # A content-addressed internal artifact is addressed by its own checksum.
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    if artifact.size is None or artifact.size < 1:
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    if artifact.content_type != STRUCTURE_COORDINATE_CONTENT_TYPE:
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    # The stored snapshot digest must actually describe the stored revision payload
    # and the stored coordinate artifact. Without this an out-of-band change to the
    # digest (or a future regression) could make a stale bundle look "current" and
    # re-link stale content as current, which the changed-snapshot rule forbids.
    if (
        structure_snapshot_checksum(revision.payload, artifact.checksum) != stored_checksum
    ):
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    return _ImportBundle(
        external_identity_id=identity_id,
        external_reference_id=reference.external_reference_id,
        artifact_id=artifact.artifact_id,
        structure_series_id=series.series_id,
        structure_revision_id=revision.revision_id,
        # The STORED series name, never the current provider title: a re-import must
        # not imply that presentation metadata was refreshed.
        structure_name=series.name,
        artifact_checksum=artifact.checksum,
        snapshot_checksum=stored_checksum,
    )


def _imported_revision(
    session: Session, external_reference_id: UUID, series_id: UUID
) -> ScientificObjectRevision | None:
    """The UNIQUE revision of `series_id` that this reference was imported as."""
    revisions = list(
        session.scalars(
            select(ScientificObjectRevision)
            .join(
                GlobalProvenanceEdge,
                GlobalProvenanceEdge.target_id == ScientificObjectRevision.revision_id,
            )
            .where(
                GlobalProvenanceEdge.relation_type == RelationType.IMPORTED_AS.value,
                GlobalProvenanceEdge.source_id == external_reference_id,
                ScientificObjectRevision.series_id == series_id,
            )
        )
    )
    return revisions[0] if len(revisions) == 1 else None


def _imported_artifact(session: Session, revision_id: UUID) -> ArtifactReference | None:
    """The UNIQUE internal ArtifactReference imported as this exact revision."""
    edges = list(
        session.scalars(
            select(GlobalProvenanceEdge).where(
                GlobalProvenanceEdge.relation_type == RelationType.IMPORTED_AS.value,
                GlobalProvenanceEdge.target_id == revision_id,
                GlobalProvenanceEdge.source_kind == ResourceKind.ARTIFACT_REFERENCE.value,
            )
        )
    )
    if len(edges) != 1:
        return None
    return session.get(ArtifactReference, edges[0].source_id)


def _assert_snapshot_matches(bundle: _ImportBundle, current_checksum: str) -> None:
    """Fail closed when the CURRENT provider snapshot differs from the imported one.

    Phase 15 implements NO refresh: it must never silently mutate a revision,
    append a revision, relabel an existing object, replace the coordinate artifact,
    or link stale data as current.
    """
    if bundle.snapshot_checksum != current_checksum:
        raise ConflictError(SNAPSHOT_CHANGED_MESSAGE)


def _link_bundle(session: Session, project_id: UUID, bundle: _ImportBundle) -> None:
    """Link an EXISTING global bundle into a Project (idempotent; no stewardship).

    Cross-Project import reuses the SAME global series/revision/reference/artifact
    and gains only its own read lens: `ProjectResourceLink` grants visibility, and
    `ResourceStewardship` alone authorizes mutation, so the second Project never
    steals stewardship from the first.
    """
    for resource_id in (
        bundle.structure_series_id,
        bundle.structure_revision_id,
        bundle.external_reference_id,
        bundle.artifact_id,
    ):
        _link_idempotent(session, project_id, resource_id)


def _link_idempotent(session: Session, project_id: UUID, resource_id: UUID) -> None:
    """Insert one `ProjectResourceLink`, resolving a concurrent duplicate.

    `persistence.link` is read-then-insert, so two Projects (or two racers) can both
    observe "not visible". A SAVEPOINT per link keeps the other links in this bundle
    intact while the losing insert is rolled back and re-checked, so a loser resolves
    to the committed winner instead of leaking a raw `IntegrityError`.
    """
    try:
        with session.begin_nested():
            persistence.link(session, project_id, resource_id)
    except IntegrityError as exc:
        if not _is_link_conflict(exc):
            raise
        if not persistence.is_visible(session, project_id, resource_id):
            raise ConflictError(CONCURRENT_IMPORT_MESSAGE) from exc


def _is_external_identity_conflict(exc: IntegrityError) -> bool:
    """Narrow detection of `uq_external_identity_authority_native`."""
    name = _constraint_name(exc)
    if name is not None:  # PostgreSQL psycopg
        return name == _EXTERNAL_IDENTITY_CONSTRAINT
    message = str(exc.orig)
    return "UNIQUE constraint failed" in message and "external_identities.authority" in message


def _is_mapping_conflict(exc: IntegrityError) -> bool:
    """Narrow detection of the `scientific_object_external_identities` PK."""
    name = _constraint_name(exc)
    if name is not None:  # PostgreSQL psycopg
        return name == _MAPPING_CONSTRAINT
    message = str(exc.orig)
    return "UNIQUE constraint failed" in message and "scientific_object_external_identities" in message


def _is_link_conflict(exc: IntegrityError) -> bool:
    """The ONE shared definition of the `uq_link_project_resource` conflict."""
    return persistence.is_link_uniqueness_conflict(exc)


def _is_import_conflict(exc: IntegrityError) -> bool:
    """Any integrity failure the concurrent-import recovery path may resolve.

    The internal content-addressed ArtifactReference is deliberately NOT in this
    set: its get-or-create is a single atomic insert-if-absent, so it can never
    raise `uq_artifact_identity` and never aborts the loser's transaction.
    """
    return (
        _is_external_identity_conflict(exc)
        or _is_mapping_conflict(exc)
        or _is_link_conflict(exc)
    )


def _constraint_name(exc: IntegrityError) -> str | None:
    diagnostics = getattr(exc.orig, "diag", None)
    return str(diagnostics.constraint_name) if diagnostics is not None else None


def _cache_metadata(provider_key: str, record: ResolvedStructureRecord) -> dict[str, Any]:
    """Bounded, normalized resolver/cache metadata (never a provider payload copy).

    Only fields with a concrete provenance/debugging use are stored: the RESOLVER
    provider key (never the durable authority) and the PDB entry revision triple.
    Raw Search API responses, Data API JSON, author lists, entity annotations, GO
    and UniProt cross-references, ligand tables, validation metrics, and HTTP
    headers are never retained.
    """
    metadata: dict[str, Any] = {
        "resolver_provider": bounded_inert_text(provider_key, MAX_STRUCTURE_AUTHORITY_CHARS)
        or provider_key[:MAX_STRUCTURE_AUTHORITY_CHARS]
    }
    if isinstance(record.entry_revision_major, int):
        metadata["pdb_revision_major"] = record.entry_revision_major
    if isinstance(record.entry_revision_minor, int):
        metadata["pdb_revision_minor"] = record.entry_revision_minor
    revision_date = bounded_inert_text(
        record.entry_revision_date, MAX_STRUCTURE_REVISION_TEXT_CHARS
    )
    if revision_date is not None:
        metadata["pdb_revision_date"] = revision_date
    return metadata


# ---------------------------------------------------------------------------
# Bounds / projection
# ---------------------------------------------------------------------------


def _bounded_query(query: str) -> str:
    if not isinstance(query, str):
        raise ValidationError("structure search query must be text")
    text = query.strip()
    if not text or len(text) > MAX_STRUCTURE_QUERY_CHARS:
        raise ValidationError(
            f"structure search query must be 1..{MAX_STRUCTURE_QUERY_CHARS} characters"
        )
    return text


def _bounded_limit(limit: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValidationError("structure result limit must be a positive integer")
    if limit > MAX_STRUCTURE_RESULT_LIMIT:
        raise ValidationError(
            f"structure result limit must not exceed {MAX_STRUCTURE_RESULT_LIMIT}"
        )
    return limit


def _bounded_identity(value: str, limit: int, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"structure {field} must be text")
    text = value.strip()
    if not text or len(text) > limit:
        raise ValidationError(f"structure {field} must be 1..{limit} characters")
    return text


def _candidate_read(candidate: StructureCandidate) -> StructureCandidateRead:
    """Re-bound a driver-returned candidate into the typed wire projection.

    The driver already applies these limits; re-applying them here is
    defense-in-depth so a mis-wired driver cannot widen the frontend/Agent surface.
    The method set is re-bounded, de-duplicated case-insensitively, SORTED, and
    truncated to the canonical count (honest bounded presentation data); coordinate
    bytes are structurally absent from a candidate. Text stays UNTRUSTED inert data.
    """
    seen: dict[str, str] = {}
    for raw in candidate.experimental_methods:
        method = bounded_inert_text(raw, MAX_STRUCTURE_METHOD_CHARS)
        if method is not None:
            seen.setdefault(method.casefold(), method)
    methods = [seen[key] for key in sorted(seen)][:MAX_STRUCTURE_METHODS]
    resolution = _positive_float(candidate.resolution_angstrom)
    polymer_entity_count = candidate.polymer_entity_count
    if (
        not isinstance(polymer_entity_count, int)
        or isinstance(polymer_entity_count, bool)
        or polymer_entity_count < 1
    ):
        polymer_entity_count = None
    return StructureCandidateRead(
        provider_key=candidate.provider_key,
        authority=bounded_inert_text(candidate.authority, MAX_STRUCTURE_AUTHORITY_CHARS) or "",
        native_id=bounded_inert_text(candidate.native_id, MAX_STRUCTURE_NATIVE_ID_CHARS) or "",
        title=bounded_inert_text(candidate.title, MAX_STRUCTURE_TITLE_CHARS),
        experimental_methods=methods,
        resolution_angstrom=resolution,
        release_date=bounded_inert_text(
            candidate.release_date, MAX_STRUCTURE_REVISION_TEXT_CHARS
        ),
        polymer_entity_count=polymer_entity_count,
    )


__all__ = [
    "INCOMPATIBLE_BUNDLE_MESSAGE",
    "SNAPSHOT_CHANGED_MESSAGE",
    "discover_structures",
    "import_structure",
    "structure_snapshot_checksum",
]
