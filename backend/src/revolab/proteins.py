"""External protein discovery + explicit import (Phase 14) application service.

This is an application/query + command sub-boundary in the same placement family
as `revolab.search`, `revolab.notes`, and `revolab.literature`: it composes existing
public contracts and adds no Core domain. Both the human Objects workspace and the
Agent-facing read-only protein-search Tool call the SAME service — there is
deliberately no frontend-specific and no Agent-specific provider path.

The governing boundaries:

    External candidate != Project truth.
    Provider/resolver != durable identity authority.
    Protein != Sequence.
    Import provenance != Evidence.
    A changed external record never silently mutates an imported ScientificObject.

`discover_proteins` is a read: it authorizes the CURRENT Project read access,
resolves the CURRENT provider/capability availability through the shared invocation
gate, invokes `ProteinDiscoveryCapability.search`, re-bounds the result, and returns
a typed candidate projection. It performs **zero** durable writes.

`import_protein` is the explicit human command: current Project MUTATION authority,
then a CURRENT provider re-resolution of the stable `(authority, native_id)`
identity, then ONE atomic global import bundle:

    ExternalIdentity                     (durable identity; not project-owned)
    ExternalReference                    (immutable resolver snapshot provenance)
    ProteinSeries / ProteinRevision      (the biological concept)
    SequenceSeries / SequenceRevision    (the exact canonical amino-acid snapshot)
    ExternalIdentity --"identity"-->  ProteinSeries
    ExternalIdentity --"sequence"-->  SequenceSeries
    SequenceSeries  --represents-->    ProteinSeries
    ExternalReference --imported_as--> ProteinRevision
    ExternalReference --imported_as--> SequenceRevision
    ProjectResourceLink + new-resource ResourceStewardship

It never trusts browser/model scientific metadata, never creates Evidence or a
Decision, never appends a revision to an existing imported object, and fails closed
on a partial/corrupt existing bundle or a changed external snapshot.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from revolab import services
from revolab.capabilities import (
    DEFAULT_PROTEIN_RESULT_LIMIT,
    MAX_PROTEIN_AUTHORITY_CHARS,
    MAX_PROTEIN_GENE_NAME_CHARS,
    MAX_PROTEIN_NAME_CHARS,
    MAX_PROTEIN_NATIVE_ID_CHARS,
    MAX_PROTEIN_ORGANISM_NAME_CHARS,
    MAX_PROTEIN_QUERY_CHARS,
    MAX_PROTEIN_RESULT_LIMIT,
    MAX_PROTEIN_SEQUENCE_CHARS,
    MAX_PROTEIN_SOURCE_RELEASE_CHARS,
    PROTEIN_SEQUENCE_ALPHABET,
    ProteinCandidate,
    ResolvedProteinRecord,
    bounded_inert_text,
)
from revolab.domain import persistence, provenance, scientific_object
from revolab.domain.discovery import resolve_protein, search_proteins
from revolab.domain.errors import ConflictError, ValidationError
from revolab.domain.identity import (
    can_mutate,
    mutation_capable_membership,
    readable_membership,
    require_active_project,
)
from revolab.domain.types_registry import validate_payload
from revolab.drivers import DriverRegistry
from revolab.enums import CapabilityKind, ObjectType, RelationType
from revolab.models import (
    ExternalIdentity,
    GlobalProvenanceEdge,
    ScientificObjectExternalIdentity,
    ScientificObjectRevision,
    ScientificObjectSeries,
)
from revolab.schemas import (
    ProteinCandidateRead,
    ProteinDiscoveryResultsRead,
    ProteinImportRead,
)
from revolab.secret_store import SecretStore

# The Core-owned scientific vocabulary this slice uses. `Protein` and `Sequence`
# are the existing `ObjectType` members; the qualifiers are the existing
# `ExternalIdentity` mapping vocabulary (one semantic target per qualifier).
PROTEIN_OBJECT_TYPE = ObjectType.PROTEIN.value
SEQUENCE_OBJECT_TYPE = ObjectType.SEQUENCE.value
PROTEIN_IDENTITY_KIND = "protein"
CANONICAL_SEQUENCE_KIND = "protein"
IDENTITY_QUALIFIER = "identity"
SEQUENCE_QUALIFIER = "sequence"

# `scientific_object_series.name` is varchar(200).
MAX_SERIES_NAME_CHARS = 200

# The typed conflict returned when the CURRENT external record no longer matches the
# snapshot the identity was imported from. Phase 14 deliberately implements NO
# refresh: it refuses rather than silently appending a revision.
SNAPSHOT_CHANGED_MESSAGE = (
    "External record has changed since the imported snapshot. Refresh/re-import "
    "revision semantics are not implemented in Phase 14."
)
# The typed conflict returned when a durable external identity already exists but is
# NOT a complete, compatible Phase-14 import bundle (a manual `identity` mapping, a
# half-attached pair, a wrong object type, a missing `represents`/`imported_as`
# edge, or more than one source snapshot).
INCOMPATIBLE_BUNDLE_MESSAGE = (
    "the external identity is already asserted by a different or incomplete mapping; "
    "Phase 14 does not repair or augment it — explicit reconciliation is required"
)
CONCURRENT_IMPORT_MESSAGE = "protein import conflicted with a concurrent import; retry"

# The two unique constraints the atomic create path can lose on. `uq_external_identity_authority_native`
# is the linearization point: because the WHOLE bundle is created in ONE transaction,
# a committed ExternalIdentity always implies a committed complete bundle.
_EXTERNAL_IDENTITY_CONSTRAINT = "uq_external_identity_authority_native"
_MAPPING_CONSTRAINT = "scientific_object_external_identities_pkey"


@dataclass(frozen=True)
class _ImportBundle:
    """The durable identity of one complete imported Protein + Sequence bundle."""

    external_identity_id: UUID
    external_reference_id: UUID
    protein_series_id: UUID
    protein_revision_id: UUID
    sequence_series_id: UUID
    sequence_revision_id: UUID
    protein_name: str
    snapshot_checksum: str


@dataclass(frozen=True)
class _Snapshot:
    """The server-built canonical normalized scientific import snapshot."""

    protein_payload: dict[str, Any]
    sequence_payload: dict[str, Any]
    protein_series_name: str
    sequence_series_name: str
    checksum: str


# ---------------------------------------------------------------------------
# Discovery (read-only, zero persistence)
# ---------------------------------------------------------------------------


def discover_proteins(
    session: Session,
    registry: DriverRegistry,
    secret_store: SecretStore,
    actor_id: UUID,
    project_id: UUID,
    *,
    provider_key: str,
    query: str,
    limit: int = DEFAULT_PROTEIN_RESULT_LIMIT,
) -> ProteinDiscoveryResultsRead:
    """Run one bounded, authorization-aware external protein search.

    Read-only: a viewer may discover proteins when ordinary read policy permits
    (the capability kind is a read-only kind). Fails closed for a non-member, a
    tombstoned Project, an over-bound query/limit, an unknown/unavailable provider,
    or a typed provider failure. Nothing is persisted — a page reload may require
    performing the search again, which is acceptable and deliberate. A
    `ProteinCandidate` is NEVER a `SearchHit`.
    """
    readable_membership(session, actor_id, project_id)
    require_active_project(session, project_id)
    bounded_query = _bounded_query(query)
    bounded_limit = _bounded_limit(limit)
    result = search_proteins(
        session,
        registry,
        secret_store,
        actor_id,
        provider_key,
        bounded_query,
        bounded_limit,
        permitted=services.project_policy_permits(
            session, actor_id, project_id, CapabilityKind.PROTEIN_DISCOVERY
        ),
    )
    candidates = []
    for candidate in result.candidates[:bounded_limit]:
        if candidate.provider_key != provider_key:
            # A candidate attributed to a different provider is structurally
            # impossible: fail closed rather than silently degrade to a short list.
            raise ValidationError("provider returned a candidate from a different provider")
        candidates.append(_candidate_read(candidate))
    return ProteinDiscoveryResultsRead(
        provider_key=provider_key,
        query=bounded_query,
        candidates=candidates,
    )


# ---------------------------------------------------------------------------
# Explicit human import (one atomic bundle)
# ---------------------------------------------------------------------------


def import_protein(
    session: Session,
    registry: DriverRegistry,
    secret_store: SecretStore,
    actor_id: UUID,
    project_id: UUID,
    *,
    provider_key: str,
    authority: str,
    native_id: str,
) -> ProteinImportRead:
    """Explicitly import ONE protein (with its canonical sequence) into the Project.

    Flow: current Project mutation authority -> current provider availability ->
    `resolve(authority, native_id)` -> verify the returned durable identity matches
    the request -> build the canonical normalized scientific snapshot SERVER-SIDE ->
    one atomic persistence bundle. Import creates no Evidence and no Decision, and it
    never mutates or refreshes an already-imported ScientificObject.
    """
    mutation_capable_membership(session, actor_id, project_id)
    require_active_project(session, project_id)
    bounded_authority = _bounded_identity(authority, MAX_PROTEIN_AUTHORITY_CHARS, "authority")
    bounded_native_id = _bounded_identity(native_id, MAX_PROTEIN_NATIVE_ID_CHARS, "native_id")
    record = resolve_protein(
        session,
        registry,
        secret_store,
        actor_id,
        provider_key,
        bounded_authority,
        bounded_native_id,
        permitted=services.project_policy_permits(
            session, actor_id, project_id, CapabilityKind.PROTEIN_DISCOVERY
        ),
    )
    # The provider must independently confirm the EXACT durable identity that was
    # requested; a mismatched or substituted record fails closed before any write.
    if record.provider_key != provider_key:
        raise ValidationError("resolved protein came from a different provider")
    if record.authority != bounded_authority or record.native_id != bounded_native_id:
        raise ValidationError("resolved protein identity does not match the import request")
    snapshot = _normalized_snapshot(record, bounded_authority, bounded_native_id)
    bundle = _persist_bundle(
        session,
        actor_id,
        project_id,
        authority=bounded_authority,
        native_id=bounded_native_id,
        snapshot=snapshot,
        provider_key=provider_key,
        record=record,
    )
    return ProteinImportRead(
        protein_series_id=bundle.protein_series_id,
        protein_revision_id=bundle.protein_revision_id,
        sequence_series_id=bundle.sequence_series_id,
        sequence_revision_id=bundle.sequence_revision_id,
        external_reference_id=bundle.external_reference_id,
        authority=bounded_authority,
        native_id=bounded_native_id,
        protein_name=bundle.protein_name,
    )


def protein_snapshot_checksum(
    protein_payload: dict[str, Any], sequence_payload: dict[str, Any]
) -> str:
    """Deterministic digest of the NORMALIZED scientific import bundle.

    Conceptually `sha256({protein_payload, sequence_payload})` with sorted keys and
    no insignificant whitespace, so changing upstream response FORMATTING can never
    change scientific snapshot identity. It is snapshot identity, NOT proof of
    origin: origin comes from the ExternalIdentity + ExternalReference +
    `imported_as` provenance.
    """
    canonical = json.dumps(
        {"protein_payload": protein_payload, "sequence_payload": sequence_payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _normalized_snapshot(
    record: ResolvedProteinRecord, bounded_authority: str, bounded_native_id: str
) -> _Snapshot:
    """Build the canonical normalized scientific snapshot SERVER-SIDE.

    The exact canonical amino-acid sequence becomes the Sequence revision content
    (never ad-hoc Protein metadata), and the Protein payload carries only the
    bounded organism. Both payloads are validated against the Core type registry
    BEFORE the checksum is taken, so the digest covers exactly what is persisted and
    an invalid payload can never reach a durable write.
    """
    sequence = _validated_sequence(record.canonical_sequence)
    if record.sequence_length is not None and record.sequence_length != len(sequence):
        raise ValidationError(
            "resolved protein sequence length disagrees with the canonical sequence"
        )
    organism = bounded_inert_text(record.organism_name, MAX_PROTEIN_ORGANISM_NAME_CHARS)
    protein_payload = validate_payload(
        ObjectType.PROTEIN,
        # `source_sequence_ref` stays null: the typed `represents` relation IS the
        # relationship truth, and duplicating it in a payload string would create a
        # second, non-authoritative representation of the same fact.
        {"organism": organism, "source_sequence_ref": None, "chain": None},
    )
    sequence_payload = validate_payload(
        ObjectType.SEQUENCE,
        {"kind": CANONICAL_SEQUENCE_KIND, "sequence": sequence},
    )
    protein_name = bounded_inert_text(record.protein_name, MAX_PROTEIN_NAME_CHARS)
    # A series name is governance/presentation metadata, never identity: the durable
    # identity is `(authority, native_id)`. Fall back to the accession when the
    # provider exposes no recommended protein name.
    protein_series_name = (protein_name or bounded_native_id)[:MAX_SERIES_NAME_CHARS]
    return _Snapshot(
        protein_payload=protein_payload,
        sequence_payload=sequence_payload,
        protein_series_name=protein_series_name,
        sequence_series_name=f"{bounded_native_id} canonical sequence"[:MAX_SERIES_NAME_CHARS],
        checksum=protein_snapshot_checksum(protein_payload, sequence_payload),
    )


def _validated_sequence(value: Any) -> str:
    """Fail closed on a malformed canonical sequence before persistence.

    The driver already enforces this at the wire boundary; re-applying it here is
    defense-in-depth so a mis-wired driver cannot persist a sequence that is empty,
    over-bound, or contains whitespace/markup/lowercase/other non-amino-acid
    symbols. The COMPLETE bounded sequence is stored — never a truncated one.
    """
    if not isinstance(value, str) or not value:
        raise ValidationError("resolved protein has no canonical sequence")
    if len(value) > MAX_PROTEIN_SEQUENCE_CHARS:
        raise ValidationError("resolved protein sequence exceeds the supported length")
    if not PROTEIN_SEQUENCE_ALPHABET.issuperset(value):
        raise ValidationError("resolved protein sequence contains invalid amino-acid symbols")
    return value


# ---------------------------------------------------------------------------
# Atomic persistence
# ---------------------------------------------------------------------------


def _persist_bundle(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    *,
    authority: str,
    native_id: str,
    snapshot: _Snapshot,
    provider_key: str,
    record: ResolvedProteinRecord,
) -> _ImportBundle:
    """Get-or-create the ONE global import bundle for this external identity.

    Same Project repeat import is idempotent, cross-Project import reuses the same
    global objects (linking only, never transferring stewardship), a changed
    snapshot fails closed, and a concurrent first import converges on the committed
    winner without leaking a raw `IntegrityError`.
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
    actor_id: UUID,
    project_id: UUID,
    *,
    authority: str,
    native_id: str,
    snapshot: _Snapshot,
    provider_key: str,
    record: ResolvedProteinRecord,
) -> _ImportBundle:
    """Create the WHOLE import bundle in ONE caller-owned transaction.

    Every step below only flushes; the single `session.commit()` happens in
    `_persist_bundle`. If any step fails, ordinary rollback leaves NONE of the
    local durable import behind: no orphan series, revision, identity mapping,
    reference, link, stewardship, or provenance edge.

    The ExternalIdentity insert is the FIRST durable write and is protected by
    `uq_external_identity_authority_native`, so it is the concurrency linearization
    point for a first import.
    """
    identity = scientific_object.get_or_create_external_identity(
        session, authority, native_id, kind=PROTEIN_IDENTITY_KIND
    )
    reference = provenance.create_external_reference_row(
        session,
        identity.external_identity_id,
        checksum=snapshot.checksum,
        as_of=datetime.now(UTC),
        cache_metadata=_cache_metadata(provider_key, record),
    )
    protein_series_id, protein_revision_id = scientific_object.create_spine(
        session,
        actor_id,
        PROTEIN_OBJECT_TYPE,
        snapshot.protein_series_name,
        description=None,
        payload=snapshot.protein_payload,
    )
    sequence_series_id, sequence_revision_id = scientific_object.create_spine(
        session,
        actor_id,
        SEQUENCE_OBJECT_TYPE,
        snapshot.sequence_series_name,
        description=None,
        payload=snapshot.sequence_payload,
    )
    # The ExternalReference row itself is linked+stewarded by the importing Project
    # exactly like every other reference node; the ExternalIdentity is NOT (it is
    # the global identity registry, never Project-owned).
    persistence.link(session, project_id, reference.external_reference_id)
    persistence.steward(session, project_id, reference.external_reference_id)
    for series_id, revision_id in (
        (protein_series_id, protein_revision_id),
        (sequence_series_id, sequence_revision_id),
    ):
        persistence.link(session, project_id, series_id)
        persistence.link(session, project_id, revision_id)
        # A newly created global scientific object is stewarded by the importing
        # Project — never by the user or the provider.
        persistence.steward(session, project_id, series_id)
    session.flush()

    protein_grant = can_mutate(
        session, actor_id, project_id, protein_series_id, purpose="import protein"
    )
    sequence_grant = can_mutate(
        session, actor_id, project_id, sequence_series_id, purpose="import sequence"
    )
    # ONE ExternalIdentity, one semantic target per qualifier: `identity` -> Protein,
    # `sequence` -> Sequence. Never two ExternalIdentity rows for one accession.
    scientific_object.attach_external_identity(
        session,
        protein_grant,
        protein_series_id,
        authority,
        native_id,
        qualifier=IDENTITY_QUALIFIER,
        kind=PROTEIN_IDENTITY_KIND,
        is_canonical=True,
    )
    scientific_object.attach_external_identity(
        session,
        sequence_grant,
        sequence_series_id,
        authority,
        native_id,
        qualifier=SEQUENCE_QUALIFIER,
        kind=PROTEIN_IDENTITY_KIND,
        is_canonical=True,
    )
    # The canonical conceptual relation, in its frozen direction.
    services.add_represents(
        session, actor_id, project_id, sequence_series_id, protein_series_id, commit=False
    )
    services.record_imported_as(
        session, actor_id, project_id, protein_revision_id, reference.external_reference_id,
        commit=False,
    )
    services.record_imported_as(
        session, actor_id, project_id, sequence_revision_id, reference.external_reference_id,
        commit=False,
    )
    return _ImportBundle(
        external_identity_id=identity.external_identity_id,
        external_reference_id=reference.external_reference_id,
        protein_series_id=protein_series_id,
        protein_revision_id=protein_revision_id,
        sequence_series_id=sequence_series_id,
        sequence_revision_id=sequence_revision_id,
        protein_name=snapshot.protein_series_name,
        snapshot_checksum=snapshot.checksum,
    )


