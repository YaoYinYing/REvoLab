"""Phase-15 external structure discovery + explicit import tests.

The provider is the in-process `FakeStructureDriver`, which realizes the SAME
`StructureDiscoveryCapability` boundary and enters through the SAME Driver/Capability
registry as the real RCSB driver. No live network access, no test-only search
bypass.
"""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

import pytest
from sqlalchemy import func, select

from revolab import queries, services
from revolab import search as search_service
from revolab import structures as structure_service
from revolab.agent.builder import build_context
from revolab.capabilities import (
    MAX_STRUCTURE_METHODS,
    MAX_STRUCTURE_RESULT_LIMIT,
    CapabilityError,
    ResolvedStructureRecord,
    StructureCandidate,
    StructureSearchResult,
)
from revolab.content_store import ContentStore
from revolab.credentials import CredentialLease
from revolab.domain.errors import AuthorizationError, ConflictError, ValidationError
from revolab.drivers import DriverContext, DriverRegistry
from revolab.enums import (
    CapabilityErrorKind,
    CapabilityKind,
    ObjectType,
    ProviderRuntimeHealth,
    RelationType,
    ResourceKind,
    Role,
    SearchScope,
    SearchTargetKind,
)
from revolab.models import (
    ArtifactReference,
    Decision,
    Evidence,
    ExternalIdentity,
    ExternalReference,
    GlobalProvenanceEdge,
    ProjectResourceLink,
    ResourceStewardship,
    ScientificObjectExternalIdentity,
    ScientificObjectRevision,
    ScientificObjectSeries,
)
from revolab.schemas import ContextSelectionCreate, StructureImportCreate
from revolab.secret_store import InMemorySecretStore
from revolab.testing.fake_structure import (
    FAKE_STRUCTURE_AUTHORITY,
    FAKE_STRUCTURE_PROVIDER_KEY,
    HOSTILE_QUERY_TOKEN,
    MUTATE_QUERY_TOKEN,
    OVERSIZED_QUERY_TOKEN,
    FakeStructureDriver,
    deterministic_coordinates,
)

DEFAULT_QUERY = "human hemoglobin"
REVOLAB_AUTHORITY = "revolab"


def _actor(session):
    return services.create_actor(session)


def _project(session, actor, name="Structure P"):
    return services.create_project(session, actor, name)


def _registry(*drivers) -> DriverRegistry:
    registry = DriverRegistry()
    for driver in drivers:
        registry.register(driver)
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return registry


def _structure_registry(*, healthy: bool = True) -> DriverRegistry:
    return _registry(FakeStructureDriver(healthy=healthy))


def _discover(session, registry, actor, project, query=DEFAULT_QUERY, limit=5):
    return structure_service.discover_structures(
        session,
        registry,
        InMemorySecretStore(),
        actor,
        project.id,
        provider_key=FAKE_STRUCTURE_PROVIDER_KEY,
        query=query,
        limit=limit,
    )


def _import(session, registry, actor, project, authority, native_id, store):
    return structure_service.import_structure(
        session,
        registry,
        InMemorySecretStore(),
        store,
        actor,
        project.id,
        provider_key=FAKE_STRUCTURE_PROVIDER_KEY,
        authority=authority,
        native_id=native_id,
    )


def _import_first(session, registry, actor, project, store, query=DEFAULT_QUERY):
    discovered = _discover(session, registry, actor, project, query=query)
    candidate = discovered.candidates[0]
    result = _import(
        session, registry, actor, project, candidate.authority, candidate.native_id, store
    )
    return discovered, candidate, result