def _load_bundle(session: Session, identity: ExternalIdentity) -> _ImportBundle:
    """Read the existing import bundle and fail closed unless it is COMPLETE.

    An identity that is not a complete, internally consistent Phase-14 bundle (a
    manual `identity` mapping, a half-attached pair, a wrong object type, a missing
    `represents`/`imported_as` edge, or an ambiguous source snapshot) is NEVER
    repaired or augmented here.
    """
    identity_id = identity.external_identity_id
    protein_mapping = session.get(
        ScientificObjectExternalIdentity, (identity_id, IDENTITY_QUALIFIER)
    )
    sequence_mapping = session.get(
        ScientificObjectExternalIdentity, (identity_id, SEQUENCE_QUALIFIER)
    )
    if protein_mapping is None or sequence_mapping is None:
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    if protein_mapping.series_id == sequence_mapping.series_id:
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    protein_series = session.get(ScientificObjectSeries, protein_mapping.series_id)
    sequence_series = session.get(ScientificObjectSeries, sequence_mapping.series_id)
    if protein_series is None or protein_series.object_type != PROTEIN_OBJECT_TYPE:
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    if sequence_series is None or sequence_series.object_type != SEQUENCE_OBJECT_TYPE:
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    references = provenance.find_external_references(session, identity_id)
    if len(references) != 1:
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    reference = references[0]
    stored_checksum = reference.checksum
    if stored_checksum is None:
        # No comparable snapshot provenance => not a Phase-14 bundle.
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    protein_revision_id = _imported_revision_id(
        session, reference.external_reference_id, protein_series.series_id
    )
    sequence_revision_id = _imported_revision_id(
        session, reference.external_reference_id, sequence_series.series_id
    )
    if protein_revision_id is None or sequence_revision_id is None:
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    if not _represents_edge_exists(session, sequence_series.series_id, protein_series.series_id):
        raise ConflictError(INCOMPATIBLE_BUNDLE_MESSAGE)
    return _ImportBundle(
        external_identity_id=identity_id,
        external_reference_id=reference.external_reference_id,
        protein_series_id=protein_series.series_id,
        protein_revision_id=protein_revision_id,
        sequence_series_id=sequence_series.series_id,
        sequence_revision_id=sequence_revision_id,
        # The STORED series name, never the current provider name: a re-import must
        # not imply that presentation metadata was refreshed.
        protein_name=protein_series.name,
        snapshot_checksum=stored_checksum,
    )


def _imported_revision_id(
    session: Session, external_reference_id: UUID, series_id: UUID
) -> UUID | None:
    """The UNIQUE revision of `series_id` that this reference was imported as."""
    revision_ids = list(
        session.scalars(
            select(ScientificObjectRevision.revision_id)
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
    return revision_ids[0] if len(revision_ids) == 1 else None


def _represents_edge_exists(session: Session, source_series_id: UUID, target_series_id: UUID) -> bool:
    return (
        session.scalar(
            select(GlobalProvenanceEdge.edge_id).where(
                GlobalProvenanceEdge.relation_type == RelationType.REPRESENTS.value,
                GlobalProvenanceEdge.source_id == source_series_id,
                GlobalProvenanceEdge.target_id == target_series_id,
            )
        )
        is not None
    )


def _assert_snapshot_matches(bundle: _ImportBundle, current_checksum: str) -> None:
    """Fail closed when the CURRENT provider snapshot differs from the imported one.

    Phase 14 implements NO refresh: it must never silently mutate a revision, append
    a revision, relabel an existing object, replace a sequence, or link stale data
    as current.
    """
    if bundle.snapshot_checksum != current_checksum:
        raise ConflictError(SNAPSHOT_CHANGED_MESSAGE)


def _link_bundle(session: Session, project_id: UUID, bundle: _ImportBundle) -> None:
    """Link an EXISTING global bundle into a Project (idempotent; no stewardship).

    Cross-Project import reuses the SAME global series/revisions/reference and gains
    only its own read lens: `ProjectResourceLink` grants visibility, and
    `ResourceStewardship` alone authorizes mutation, so the second Project never
    steals stewardship from the first.
    """
    for resource_id in (
        bundle.protein_series_id,
        bundle.protein_revision_id,
        bundle.sequence_series_id,
        bundle.sequence_revision_id,
        bundle.external_reference_id,
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
    """Any integrity failure the concurrent-import recovery path may resolve."""
    return (
        _is_external_identity_conflict(exc)
        or _is_mapping_conflict(exc)
        or _is_link_conflict(exc)
    )


def _constraint_name(exc: IntegrityError) -> str | None:
    diagnostics = getattr(exc.orig, "diag", None)
    return str(diagnostics.constraint_name) if diagnostics is not None else None


def _cache_metadata(provider_key: str, record: ResolvedProteinRecord) -> dict[str, str]:
    """Bounded, normalized resolver/cache metadata (never a provider payload copy).

    Only fields with a concrete provenance/debugging use are stored: the RESOLVER
    provider key (never the durable authority) and the upstream release identity.
    The upstream release is bounded inert text taken from the response headers, so no
    raw provider JSON, annotation, feature table, or HTTP header set is retained.
    """
    metadata = {
        "resolver_provider": bounded_inert_text(provider_key, MAX_PROTEIN_AUTHORITY_CHARS)
        or provider_key[:MAX_PROTEIN_AUTHORITY_CHARS]
    }
    release = bounded_inert_text(record.source_release, MAX_PROTEIN_SOURCE_RELEASE_CHARS)
    if release is not None:
        metadata["source_release"] = release
    release_date = bounded_inert_text(
        record.source_release_date, MAX_PROTEIN_SOURCE_RELEASE_CHARS
    )
    if release_date is not None:
        metadata["source_release_date"] = release_date
    return metadata


# ---------------------------------------------------------------------------
# Bounds / projection
# ---------------------------------------------------------------------------


def _bounded_query(query: str) -> str:
    if not isinstance(query, str):
        raise ValidationError("protein search query must be text")
    text = query.strip()
    if not text or len(text) > MAX_PROTEIN_QUERY_CHARS:
        raise ValidationError(
            f"protein search query must be 1..{MAX_PROTEIN_QUERY_CHARS} characters"
        )
    return text


def _bounded_limit(limit: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValidationError("protein result limit must be a positive integer")
    if limit > MAX_PROTEIN_RESULT_LIMIT:
        raise ValidationError(
            f"protein result limit must not exceed {MAX_PROTEIN_RESULT_LIMIT}"
        )
    return limit


def _bounded_identity(value: str, limit: int, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"protein {field} must be text")
    text = value.strip()
    if not text or len(text) > limit:
        raise ValidationError(f"protein {field} must be 1..{limit} characters")
    return text


def _candidate_read(candidate: ProteinCandidate) -> ProteinCandidateRead:
    """Re-bound a driver-returned candidate into the typed wire projection.

    The driver already applies these limits; re-applying them here is
    defense-in-depth so a mis-wired driver cannot widen the frontend/Agent surface.
    Truncation is honest bounded presentation data, and the canonical sequence is
    structurally absent from a candidate. Text stays UNTRUSTED inert data.
    """
    organism_id = candidate.organism_id
    if not isinstance(organism_id, int) or isinstance(organism_id, bool) or organism_id < 1:
        organism_id = None
    sequence_length = candidate.sequence_length
    if (
        not isinstance(sequence_length, int)
        or isinstance(sequence_length, bool)
        or sequence_length < 1
    ):
        sequence_length = None
    reviewed = candidate.reviewed if isinstance(candidate.reviewed, bool) else None
    return ProteinCandidateRead(
        provider_key=candidate.provider_key,
        authority=bounded_inert_text(candidate.authority, MAX_PROTEIN_AUTHORITY_CHARS) or "",
        native_id=bounded_inert_text(candidate.native_id, MAX_PROTEIN_NATIVE_ID_CHARS) or "",
        protein_name=bounded_inert_text(candidate.protein_name, MAX_PROTEIN_NAME_CHARS),
        gene_name=bounded_inert_text(candidate.gene_name, MAX_PROTEIN_GENE_NAME_CHARS),
        organism_name=bounded_inert_text(
            candidate.organism_name, MAX_PROTEIN_ORGANISM_NAME_CHARS
        ),
        organism_id=organism_id,
        sequence_length=sequence_length,
        reviewed=reviewed,
    )


__all__ = [
    "INCOMPATIBLE_BUNDLE_MESSAGE",
    "SNAPSHOT_CHANGED_MESSAGE",
    "discover_proteins",
    "import_protein",
    "protein_snapshot_checksum",
]