def _count(session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _durable_state(session) -> tuple[int, ...]:
    return (
        _count(session, ExternalIdentity),
        _count(session, ExternalReference),
        _count(session, ArtifactReference),
        _count(session, ScientificObjectSeries),
        _count(session, ScientificObjectRevision),
        _count(session, ScientificObjectExternalIdentity),
        _count(session, GlobalProvenanceEdge),
        _count(session, ProjectResourceLink),
        _count(session, ResourceStewardship),
    )


def _edges(session) -> list[GlobalProvenanceEdge]:
    return list(session.scalars(select(GlobalProvenanceEdge)))


def _edge_tuples(session) -> set[tuple[str, Any, Any]]:
    return {(edge.relation_type, edge.source_id, edge.target_id) for edge in _edges(session)}


def _record(
    native_id: str,
    *,
    size: int = 0,
    resolution: float | None = 1.5,
    methods: tuple[str, ...] = ("X-RAY DIFFRACTION",),
    coordinate_format: str = "mmcif",
) -> ResolvedStructureRecord:
    """One synthetic provider record for the local test doubles."""
    coordinates = deterministic_coordinates(native_id)
    if size:
        coordinates = coordinates + b"#" + b"x" * size
    return ResolvedStructureRecord(
        provider_key=FAKE_STRUCTURE_PROVIDER_KEY,
        authority=FAKE_STRUCTURE_AUTHORITY,
        native_id=native_id,
        coordinate_format=coordinate_format,
        coordinate_bytes=coordinates,
        title=f"Synthetic {native_id}",
        experimental_methods=methods,
        resolution_angstrom=resolution,
        entry_revision_major=1,
        entry_revision_minor=0,
        entry_revision_date="2026-01-01T00:00:00Z",
    )


class _StaticCapability:
    """A structure capability backed by an explicit record map (test double)."""

    kind = CapabilityKind.STRUCTURE_DISCOVERY

    def __init__(
        self,
        provider_key: str,
        records: dict[str, ResolvedStructureRecord],
        *,
        substitute: str | None = None,
        alias: dict[str, str] | None = None,
    ) -> None:
        self.provider_key = provider_key
        self._records = records
        self._substitute = substitute
        self._alias = alias or {}

    def search(self, query: str, limit: int, credentials: CredentialLease) -> StructureSearchResult:
        candidates = tuple(
            StructureCandidate(
                provider_key=self.provider_key,
                authority=record.authority,
                native_id=record.native_id,
                title=record.title,
                experimental_methods=record.experimental_methods,
                resolution_angstrom=record.resolution_angstrom,
            )
            for record in list(self._records.values())[:limit]
        )
        return StructureSearchResult(provider_key=self.provider_key, candidates=candidates)

    def resolve(
        self, authority: str, native_id: str, credentials: CredentialLease
    ) -> ResolvedStructureRecord:
        if self._substitute is not None:
            return self._records[self._substitute]
        lookup = self._alias.get(native_id.strip().casefold(), native_id)
        record = self._records.get(lookup)
        if record is None:
            raise CapabilityError(
                CapabilityErrorKind.NOT_FOUND,
                "not found",
                provider_key=self.provider_key,
                capability_kind=CapabilityKind.STRUCTURE_DISCOVERY,
            )
        return record


class _StaticDriver:
    """A zero-credential test driver projecting one `_StaticCapability`."""

    display_name = "Static structure provider"
    description = None
    required_credential_kinds: tuple[str, ...] = ()

    def __init__(
        self,
        records: dict[str, ResolvedStructureRecord],
        *,
        name: str = FAKE_STRUCTURE_PROVIDER_KEY,
        substitute: str | None = None,
        alias: dict[str, str] | None = None,
        authorities: tuple[str, ...] = (FAKE_STRUCTURE_AUTHORITY,),
        authority: str | None = None,
    ) -> None:
        self.name = name
        self.authorities = authorities
        target_authority = authority or authorities[0]
        # A driver's capability always attributes records to its OWN provider key and
        # durable authority, exactly as the real driver constructs them.
        owned = {
            key: dataclasses.replace(
                record, provider_key=name, authority=target_authority
            )
            for key, record in records.items()
        }
        self.capabilities = {
            CapabilityKind.STRUCTURE_DISCOVERY: _StaticCapability(
                name, owned, substitute=substitute, alias=alias
            )
        }

    def start(self, context: DriverContext) -> None:
        pass

    def stop(self) -> None:
        pass

    def probe_health(self) -> ProviderRuntimeHealth:
        return ProviderRuntimeHealth.READY


def _alias_driver(*, name: str = FAKE_STRUCTURE_PROVIDER_KEY) -> _StaticDriver:
    """A fake answering the canonical 4-character id from either official form."""
    return _StaticDriver(
        {"4HHB": _record("4HHB")},
        name=name,
        alias={"4hhb": "4HHB", "pdb_00004hhb": "4HHB", "4HHB": "4HHB"},
    )


@pytest.fixture
def store(tmp_path) -> ContentStore:
    return ContentStore(tmp_path / "content")


# ---------------------------------------------------------------------------
# Discovery is ephemeral, bounded and read-only
# ---------------------------------------------------------------------------


def test_discovery_returns_bounded_candidates(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    result = _discover(session, _structure_registry(), actor, project)
    assert result.provider_key == FAKE_STRUCTURE_PROVIDER_KEY
    assert result.query == DEFAULT_QUERY
    assert 1 <= len(result.candidates) <= 5
    candidate = result.candidates[0]
    assert candidate.authority == FAKE_STRUCTURE_AUTHORITY
    assert candidate.native_id
    assert candidate.title
    assert candidate.experimental_methods == ["X-RAY DIFFRACTION"]
    assert candidate.resolution_angstrom == 1.5
    assert candidate.polymer_entity_count == 1


def test_discovery_performs_zero_durable_writes(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    before = _durable_state(session)
    _discover(session, _structure_registry(), actor, project)
    assert _durable_state(session) == before
    assert _count(session, ExternalIdentity) == 0
    assert _count(session, ArtifactReference) == 0
    assert _count(session, ScientificObjectSeries) == 0


def test_a_candidate_is_never_a_project_search_hit(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    candidate = _discover(session, _structure_registry(), actor, project).candidates[0]
    hits = search_service.search(
        session, actor, project.id, query=candidate.native_id, scope=SearchScope.PROJECT_SHARED
    ).hits
    assert hits == []
    # A candidate carries no coordinate bytes and no metadata escape hatch.
    assert not hasattr(candidate, "coordinate_bytes")
    assert not hasattr(candidate, "metadata")
    assert set(candidate.model_dump()) == {
        "provider_key",
        "authority",
        "native_id",
        "title",
        "experimental_methods",
        "resolution_angstrom",
        "release_date",
        "polymer_entity_count",
    }


def test_a_non_member_cannot_discover(session, store):
    owner = _actor(session)
    project = _project(session, owner)
    outsider = _actor(session)
    with pytest.raises(AuthorizationError):
        _discover(session, _structure_registry(), outsider, project)


def test_a_viewer_may_discover_but_cannot_import(session, store):
    owner = _actor(session)
    viewer = _actor(session)
    project = _project(session, owner)
    services.add_membership(session, owner, project.id, viewer, Role.VIEWER)
    registry = _structure_registry()
    candidate = _discover(session, registry, viewer, project).candidates[0]
    with pytest.raises(AuthorizationError):
        _import(session, registry, viewer, project, candidate.authority, candidate.native_id, store)
    assert _count(session, ScientificObjectSeries) == 0


def test_provider_outage_is_a_typed_non_destructive_failure(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    before = _durable_state(session)
    with pytest.raises(CapabilityError):
        _discover(session, _structure_registry(healthy=False), actor, project)
    assert _durable_state(session) == before


def test_hostile_provider_text_stays_inert_data(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _structure_registry()
    candidate = _discover(
        session, registry, actor, project, query=f"{HOSTILE_QUERY_TOKEN} thing"
    ).candidates[0]
    assert candidate.title is not None
    assert "<script>" in candidate.title
    assert "\x1b" not in candidate.title
    result = _import(
        session,
        registry,
        actor,
        project,
        candidate.authority,
        candidate.native_id,
        store,
    )
    series = session.get(ScientificObjectSeries, result.structure_series_id)
    assert series is not None
    assert "<script>" in series.name
    assert _count(session, Evidence) == 0


def test_an_oversized_structure_surfaces_a_typed_failure(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    before = _durable_state(session)
    with pytest.raises(CapabilityError) as exc:
        _import(
            session,
            _structure_registry(),
            actor,
            project,
            FAKE_STRUCTURE_AUTHORITY,
            f"FAKE{OVERSIZED_QUERY_TOKEN}1",
            store,
        )
    assert exc.value.kind is CapabilityErrorKind.PROVIDER_UNAVAILABLE
    assert _durable_state(session) == before


def test_an_unknown_identity_fails_closed_before_any_write(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    before = _durable_state(session)
    with pytest.raises(CapabilityError):
        _import(
            session,
            _structure_registry(),
            actor,
            project,
            FAKE_STRUCTURE_AUTHORITY,
            "FAKENEVERSEEN",
            store,
        )
    assert _durable_state(session) == before


# ---------------------------------------------------------------------------
# The first import creates exactly the canonical bundle
# ---------------------------------------------------------------------------


def test_first_import_creates_exactly_the_canonical_bundle(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    _, _, result = _import_first(session, _structure_registry(), actor, project, store)

    identity = session.scalar(
        select(ExternalIdentity).where(ExternalIdentity.native_id == result.native_id)
    )
    assert identity is not None
    assert identity.authority == FAKE_STRUCTURE_AUTHORITY
    assert identity.kind == "structure"
    assert _count(session, ExternalIdentity) == 1
    assert _count(session, ExternalReference) == 1
    assert _count(session, ArtifactReference) == 1
    assert _count(session, ScientificObjectSeries) == 1
    assert _count(session, ScientificObjectRevision) == 1
    assert _count(session, ScientificObjectExternalIdentity) == 1

    mapping = session.get(
        ScientificObjectExternalIdentity, (identity.external_identity_id, "identity")
    )
    assert mapping is not None
    assert mapping.is_canonical is True
    assert mapping.series_id == result.structure_series_id

    revision = session.get(ScientificObjectRevision, result.structure_revision_id)
    assert revision is not None
    assert revision.series_id == result.structure_series_id
    assert revision.revision_seq == 1

    reference = session.get(ExternalReference, result.external_reference_id)
    assert reference is not None
    assert reference.external_identity_id == identity.external_identity_id
    assert reference.checksum is not None

    artifact = session.get(ArtifactReference, result.coordinate_artifact_id)
    assert artifact is not None
    assert artifact.authority == REVOLAB_AUTHORITY
    # Byte custody is NOT scientific origin: the artifact authority differs from
    # the scientific identity authority.
    assert artifact.authority != result.authority
    assert artifact.checksum == artifact.native_id

    imported = [
        edge for edge in _edges(session) if edge.relation_type == RelationType.IMPORTED_AS.value
    ]
    assert len(imported) == 2
    assert {edge.source_id for edge in imported} == {
        result.external_reference_id,
        result.coordinate_artifact_id,
    }
    assert {edge.target_id for edge in imported} == {result.structure_revision_id}
    assert {edge.source_kind for edge in imported} == {
        ResourceKind.EXTERNAL_REFERENCE.value,
        ResourceKind.ARTIFACT_REFERENCE.value,
    }
    # No invented relation vocabulary.
    assert {edge.relation_type for edge in _edges(session)} == {RelationType.IMPORTED_AS.value}

    assert _count(session, Evidence) == 0
    assert _count(session, Decision) == 0
    assert session.get(ResourceStewardship, result.structure_series_id) is not None
    assert session.get(ResourceStewardship, identity.external_identity_id) is None


def test_structure_payload_does_not_duplicate_identity_or_coordinate_linkage(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    _, _, result = _import_first(session, _structure_registry(), actor, project, store)
    revision = session.get(ScientificObjectRevision, result.structure_revision_id)
    assert revision is not None
    payload = revision.payload
    assert payload["resolution"] == 1.5
    assert payload["method"] == "X-RAY DIFFRACTION"
    assert payload["pdb_id"] is None
    assert payload["coordinates_ref"] is None
    assert payload["ligand_ref"] is None


def test_import_creates_no_protein_sequence_complex_or_ligand_objects(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    _import_first(session, _structure_registry(), actor, project, store)
    assert set(session.scalars(select(ScientificObjectSeries.object_type))) == {
        ObjectType.STRUCTURE.value
    }


def test_coordinate_bytes_are_stored_complete_and_verifiable(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    _, _, result = _import_first(session, _structure_registry(), actor, project, store)
    artifact = session.get(ArtifactReference, result.coordinate_artifact_id)
    assert artifact is not None
    data = store.get(artifact.native_id)
    assert hashlib.sha256(data).hexdigest() == artifact.checksum
    assert len(data) == artifact.size
    assert artifact.content_type == "chemical/x-cif"
    assert data.startswith(b"data_")


def test_the_snapshot_checksum_describes_the_stored_revision_and_artifact(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    _, _, result = _import_first(session, _structure_registry(), actor, project, store)
    revision = session.get(ScientificObjectRevision, result.structure_revision_id)
    artifact = session.get(ArtifactReference, result.coordinate_artifact_id)
    reference = session.get(ExternalReference, result.external_reference_id)
    assert revision is not None and artifact is not None and reference is not None
    assert artifact.checksum is not None
    assert reference.checksum == structure_service.structure_snapshot_checksum(
        revision.payload, artifact.checksum
    )
    assert reference.cache_metadata is not None
    assert reference.cache_metadata["resolver_provider"] == FAKE_STRUCTURE_PROVIDER_KEY
    assert reference.cache_metadata["pdb_revision_major"] == 1


# ---------------------------------------------------------------------------
# Idempotency, cross-Project reuse, stewardship
# ---------------------------------------------------------------------------


def test_same_project_repeat_import_is_idempotent(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _structure_registry()
    _, candidate, first = _import_first(session, registry, actor, project, store)
    before = _durable_state(session)
    second = _import(
        session, registry, actor, project, candidate.authority, candidate.native_id, store
    )
    assert second == first
    assert _durable_state(session) == before


def test_cross_project_import_reuses_the_same_global_objects(session, store):
    owner_a = _actor(session)
    project_a = _project(session, owner_a, "Structure A")
    owner_b = _actor(session)
    project_b = _project(session, owner_b, "Structure B")
    registry = _structure_registry()
    _, candidate, first = _import_first(session, registry, owner_a, project_a, store)

    second = _import(
        session, registry, owner_b, project_b, candidate.authority, candidate.native_id, store
    )
    assert second.structure_series_id == first.structure_series_id
    assert second.structure_revision_id == first.structure_revision_id
    assert second.coordinate_artifact_id == first.coordinate_artifact_id
    assert second.external_reference_id == first.external_reference_id
    assert _count(session, ExternalIdentity) == 1
    assert _count(session, ExternalReference) == 1
    assert _count(session, ArtifactReference) == 1
    assert _count(session, ScientificObjectSeries) == 1
    assert _count(session, GlobalProvenanceEdge) == 2
    for project in (project_a, project_b):
        visible = set(
            session.scalars(
                select(ProjectResourceLink.resource_id).where(
                    ProjectResourceLink.project_id == project.id
                )
            )
        )
        assert {
            first.structure_series_id,
            first.structure_revision_id,
            first.coordinate_artifact_id,
            first.external_reference_id,
        } <= visible


def test_the_second_project_does_not_steal_stewardship(session, store):
    owner_a = _actor(session)
    project_a = _project(session, owner_a, "Steward A")
    owner_b = _actor(session)
    project_b = _project(session, owner_b, "Steward B")
    registry = _structure_registry()
    _, candidate, first = _import_first(session, registry, owner_a, project_a, store)
    _import(session, registry, owner_b, project_b, candidate.authority, candidate.native_id, store)

    for resource_id in (first.structure_series_id, first.coordinate_artifact_id):
        stewardship = session.get(ResourceStewardship, resource_id)
        assert stewardship is not None
        assert stewardship.steward_project_id == project_a.id
    assert queries.read_only(session, project_b.id, first.structure_series_id) is True
    with pytest.raises(AuthorizationError):
        services.can_mutate(
            session, owner_b, project_b.id, first.structure_series_id, purpose="mutate"
        )


# ---------------------------------------------------------------------------
# Changed snapshots fail closed
# ---------------------------------------------------------------------------


def test_changed_coordinates_fail_closed_and_mutate_nothing(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _structure_registry()
    _, candidate, first = _import_first(session, registry, actor, project, store)
    before = _durable_state(session)
    before_edges = _edge_tuples(session)
    revision_before = session.get(ScientificObjectRevision, first.structure_revision_id)
    assert revision_before is not None
    payload_before = dict(revision_before.payload)

    # The provider now resolves the SAME identity with DIFFERENT coordinate bytes.
    _discover(session, registry, actor, project, query=f"{DEFAULT_QUERY} {MUTATE_QUERY_TOKEN}")
    with pytest.raises(ConflictError, match="changed since the imported snapshot"):
        _import(
            session, registry, actor, project, candidate.authority, candidate.native_id, store
        )

    assert _durable_state(session) == before
    assert _edge_tuples(session) == before_edges
    revision_after = session.get(ScientificObjectRevision, first.structure_revision_id)
    assert revision_after is not None and revision_after.payload == payload_before


def test_changed_normalized_metadata_fails_closed(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _structure_registry()
    _, candidate, first = _import_first(session, registry, actor, project, store)
    before = _durable_state(session)

    driver = registry.get(FAKE_STRUCTURE_PROVIDER_KEY).driver
    original = driver.state.resolve(candidate.native_id)
    assert original is not None
    driver.state.seed(dataclasses.replace(original, resolution_angstrom=3.5))
    with pytest.raises(ConflictError, match="changed since the imported snapshot"):
        _import(
            session, registry, actor, project, candidate.authority, candidate.native_id, store
        )
    assert _durable_state(session) == before
    revision = session.get(ScientificObjectRevision, first.structure_revision_id)
    assert revision is not None and revision.payload["resolution"] == 1.5


def test_title_only_drift_is_idempotent_and_never_rewrites_the_stored_name(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _structure_registry()
    _, candidate, first = _import_first(session, registry, actor, project, store)
    series_before = session.get(ScientificObjectSeries, first.structure_series_id)
    assert series_before is not None
    stored_name = series_before.name

    driver = registry.get(FAKE_STRUCTURE_PROVIDER_KEY).driver
    original = driver.state.resolve(candidate.native_id)
    assert original is not None
    driver.state.seed(dataclasses.replace(original, title="A completely different upstream title"))
    before = _durable_state(session)
    second = _import(
        session, registry, actor, project, candidate.authority, candidate.native_id, store
    )
    assert second == first
    assert _durable_state(session) == before
    series_after = session.get(ScientificObjectSeries, first.structure_series_id)
    assert series_after is not None and series_after.name == stored_name


def test_a_substituted_provider_identity_fails_closed(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    before = _durable_state(session)
    registry = _registry(_StaticDriver({"1ABC": _record("1ABC"), "2XYZ": _record("2XYZ")}, substitute="2XYZ"))
    with pytest.raises((CapabilityError, ValidationError)):
        _import(session, registry, actor, project, FAKE_STRUCTURE_AUTHORITY, "1ABC", store)
    assert _durable_state(session) == before


def test_an_extended_and_a_legacy_identifier_converge_on_one_identity(session, store):
    """The documented wwPDB alias maps two spellings onto ONE durable identity."""
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(_alias_driver())
    first = _import(
        session, registry, actor, project, FAKE_STRUCTURE_AUTHORITY, "4hhb", store
    )
    second = _import(
        session, registry, actor, project, FAKE_STRUCTURE_AUTHORITY, "pdb_00004hhb", store
    )
    assert second == first
    assert first.native_id == "4HHB"
    assert _count(session, ExternalIdentity) == 1
    assert _count(session, ArtifactReference) == 1


# ---------------------------------------------------------------------------
# Incomplete / corrupt existing bundles fail closed
# ---------------------------------------------------------------------------


def _bundle_fixture(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _structure_registry()
    _, candidate, result = _import_first(session, registry, actor, project, store)
    return actor, project, registry, candidate, result


@pytest.mark.parametrize(
    "mutate",
    [
        "missing_artifact_provenance",
        "wrong_object_type",
        "disagreeing_kind",
        "non_canonical_mapping",
        "retired_series",
        "second_snapshot",
        "disagreeing_digest",
        "provider_authority_artifact",
    ],
)
def test_an_incomplete_or_corrupt_bundle_fails_closed(session, store, mutate):
    actor, project, registry, candidate, result = _bundle_fixture(session, store)
    identity = session.scalar(
        select(ExternalIdentity).where(ExternalIdentity.native_id == result.native_id)
    )
    assert identity is not None
    if mutate == "missing_artifact_provenance":
        edge = session.scalar(
            select(GlobalProvenanceEdge).where(
                GlobalProvenanceEdge.relation_type == RelationType.IMPORTED_AS.value,
                GlobalProvenanceEdge.source_kind == ResourceKind.ARTIFACT_REFERENCE.value,
            )
        )
        assert edge is not None
        session.delete(edge)
    elif mutate == "wrong_object_type":
        series = session.get(ScientificObjectSeries, result.structure_series_id)
        assert series is not None
        series.object_type = ObjectType.PROTEIN.value
    elif mutate == "disagreeing_kind":
        identity.kind = "protein"
    elif mutate == "non_canonical_mapping":
        mapping = session.scalar(select(ScientificObjectExternalIdentity))
        assert mapping is not None
        mapping.is_canonical = False
    elif mutate == "retired_series":
        series = session.get(ScientificObjectSeries, result.structure_series_id)
        assert series is not None
        series.archived_at = datetime.now(UTC)
    elif mutate == "second_snapshot":
        services.create_external_reference(
            session, actor, project.id, identity.authority, identity.native_id
        )
    elif mutate == "disagreeing_digest":
        reference = session.get(ExternalReference, result.external_reference_id)
        assert reference is not None
        reference.checksum = "0" * 64
    elif mutate == "provider_authority_artifact":
        artifact = session.get(ArtifactReference, result.coordinate_artifact_id)
        assert artifact is not None
        artifact.authority = result.authority
    session.commit()
    before = _durable_state(session)
    with pytest.raises(ConflictError, match="incomplete mapping"):
        _import(
            session, registry, actor, project, candidate.authority, candidate.native_id, store
        )
    assert _durable_state(session) == before


def test_the_service_rejects_a_substituted_identity_before_any_write(session, store):
    """The service's own alias-aware identity check is real defense-in-depth."""
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(
        _StaticDriver({"1ABC": _record("1ABC"), "2XYZ": _record("2XYZ")}, substitute="1ABC")
    )
    # The driver double answers the request for 2XYZ with the 1ABC record, and the
    # service refuses because the confirmed identity is a DIFFERENT archive entry.
    with pytest.raises(ValidationError, match="does not match the import request"):
        _import(session, registry, actor, project, FAKE_STRUCTURE_AUTHORITY, "2XYZ", store)
    assert _count(session, ScientificObjectSeries) == 0
    assert _count(session, ArtifactReference) == 0


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


def test_a_failure_after_staging_leaves_no_partial_durable_state(session, store, monkeypatch):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _structure_registry()
    candidate = _discover(session, registry, actor, project).candidates[0]
    before = _durable_state(session)

    def explode(*args: Any, **kwargs: Any):
        raise RuntimeError("injected failure after the bundle was staged")

    monkeypatch.setattr(services, "record_imported_as", explode)
    with pytest.raises(RuntimeError, match="injected failure"):
        _import(session, registry, actor, project, candidate.authority, candidate.native_id, store)
    session.rollback()
    assert _durable_state(session) == before
    assert _count(session, GlobalProvenanceEdge) == 0


# ---------------------------------------------------------------------------
# Search + Agent-context integration
# ---------------------------------------------------------------------------


def test_an_imported_structure_becomes_project_searchable(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    _, _, result = _import_first(session, _structure_registry(), actor, project, store)
    by_id = search_service.search(
        session, actor, project.id, query=result.native_id, scope=SearchScope.PROJECT_SHARED
    ).hits
    # The PDB id matches the Structure series AND the imported provenance
    # (ExternalReference / coordinate artifact), all through the EXISTING Phase-12
    # read projection — no search index or document table is added.
    assert result.structure_series_id in {hit.target_id for hit in by_id}
    series = session.get(ScientificObjectSeries, result.structure_series_id)
    assert series is not None
    by_name = search_service.search(
        session, actor, project.id, query=series.name, scope=SearchScope.PROJECT_SHARED
    ).hits
    assert result.structure_series_id in {hit.target_id for hit in by_name}
    assert {hit.target_kind for hit in by_id} <= {
        SearchTargetKind.SCIENTIFIC_OBJECT_SERIES,
        SearchTargetKind.EXTERNAL_REFERENCE,
        SearchTargetKind.ARTIFACT_REFERENCE,
    }


def test_import_survives_a_provider_outage(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    _, _, result = _import_first(session, _structure_registry(), actor, project, store)
    offline = _structure_registry(healthy=False)
    detail = queries.object_detail(session, project.id, result.structure_series_id)
    assert detail["series"]["object_type"] == ObjectType.STRUCTURE.value
    artifact = session.get(ArtifactReference, result.coordinate_artifact_id)
    assert artifact is not None
    assert store.get(artifact.native_id).startswith(b"data_")
    assert search_service.search(
        session, actor, project.id, query=result.native_id, scope=SearchScope.PROJECT_SHARED
    ).hits
    context = build_context(
        session,
        actor,
        project.id,
        offline,
        ContextSelectionCreate(series_ids=[result.structure_series_id]),
    )
    assert result.structure_series_id in {ref.series_id for ref in context.series}


def test_import_does_not_automatically_add_anything_to_agent_context(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    _, _, result = _import_first(session, _structure_registry(), actor, project, store)
    revision = session.get(ScientificObjectRevision, result.structure_revision_id)
    assert revision is not None
    # The pre-existing implicit Project context carries the bounded series skeleton
    # (exactly as for any Project object) but NEVER a revision payload: Import adds
    # no content, and coordinate bytes never enter a turn.
    implicit = build_context(
        session, actor, project.id, _structure_registry(), ContextSelectionCreate()
    )
    assert implicit.revisions == []
    serialized = implicit.model_dump_json()
    assert revision.payload["method"] not in serialized
    assert deterministic_coordinates(result.native_id)[:16].decode() not in serialized

    explicit = build_context(
        session,
        actor,
        project.id,
        _structure_registry(),
        ContextSelectionCreate(series_ids=[result.structure_series_id]),
    )
    assert result.structure_series_id in {ref.series_id for ref in explicit.series}
    assert result.structure_revision_id in {ref.revision_id for ref in explicit.revisions}


def test_coordinates_never_enter_project_context_as_bytes(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(_StaticDriver({"BIG1": _record("BIG1", size=200_000)}))
    result = _import(session, registry, actor, project, FAKE_STRUCTURE_AUTHORITY, "BIG1", store)
    context = build_context(
        session,
        actor,
        project.id,
        registry,
        ContextSelectionCreate(series_ids=[result.structure_series_id]),
    )
    serialized = context.model_dump_json()
    assert len(serialized) < 20_000
    artifact = session.get(ArtifactReference, result.coordinate_artifact_id)
    assert artifact is not None and artifact.size is not None
    assert artifact.size > 100_000
    assert "xxxxxxx" not in serialized


def test_a_resolver_that_is_not_the_authority_still_creates_the_pdb_identity(session, store):
    """`provider_key != authority` must hold: the resolver is not the identity."""
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(_alias_driver(name="pdbmirror"))
    result = structure_service.import_structure(
        session,
        registry,
        InMemorySecretStore(),
        store,
        actor,
        project.id,
        provider_key="pdbmirror",
        authority=FAKE_STRUCTURE_AUTHORITY,
        native_id="4HHB",
    )
    identity = session.scalar(
        select(ExternalIdentity).where(ExternalIdentity.native_id == result.native_id)
    )
    assert identity is not None
    assert identity.authority == FAKE_STRUCTURE_AUTHORITY
    assert identity.authority != "pdbmirror"
    reference = session.get(ExternalReference, result.external_reference_id)
    assert reference is not None and reference.cache_metadata is not None
    assert reference.cache_metadata["resolver_provider"] == "pdbmirror"


def test_registering_two_resolvers_for_one_authority_is_refused():
    registry = DriverRegistry()
    registry.register(_alias_driver())
    with pytest.raises(ValueError, match="already resolved by driver"):
        registry.register(_alias_driver(name="other"))


# ---------------------------------------------------------------------------
# Request contract
# ---------------------------------------------------------------------------


def test_the_import_request_carries_stable_identity_only():
    assert set(StructureImportCreate.model_fields) == {"provider_key", "authority", "native_id"}
    assert StructureImportCreate.model_config.get("extra") == "forbid"
    with pytest.raises(Exception):
        StructureImportCreate(
            provider_key=FAKE_STRUCTURE_PROVIDER_KEY,
            authority=FAKE_STRUCTURE_AUTHORITY,
            native_id="1ABC",
            resolution=1.5,  # type: ignore[call-arg]
        )


def test_bounded_inputs_are_rejected_before_any_provider_call(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    with pytest.raises(ValidationError):
        _discover(session, _structure_registry(), actor, project, query="   ")
    with pytest.raises(ValidationError):
        _discover(session, _structure_registry(), actor, project, query="x" * 301)
    with pytest.raises(ValidationError):
        _discover(
            session, _structure_registry(), actor, project, limit=MAX_STRUCTURE_RESULT_LIMIT + 1
        )
    with pytest.raises(ValidationError):
        _import(session, _structure_registry(), actor, project, FAKE_STRUCTURE_AUTHORITY, "", store)


def test_a_discovery_result_is_bounded_by_the_requested_limit(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    assert len(_discover(session, _structure_registry(), actor, project, limit=1).candidates) == 1


# ---------------------------------------------------------------------------
# Core Structure payload invariant + the shared artifact get-or-create
# ---------------------------------------------------------------------------


def _manual_structure_resolution(session, actor, project, payload):
    """Create one manual `structure` object and read back its initial resolution."""
    series_id = services.create_object(
        session, actor, project.id, "structure", "Manual", payload=payload
    )
    revision = session.scalar(
        select(ScientificObjectRevision).where(ScientificObjectRevision.series_id == series_id)
    )
    assert revision is not None
    return revision


def test_a_manual_structure_payload_resolution_is_validated(session, store):
    """The finite-positive-angstrom rule lives in the Core type registry.

    It is the CANONICAL invariant, not an import-only shadow rule: manual creation
    and revision appends go through the same validator.
    """
    actor = _actor(session)
    project = _project(session, actor)
    revision = _manual_structure_resolution(session, actor, project, {"resolution": 2.5})
    assert revision.payload["resolution"] == 2.5
    assert revision.payload["method"] is None
    assert revision.payload["pdb_id"] is None

    # An integer is coerced to the angstrom value; a null is the honest
    # "no resolution applies" case (NMR, integrative).
    assert (
        _manual_structure_resolution(session, actor, project, {"resolution": 4}).payload[
            "resolution"
        ]
        == 4.0
    )
    assert (
        _manual_structure_resolution(session, actor, project, {"resolution": None}).payload[
            "resolution"
        ]
        is None
    )

    for bad in (0, -1.0, float("inf"), float("-inf"), True, "nan"):
        with pytest.raises(ValidationError):
            _manual_structure_resolution(session, actor, project, {"resolution": bad})

    for bad in (0, -1.0, float("inf"), float("-inf"), True, "nan"):
        with pytest.raises(ValidationError):
            services.create_object(
                session, actor, project.id, "structure", "Bad", payload={"resolution": bad}
            )


def test_the_shared_artifact_get_or_create_reuses_one_row_without_orphans(session, store):
    """The shared internal-artifact path is a race-safe get-or-create.

    A second create of the SAME content-addressed identity must reuse the existing
    row, must not duplicate it, must not orphan a `GlobalResourceRegistry` row, and
    must not commit a caller's `commit=False` composition.
    """
    from revolab.models import GlobalResourceRegistry

    actor = _actor(session)
    project = _project(session, actor)
    payload = b"data_DUP\n#\n_entry.id DUP\n"
    first = services.create_internal_artifact(
        session, actor, project.id, store, payload, content_type="chemical/x-cif", commit=False
    )
    before_registry = _count(session, GlobalResourceRegistry)
    second = services.create_internal_artifact(
        session, actor, project.id, store, payload, content_type="chemical/x-cif", commit=False
    )
    assert second.artifact_id == first.artifact_id
    assert _count(session, ArtifactReference) == 1
    # A lost insert-if-absent must clean up its own registry row.
    assert _count(session, GlobalResourceRegistry) == before_registry
    # Nothing was committed on the caller's behalf.
    session.rollback()
    assert _count(session, ArtifactReference) == 0
    assert _count(session, GlobalResourceRegistry) == 0


def test_an_incompatible_existing_artifact_identity_fails_closed(session, store):
    """Same identity, contradictory immutable metadata: a conflict, never a merge."""
    actor = _actor(session)
    project = _project(session, actor)
    payload = b"data_CONFLICT\n#\n"
    services.create_internal_artifact(
        session, actor, project.id, store, payload, content_type="chemical/x-cif"
    )
    with pytest.raises(ConflictError):
        services._persist_artifact_reference_trusted(
            session,
            actor,
            project.id,
            "revolab",
            hashlib.sha256(payload).hexdigest(),
            content_type="application/octet-stream",
            size=1,
            checksum=hashlib.sha256(payload).hexdigest(),
        )


# ---------------------------------------------------------------------------
# Review round 1 fixes
# ---------------------------------------------------------------------------


def test_a_lost_insert_if_absent_cleans_up_its_own_registry_row(session, store):
    """The lost-race cleanup branch is genuinely exercised (round-1 P1).

    `create_internal_artifact` short-circuits on its own read-then-insert, so the
    cleanup at the END of the atomic create path can only be reached by calling the
    domain operation directly. Removing the cleanup leaves an orphan registry row and
    fails this test.
    """
    from revolab.domain import provenance
    from revolab.models import GlobalResourceRegistry

    actor = _actor(session)
    project = _project(session, actor)
    data = b"data_LOST\n#\n_entry.id LOST\n"
    first = services.create_internal_artifact(
        session, actor, project.id, store, data, content_type="chemical/x-cif"
    )
    before_registry = _count(session, GlobalResourceRegistry)
    assert first.checksum is not None

    loser = provenance.create_artifact_reference_row_if_absent(
        session,
        "revolab",
        first.native_id,
        content_type="chemical/x-cif",
        size=first.size,
        checksum=first.checksum,
        version_id="",
    )
    assert loser is None
    assert _count(session, ArtifactReference) == 1
    assert _count(session, GlobalResourceRegistry) == before_registry


def _preexisting_coordinate_artifact(session, store, actor, project, native_id, content_type):
    """A byte-identical internal artifact, as if the bytes were stored earlier.

    This models the reachable case where the exact archive bytes already exist in
    ContentStore under a media type the generic artifact surface accepted (a
    browser-supplied or absent `content_type`).
    """
    return services.create_internal_artifact(
        session,
        actor,
        project.id,
        store,
        deterministic_coordinates(native_id),
        content_type=content_type,
    )


def test_an_import_reuses_a_preexisting_canonical_coordinate_artifact(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(_StaticDriver({"1ABC": _record("1ABC")}))
    pre = _preexisting_coordinate_artifact(
        session, store, actor, project, "1ABC", "chemical/x-cif"
    )
    first = _import(session, registry, actor, project, FAKE_STRUCTURE_AUTHORITY, "1ABC", store)
    assert first.coordinate_artifact_id == pre.artifact_id
    assert _count(session, ArtifactReference) == 1
    # Idempotent re-import of a bundle that reused a canonical artifact.
    second = _import(session, registry, actor, project, FAKE_STRUCTURE_AUTHORITY, "1ABC", store)
    assert second == first


@pytest.mark.parametrize("pre_type", ["text/plain", None])
def test_an_import_fails_closed_when_the_bytes_have_a_different_or_absent_media_type(
    session, store, pre_type
):
    """Create and reuse must agree on the canonical type (round-2 P0).

    A byte-identical artifact stored under a different or ABSENT media type must refuse
    the import BEFORE any durable write, instead of persisting a mislabeled Structure
    that the reuse validator could then never accept again.
    """
    from revolab.models import GlobalResourceRegistry

    actor = _actor(session)
    project = _project(session, actor)
    pre = _preexisting_coordinate_artifact(session, store, actor, project, "1ABC", pre_type)
    before = _durable_state(session)
    before_registry = _count(session, GlobalResourceRegistry)
    registry = _registry(_StaticDriver({"1ABC": _record("1ABC")}))

    with pytest.raises(ConflictError, match="different media type"):
        _import(session, registry, actor, project, FAKE_STRUCTURE_AUTHORITY, "1ABC", store)

    # No durable bundle, no orphan registry row, and NO identity was ever asserted.
    assert _durable_state(session) == before
    assert _count(session, GlobalResourceRegistry) == before_registry
    assert _count(session, ExternalIdentity) == 0
    stored = session.get(ArtifactReference, pre.artifact_id)
    assert stored is not None and stored.content_type == pre_type


def test_the_shared_artifact_compatibility_rule_is_not_relaxed(session, store):
    """The SHARED rule stays strict for content-addressed internal artifacts too.

    Phase 15 enforces its canonical media type at its own boundary rather than
    weakening the rule for every caller. `native_id == checksum` here, so this pins the
    content-addressed case explicitly.
    """
    actor = _actor(session)
    project = _project(session, actor)
    payload = b"data_STRICT\n#\n"
    first = services.create_internal_artifact(
        session, actor, project.id, store, payload, content_type="chemical/x-cif"
    )
    assert first.checksum is not None and first.native_id == first.checksum
    with pytest.raises(ConflictError):
        services._persist_artifact_reference_trusted(
            session,
            actor,
            project.id,
            services.INTERNAL_ARTIFACT_AUTHORITY,
            first.native_id,
            content_type="text/plain",
            size=first.size,
            checksum=first.checksum,
        )


def test_a_mis_wired_driver_cannot_persist_a_non_mmcif_coordinate_format(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    before = _durable_state(session)
    registry = _registry(_StaticDriver({"1ABC": _record("1ABC", coordinate_format="pdb")}))
    with pytest.raises(ValidationError, match="canonical PDBx/mmCIF"):
        _import(session, registry, actor, project, FAKE_STRUCTURE_AUTHORITY, "1ABC", store)
    assert _durable_state(session) == before


def test_a_mis_wired_driver_cannot_exceed_the_coordinate_ceiling(session, store, monkeypatch):
    actor = _actor(session)
    project = _project(session, actor)
    before = _durable_state(session)
    monkeypatch.setattr(structure_service, "MAX_STRUCTURE_COORDINATE_BYTES", 32)
    registry = _registry(_StaticDriver({"1ABC": _record("1ABC", size=64)}))
    with pytest.raises(ValidationError, match="exceed the supported size"):
        _import(session, registry, actor, project, FAKE_STRUCTURE_AUTHORITY, "1ABC", store)
    assert _durable_state(session) == before


def test_the_service_re_applies_deterministic_method_normalization(session, store):
    """A mis-wired driver cannot persist an unsorted or duplicated method string."""
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(
        _StaticDriver(
            {
                "1ABC": _record(
                    "1ABC",
                    methods=("X-RAY DIFFRACTION", "NEUTRON DIFFRACTION", "x-ray diffraction"),
                )
            }
        )
    )
    result = _import(session, registry, actor, project, FAKE_STRUCTURE_AUTHORITY, "1ABC", store)
    revision = session.get(ScientificObjectRevision, result.structure_revision_id)
    assert revision is not None
    assert revision.payload["method"] == "NEUTRON DIFFRACTION; X-RAY DIFFRACTION"


def test_the_service_fails_closed_on_an_absurd_method_list(session, store):
    actor = _actor(session)
    project = _project(session, actor)
    before = _durable_state(session)
    registry = _registry(
        _StaticDriver(
            {
                "1ABC": _record(
                    "1ABC",
                    methods=tuple(f"M{i}" for i in range(MAX_STRUCTURE_METHODS + 1)),
                )
            }
        )
    )
    with pytest.raises(ValidationError, match="too many experimental methods"):
        _import(session, registry, actor, project, FAKE_STRUCTURE_AUTHORITY, "1ABC", store)
    assert _durable_state(session) == before


def test_the_candidate_projection_re_applies_method_normalization(session, store):
    """The candidate projection is bounded, de-duplicated and sorted too."""
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(
        _StaticDriver(
            {
                "1ABC": _record(
                    "1ABC", methods=("z method", "A METHOD", "a method", "b method")
                )
            }
        )
    )
    candidate = structure_service.discover_structures(
        session, registry, InMemorySecretStore(), actor, project.id,
        provider_key=FAKE_STRUCTURE_PROVIDER_KEY, query="x", limit=1,
    ).candidates[0]
    assert candidate.experimental_methods == ["A METHOD", "b method", "z method"]


def test_an_alternate_resolver_for_the_pdb_authority_still_creates_the_pdb_identity(
    session, store,
):
    """A future wwPDB/PDBe/PDBj resolver resolves the same `pdb:<entry>` identity.

    The durable authority is `pdb`; the resolver key is recorded only as
    `cache_metadata.resolver_provider`.
    """
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(
        _StaticDriver(
            {"4HHB": _record("4HHB")},
            name="pdbmirror",
            authorities=("pdb",),
        )
    )
    result = structure_service.import_structure(
        session,
        registry,
        InMemorySecretStore(),
        store,
        actor,
        project.id,
        provider_key="pdbmirror",
        authority="pdb",
        native_id="4HHB",
    )
    identity = session.scalar(
        select(ExternalIdentity).where(ExternalIdentity.native_id == result.native_id)
    )
    assert identity is not None
    assert identity.authority == "pdb"
    assert identity.authority != "pdbmirror"
    reference = session.get(ExternalReference, result.external_reference_id)
    assert reference is not None and reference.cache_metadata is not None
    assert reference.cache_metadata["resolver_provider"] == "pdbmirror"


def test_the_real_pdb_authority_is_guarded_by_the_collision_check():
    """The real `pdb` namespace can never be silently claimed twice.

    The in-process fake claims its OWN `fakepdb` authority, so it coexists with a
    `pdb` resolver under distinct namespaces; a SECOND `pdb` claimant is refused.
    """
    registry = DriverRegistry()
    registry.register(FakeStructureDriver())
    registry.register(_StaticDriver({"4HHB": _record("4HHB")}, name="pdbmirror", authorities=("pdb",)))
    assert set(registry.names()) == {FAKE_STRUCTURE_PROVIDER_KEY, "pdbmirror"}
    with pytest.raises(ValueError, match="already resolved by driver"):
        registry.register(
            _StaticDriver({"1CRN": _record("1CRN")}, name="pdbother", authorities=("pdb",))
        )


def test_the_sqlite_rollback_leaves_no_partial_db_state(session, store, monkeypatch):
    """A staged-bundle failure leaves NO partial DB truth.

    The ContentStore is NOT transactional, so an unreachable content-addressed blob
    MAY survive the rollback. It is immutable, content-addressed, and not visible
    Project truth, and a later identical import safely reuses it — the test above
    proves no orphan registry ROW is left.
    """
    from revolab.models import GlobalResourceRegistry

    actor = _actor(session)
    project = _project(session, actor)
    registry = _structure_registry()
    candidate = _discover(session, registry, actor, project).candidates[0]
    before = _durable_state(session)
    before_registry = _count(session, GlobalResourceRegistry)

    def explode(*args: Any, **kwargs: Any):
        raise RuntimeError("injected failure after the bundle was staged")

    monkeypatch.setattr(services, "record_imported_as", explode)
    with pytest.raises(RuntimeError, match="injected failure"):
        _import(session, registry, actor, project, candidate.authority, candidate.native_id, store)
    session.rollback()
    assert _durable_state(session) == before
    assert _count(session, GlobalResourceRegistry) == before_registry
