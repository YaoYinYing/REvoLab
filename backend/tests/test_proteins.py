"""Phase-14 external protein discovery + explicit import tests.

The provider is the in-process `FakeProteinDriver`, which realizes the SAME
`ProteinDiscoveryCapability` boundary and enters through the SAME Driver/Capability
registry as the real UniProt driver. No live network access, no test-only search
bypass.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, ClassVar
from uuid import UUID

import pytest
from sqlalchemy import func, select

from revolab import proteins as protein_service
from revolab import search as search_service
from revolab import services
from revolab.agent.builder import build_context
from revolab.capabilities import (
    MAX_PROTEIN_RESULT_LIMIT,
    MAX_PROTEIN_SEQUENCE_CHARS,
    CapabilityError,
    ProteinCandidate,
    ProteinSearchResult,
    ResolvedProteinRecord,
)
from revolab.credentials import CredentialLease
from revolab.domain import persistence
from revolab.domain.errors import AuthorizationError, ConflictError, ValidationError
from revolab.domain.types_registry import validate_payload
from revolab.drivers import DriverContext, DriverRegistry
from revolab.enums import (
    CapabilityErrorKind,
    CapabilityKind,
    ObjectType,
    RelationType,
    ResourceKind,
    Role,
    SearchScope,
)
from revolab.models import (
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
from revolab.schemas import (
    ContextSelectionCreate,
    ProteinImportCreate,
    ProteinImportRead,
)
from revolab.secret_store import InMemorySecretStore
from revolab.testing.fake_protein import (
    FAKE_PROTEIN_AUTHORITY,
    FAKE_PROTEIN_PROVIDER_KEY,
    HOSTILE_QUERY_TOKEN,
    UNAVAILABLE_QUERY_TOKEN,
    FakeProteinDriver,
    deterministic_sequence,
)

DEFAULT_QUERY = "human kinase"


def _actor(session):
    return services.create_actor(session)


def _project(session, actor, name="Protein P"):
    return services.create_project(session, actor, name)


def _registry(*drivers) -> DriverRegistry:
    registry = DriverRegistry()
    for driver in drivers:
        registry.register(driver)
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return registry


def _protein_registry(*, healthy: bool = True) -> DriverRegistry:
    return _registry(FakeProteinDriver(healthy=healthy))


def _discover(session, registry, actor, project, query=DEFAULT_QUERY, limit=5):
    return protein_service.discover_proteins(
        session,
        registry,
        InMemorySecretStore(),
        actor,
        project.id,
        provider_key=FAKE_PROTEIN_PROVIDER_KEY,
        query=query,
        limit=limit,
    )


def _import(session, registry, actor, project, authority, native_id):
    return protein_service.import_protein(
        session,
        registry,
        InMemorySecretStore(),
        actor,
        project.id,
        provider_key=FAKE_PROTEIN_PROVIDER_KEY,
        authority=authority,
        native_id=native_id,
    )


def _import_first(session, registry, actor, project, query=DEFAULT_QUERY):
    discovered = _discover(session, registry, actor, project, query=query)
    candidate = discovered.candidates[0]
    result = _import(session, registry, actor, project, candidate.authority, candidate.native_id)
    return discovered, candidate, result


def _count(session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _durable_state(session) -> tuple[int, ...]:
    return (
        _count(session, ExternalIdentity),
        _count(session, ExternalReference),
        _count(session, ScientificObjectSeries),
        _count(session, ScientificObjectRevision),
        _count(session, ScientificObjectExternalIdentity),
        _count(session, GlobalProvenanceEdge),
        _count(session, ProjectResourceLink),
        _count(session, ResourceStewardship),
    )


# ---------------------------------------------------------------------------
# Discovery: read-only, bounded, provider-neutral, zero persistence
# ---------------------------------------------------------------------------


def test_discovery_projects_bounded_provider_neutral_candidates(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    result = _discover(session, registry, actor, project)

    assert result.provider_key == FAKE_PROTEIN_PROVIDER_KEY
    assert result.query == DEFAULT_QUERY
    assert result.candidates
    candidate = result.candidates[0]
    assert candidate.provider_key == FAKE_PROTEIN_PROVIDER_KEY
    # Provider key and durable authority stay CONCEPTUALLY separate.
    assert candidate.authority == FAKE_PROTEIN_AUTHORITY
    assert candidate.authority != candidate.provider_key
    assert candidate.native_id
    assert candidate.sequence_length is not None
    # A candidate is presentation data: it never carries the canonical sequence.
    assert "sequence" not in candidate.model_dump()
    assert set(candidate.model_dump()) == {
        "provider_key",
        "authority",
        "native_id",
        "protein_name",
        "gene_name",
        "organism_name",
        "organism_id",
        "sequence_length",
        "reviewed",
    }


def test_discovery_performs_zero_persistence(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    before = _durable_state(session)
    _discover(session, registry, actor, project)
    assert _durable_state(session) == before


def test_candidates_never_enter_project_search(session):
    """A `ProteinCandidate` is ephemeral; only an explicit Import creates truth."""
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    discovered = _discover(session, registry, actor, project)
    native_id = discovered.candidates[0].native_id

    hits = search_service.search(
        session, actor, project.id, query=native_id, scope=SearchScope.PROJECT_SHARED
    ).hits
    assert hits == []
    # No SearchHit is even representable for a candidate: search only returns
    # already-canonical REvoLab resources.
    assert all(hit.target_id for hit in hits)


def test_discovery_is_bounded_by_the_canonical_ceilings(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    assert len(_discover(session, registry, actor, project, limit=MAX_PROTEIN_RESULT_LIMIT).candidates) <= MAX_PROTEIN_RESULT_LIMIT
    with pytest.raises(ValidationError):
        _discover(session, registry, actor, project, limit=MAX_PROTEIN_RESULT_LIMIT + 1)
    with pytest.raises(ValidationError):
        _discover(session, registry, actor, project, query="   ")
    with pytest.raises(ValidationError):
        _discover(session, registry, actor, project, query="x" * 301)


def test_a_viewer_may_discover_but_a_stranger_may_not(session):
    owner = _actor(session)
    project = _project(session, owner)
    viewer = _actor(session)
    stranger = _actor(session)
    services.add_membership(session, owner, project.id, viewer, Role.VIEWER)
    registry = _protein_registry()

    assert _discover(session, registry, viewer, project).candidates
    with pytest.raises(AuthorizationError):
        _discover(session, registry, stranger, project)


def test_discovery_fails_closed_for_an_unknown_or_unavailable_provider(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    with pytest.raises(Exception):
        protein_service.discover_proteins(
            session,
            registry,
            InMemorySecretStore(),
            actor,
            project.id,
            provider_key="nosuchprovider",
            query=DEFAULT_QUERY,
        )

    before = _durable_state(session)
    with pytest.raises(CapabilityError):
        _discover(session, registry, actor, project, query=UNAVAILABLE_QUERY_TOKEN)
    assert _durable_state(session) == before


def test_discovery_fails_closed_when_a_driver_returns_a_foreign_candidate(session):
    """Defense-in-depth: a candidate attributed to another provider is impossible."""

    class _WrongProviderCapability:
        provider_key = "miswired"
        kind = CapabilityKind.PROTEIN_DISCOVERY

        def search(self, query, limit, credentials):
            return ProteinSearchResult(
                provider_key="miswired",
                candidates=(
                    ProteinCandidate(
                        provider_key="someoneelse",
                        authority="other",
                        native_id="X1",
                    ),
                ),
            )

        def resolve(self, authority, native_id, credentials):  # pragma: no cover
            raise AssertionError("resolve must not be reached")

    class _WrongProviderDriver:
        name = "miswired"
        display_name = "Miswired"
        description = None
        authorities = ("miswired",)
        required_credential_kinds: tuple[str, ...] = ()
        capabilities: ClassVar[dict] = {CapabilityKind.PROTEIN_DISCOVERY: _WrongProviderCapability()}

        def start(self, context):  # pragma: no cover - lifecycle
            pass

        def stop(self):  # pragma: no cover - lifecycle
            pass

        def probe_health(self):  # pragma: no cover - lifecycle
            from revolab.enums import ProviderRuntimeHealth

            return ProviderRuntimeHealth.READY

    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(_WrongProviderDriver())
    with pytest.raises(ValidationError, match="different provider"):
        protein_service.discover_proteins(
            session,
            registry,
            InMemorySecretStore(),
            actor,
            project.id,
            provider_key="miswired",
            query=DEFAULT_QUERY,
        )


def test_discovery_fails_closed_for_a_tombstoned_project(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    services.delete_project(session, actor, project.id)
    with pytest.raises(AuthorizationError):
        _discover(session, registry, actor, project)


# ---------------------------------------------------------------------------
# First import: exactly the canonical bundle
# ---------------------------------------------------------------------------


def test_first_import_creates_exactly_the_canonical_bundle(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    _, candidate, result = _import_first(session, registry, actor, project)

    assert isinstance(result, ProteinImportRead)
    assert result.authority == FAKE_PROTEIN_AUTHORITY
    assert result.native_id == candidate.native_id
    # The response echoes a bounded protein name and NEVER the canonical sequence.
    assert candidate.protein_name is not None
    assert result.protein_name == candidate.protein_name
    assert "sequence" not in result.model_dump()

    # Database truth, not just the API response.
    assert _count(session, ExternalIdentity) == 1
    assert _count(session, ExternalReference) == 1
    assert _count(session, ScientificObjectSeries) == 2
    assert _count(session, ScientificObjectRevision) == 2
    assert _count(session, ScientificObjectExternalIdentity) == 2
    assert _count(session, GlobalProvenanceEdge) == 3

    identity = session.scalars(select(ExternalIdentity)).one()
    assert (identity.authority, identity.native_id) == (FAKE_PROTEIN_AUTHORITY, candidate.native_id)
    assert identity.kind == "protein"

    protein_series = session.get(ScientificObjectSeries, result.protein_series_id)
    sequence_series = session.get(ScientificObjectSeries, result.sequence_series_id)
    assert protein_series is not None and protein_series.object_type == ObjectType.PROTEIN.value
    assert sequence_series is not None and sequence_series.object_type == ObjectType.SEQUENCE.value
    assert protein_series.name == result.protein_name
    assert sequence_series.name == f"{candidate.native_id} canonical sequence"

    # The Protein payload holds only the organism, and no sequence/ref duplication.
    protein_revision = session.get(ScientificObjectRevision, result.protein_revision_id)
    assert protein_revision is not None
    assert protein_revision.payload["organism"] == "Synthetic organism"
    assert protein_revision.payload["source_sequence_ref"] is None
    assert protein_revision.payload["chain"] is None

    # The EXACT canonical sequence is the Sequence revision content.
    sequence_revision = session.get(ScientificObjectRevision, result.sequence_revision_id)
    assert sequence_revision is not None
    assert sequence_revision.payload["kind"] == "protein"
    assert sequence_revision.payload["sequence"] == deterministic_sequence(
        candidate.native_id, candidate.sequence_length or 0
    )
    # The revision checksum is REvoLab's EXISTING canonical payload checksum.
    assert sequence_revision.checksum == persistence.payload_checksum(sequence_revision.payload)


def test_imported_sequence_represents_the_protein_in_the_correct_direction(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    _, _, result = _import_first(session, registry, actor, project)

    represents = session.scalars(
        select(GlobalProvenanceEdge).where(
            GlobalProvenanceEdge.relation_type == RelationType.REPRESENTS.value
        )
    ).all()
    assert len(represents) == 1
    edge = represents[0]
    assert edge.source_id == result.sequence_series_id
    assert edge.target_id == result.protein_series_id
    assert edge.source_kind == ResourceKind.SCIENTIFIC_OBJECT_SERIES.value
    assert edge.target_kind == ResourceKind.SCIENTIFIC_OBJECT_SERIES.value


def test_one_external_reference_is_imported_as_both_concrete_revisions(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    _, _, result = _import_first(session, registry, actor, project)

    imported = session.scalars(
        select(GlobalProvenanceEdge).where(
            GlobalProvenanceEdge.relation_type == RelationType.IMPORTED_AS.value
        )
    ).all()
    assert len(imported) == 2
    assert {edge.source_id for edge in imported} == {result.external_reference_id}
    assert {edge.source_kind for edge in imported} == {ResourceKind.EXTERNAL_REFERENCE.value}
    assert {edge.target_id for edge in imported} == {
        result.protein_revision_id,
        result.sequence_revision_id,
    }
    # The snapshot provenance records a deterministic digest of the normalized
    # scientific bundle (never raw HTTP bytes) plus bounded resolver metadata.
    reference = session.get(ExternalReference, result.external_reference_id)
    assert reference is not None
    assert reference.checksum is not None and len(reference.checksum) == 64
    assert reference.as_of is not None
    assert reference.cache_metadata == {
        "resolver_provider": FAKE_PROTEIN_PROVIDER_KEY,
        "source_release": "test_2026_01",
        "source_release_date": "01-January-2026",
    }
    protein_revision = session.get(ScientificObjectRevision, result.protein_revision_id)
    sequence_revision = session.get(ScientificObjectRevision, result.sequence_revision_id)
    assert protein_revision is not None and sequence_revision is not None
    assert reference.checksum == protein_service.protein_snapshot_checksum(
        protein_revision.payload, sequence_revision.payload
    )


def test_the_snapshot_checksum_is_independent_of_provider_response_formatting(session):
    payload = validate_payload(ObjectType.PROTEIN, {"organism": "Homo sapiens"})
    sequence = validate_payload(ObjectType.SEQUENCE, {"kind": "protein", "sequence": "MALW"})
    # Same content, different dict insertion order => the SAME scientific snapshot.
    reordered = {"sequence_payload": dict(sequence), "protein_payload": dict(payload)}
    assert protein_service.protein_snapshot_checksum(
        dict(reversed(list(payload.items()))), dict(sequence)
    ) == protein_service.protein_snapshot_checksum(payload, sequence)
    assert set(reordered) == {"protein_payload", "sequence_payload"}


def test_external_identity_maps_by_identity_and_sequence_qualifiers(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    _, _, result = _import_first(session, registry, actor, project)

    identity = session.scalars(select(ExternalIdentity)).one()
    mappings = session.scalars(
        select(ScientificObjectExternalIdentity).where(
            ScientificObjectExternalIdentity.external_identity_id == identity.external_identity_id
        )
    ).all()
    by_qualifier = {mapping.qualifier: mapping for mapping in mappings}
    assert set(by_qualifier) == {"identity", "sequence"}
    assert by_qualifier["identity"].series_id == result.protein_series_id
    assert by_qualifier["sequence"].series_id == result.sequence_series_id
    # Exactly ONE ExternalIdentity row for one accession.
    assert _count(session, ExternalIdentity) == 1
    assert all(mapping.is_canonical for mapping in mappings)


def test_import_creates_no_evidence_and_no_decision(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    _import_first(session, registry, actor, project)
    assert _count(session, Evidence) == 0
    assert _count(session, Decision) == 0
    # An external database record is a fact with provenance, never interpretation.
    # A human can still create Evidence explicitly through the existing operation.
    assert session.scalar(select(func.count()).select_from(Evidence)) == 0


def test_import_links_and_stewards_the_new_resources_for_the_importing_project(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    _, _, result = _import_first(session, registry, actor, project)

    linked = set(
        session.scalars(
            select(ProjectResourceLink.resource_id).where(
                ProjectResourceLink.project_id == project.id
            )
        )
    )
    assert {
        result.protein_series_id,
        result.protein_revision_id,
        result.sequence_series_id,
        result.sequence_revision_id,
        result.external_reference_id,
    } <= linked
    stewarded = {
        row.resource_id: row.steward_project_id
        for row in session.scalars(select(ResourceStewardship))
    }
    assert stewarded[result.protein_series_id] == project.id
    assert stewarded[result.sequence_series_id] == project.id
    assert stewarded[result.external_reference_id] == project.id
    # The ExternalIdentity is the global identity registry: never Project-owned.
    identity = session.scalars(select(ExternalIdentity)).one()
    assert identity.external_identity_id not in stewarded


# ---------------------------------------------------------------------------
# Idempotency, cross-Project reuse, stewardship
# ---------------------------------------------------------------------------


def test_same_project_repeat_import_is_idempotent(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    _, candidate, first = _import_first(session, registry, actor, project)
    before = _durable_state(session)

    second = _import(session, registry, actor, project, candidate.authority, candidate.native_id)
    assert second == first
    assert _durable_state(session) == before
    # No duplicate series/revision/mapping/represents/imported_as graph.
    assert _count(session, ScientificObjectSeries) == 2
    assert _count(session, ScientificObjectRevision) == 2
    assert _count(session, GlobalProvenanceEdge) == 3


def test_cross_project_import_reuses_the_same_global_objects(session):
    owner_a = _actor(session)
    project_a = _project(session, owner_a, "Protein A")
    owner_b = _actor(session)
    project_b = _project(session, owner_b, "Protein B")
    registry = _protein_registry()
    _, candidate, first = _import_first(session, registry, owner_a, project_a)

    second = _import(
        session, registry, owner_b, project_b, candidate.authority, candidate.native_id
    )
    assert second.protein_series_id == first.protein_series_id
    assert second.protein_revision_id == first.protein_revision_id
    assert second.sequence_series_id == first.sequence_series_id
    assert second.sequence_revision_id == first.sequence_revision_id
    assert second.external_reference_id == first.external_reference_id
    # No copied scientific objects, no duplicate identity.
    assert _count(session, ExternalIdentity) == 1
    assert _count(session, ExternalReference) == 1
    assert _count(session, ScientificObjectSeries) == 2
    assert _count(session, ScientificObjectRevision) == 2
    assert _count(session, GlobalProvenanceEdge) == 3
    # Two independent Project read lenses.
    for project in (project_a, project_b):
        visible = set(
            session.scalars(
                select(ProjectResourceLink.resource_id).where(
                    ProjectResourceLink.project_id == project.id
                )
            )
        )
        assert first.protein_series_id in visible
        assert first.sequence_series_id in visible


def test_the_second_project_does_not_steal_stewardship(session):
    owner_a = _actor(session)
    project_a = _project(session, owner_a, "Steward A")
    owner_b = _actor(session)
    project_b = _project(session, owner_b, "Steward B")
    registry = _protein_registry()
    _, candidate, first = _import_first(session, registry, owner_a, project_a)
    _import(session, registry, owner_b, project_b, candidate.authority, candidate.native_id)

    stewards = {
        row.resource_id: row.steward_project_id
        for row in session.scalars(select(ResourceStewardship))
    }
    assert stewards[first.protein_series_id] == project_a.id
    assert stewards[first.sequence_series_id] == project_a.id
    assert stewards[first.external_reference_id] == project_a.id
    # Project B gains only its read lens: it still cannot mutate the objects.
    with pytest.raises(AuthorizationError):
        services.update_series(session, owner_b, project_b.id, first.protein_series_id, name="hijack")


# ---------------------------------------------------------------------------
# Changed snapshot / incompatible bundle: fail closed
# ---------------------------------------------------------------------------


def test_a_changed_external_snapshot_fails_closed_without_mutating_anything(session):
    actor = _actor(session)
    project = _project(session, actor)
    driver = FakeProteinDriver()
    registry = _registry(driver)
    _, candidate, first = _import_first(session, registry, actor, project)
    before = _durable_state(session)
    original = driver.state.resolve(candidate.native_id)
    assert original is not None

    # The provider record CHANGES: same durable identity, different sequence.
    driver.state.seed(
        ResolvedProteinRecord(
            provider_key=FAKE_PROTEIN_PROVIDER_KEY,
            authority=FAKE_PROTEIN_AUTHORITY,
            native_id=candidate.native_id,
            canonical_sequence=deterministic_sequence(
                "MUTATED", len(original.canonical_sequence)
            ),
            protein_name=original.protein_name,
            organism_name=original.organism_name,
            sequence_length=original.sequence_length,
            reviewed=original.reviewed,
        )
    )
    with pytest.raises(ConflictError) as excinfo:
        _import(session, registry, actor, project, candidate.authority, candidate.native_id)
    message = str(excinfo.value)
    assert "changed since the imported snapshot" in message
    assert "not implemented in Phase 14" in message
    # No new revision, no mutated revision, no changed mapping/edge.
    assert _durable_state(session) == before
    assert first.protein_revision_id is not None
    revision = session.get(ScientificObjectRevision, first.sequence_revision_id)
    assert revision is not None
    assert revision.payload["sequence"] == original.canonical_sequence


@pytest.mark.parametrize("field", ["organism_name", "sequence"])
def test_any_normalized_scientific_change_is_a_conflict(session, field: str):
    actor = _actor(session)
    project = _project(session, actor)
    driver = FakeProteinDriver()
    registry = _registry(driver)
    _, candidate, _ = _import_first(session, registry, actor, project)
    before = _durable_state(session)
    original = driver.state.resolve(candidate.native_id)
    assert original is not None
    replacement = ResolvedProteinRecord(
        provider_key=original.provider_key,
        authority=original.authority,
        native_id=original.native_id,
        canonical_sequence=(
            deterministic_sequence("CHANGED", len(original.canonical_sequence))
            if field == "sequence"
            else original.canonical_sequence
        ),
        protein_name=original.protein_name,
        organism_name=(
            "Different organism" if field == "organism_name" else original.organism_name
        ),
        sequence_length=original.sequence_length,
        reviewed=original.reviewed,
    )
    driver.state.seed(replacement)
    with pytest.raises(ConflictError):
        _import(session, registry, actor, project, candidate.authority, candidate.native_id)
    assert _durable_state(session) == before


def test_a_protein_name_only_change_is_presentation_drift_not_a_conflict(session):
    """The series name is governance/presentation metadata, never identity.

    The snapshot digest covers the normalized scientific bundle
    (`{protein_payload, sequence_payload}`), so a renamed recommendation is NOT a
    scientific change — and the re-import must not rewrite the stored series name.
    """
    actor = _actor(session)
    project = _project(session, actor)
    driver = FakeProteinDriver()
    registry = _registry(driver)
    _, candidate, first = _import_first(session, registry, actor, project)
    original = driver.state.resolve(candidate.native_id)
    assert original is not None
    driver.state.seed(
        ResolvedProteinRecord(
            provider_key=original.provider_key,
            authority=original.authority,
            native_id=original.native_id,
            canonical_sequence=original.canonical_sequence,
            protein_name="A completely new recommended name",
            organism_name=original.organism_name,
            sequence_length=original.sequence_length,
            reviewed=original.reviewed,
        )
    )
    again = _import(session, registry, actor, project, candidate.authority, candidate.native_id)
    assert again.protein_series_id == first.protein_series_id
    assert again.protein_name == first.protein_name
    series = session.get(ScientificObjectSeries, first.protein_series_id)
    assert series is not None and series.name == first.protein_name


def test_the_deterministic_mutate_token_changes_content_but_not_identity(session):
    """The `mutate` affordance the browser conflict negative depends on.

    It must keep the durable identity stable while changing the RESOLVED scientific
    content, which is exactly the "external record changed since import" situation.
    """
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    driver = registry.get(FAKE_PROTEIN_PROVIDER_KEY).driver
    discovered = _discover(session, registry, actor, project, query="identity stable")
    candidate = discovered.candidates[0]
    original = driver.state.resolve(candidate.native_id)
    assert original is not None
    _import(session, registry, actor, project, candidate.authority, candidate.native_id)

    # The `mutate` search reports the SAME durable identities and the SAME
    # presentation fields; only the RESOLVED scientific content changes.
    mutated_view = _discover(
        session, registry, actor, project, query="mutate identity stable"
    )
    assert [c.native_id for c in mutated_view.candidates] == [
        c.native_id for c in discovered.candidates
    ]
    assert mutated_view.candidates[0].protein_name == candidate.protein_name
    after = driver.state.resolve(candidate.native_id)
    assert after is not None
    assert after.native_id == original.native_id
    assert after.canonical_sequence != original.canonical_sequence
    assert len(after.canonical_sequence) == original.sequence_length

    # The re-import therefore fails closed exactly like any changed snapshot.
    with pytest.raises(ConflictError, match="changed since the imported snapshot"):
        _import(session, registry, actor, project, candidate.authority, candidate.native_id)


def test_a_manual_identity_mapping_without_the_import_bundle_fails_closed(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()

    # A human manually creates a Protein and asserts (fakeuniprot, ACC) as its
    # identity — WITHOUT the Phase-14 import bundle.
    series_id = services.create_object(
        session, actor, project.id, "protein", "Manual protein", payload={"organism": "Homo sapiens"}
    )
    services.attach_external_identity(
        session, actor, project.id, series_id, FAKE_PROTEIN_AUTHORITY, "manual-acc",
        qualifier="identity",
    )
    before = _durable_state(session)

    candidate = ProteinCandidate(
        provider_key=FAKE_PROTEIN_PROVIDER_KEY,
        authority=FAKE_PROTEIN_AUTHORITY,
        native_id="manual-acc",
    )
    registry.get(FAKE_PROTEIN_PROVIDER_KEY).driver.state.seed(
        ResolvedProteinRecord(
            provider_key=FAKE_PROTEIN_PROVIDER_KEY,
            authority=FAKE_PROTEIN_AUTHORITY,
            native_id="manual-acc",
            canonical_sequence=deterministic_sequence("manual", 33),
            protein_name="Manual protein",
            organism_name="Homo sapiens",
            sequence_length=33,
        )
    )
    with pytest.raises(ConflictError):
        _import(session, registry, actor, project, candidate.authority, candidate.native_id)
    # No automatic augmentation: no imported Sequence, no new source edge.
    assert _durable_state(session) == before
    assert session.scalar(
        select(func.count()).select_from(ScientificObjectSeries).where(
            ScientificObjectSeries.object_type == ObjectType.SEQUENCE.value
        )
    ) == 0


def _corrupt_bundle(session, actor, project, registry) -> ProteinImportRead:
    _, _, result = _import_first(session, registry, actor, project)
    return result


def test_a_winner_committing_after_the_first_lookup_still_converges(session, monkeypatch):
    """Reviewer P1: the create path must LOSE on the unique constraint, not on a stale read.

    If the winning transaction commits between the first identity lookup and the create
    attempt, a read-then-insert create path would observe the winner's identity, skip the
    insert, build a SECOND bundle, and then fail with a misleading "already maps to
    another series" conflict. The insert-only create path makes the database the single
    linearization point, so the loser hits the unique constraint, rolls back, and
    converges on the winner.

    Mutation-sensitivity: restoring `get_or_create_external_identity` in `_create_bundle`
    makes this test fail with `ConflictError: external identity already maps to another
    series for this qualifier`.
    """
    from revolab.domain import scientific_object as scientific_object_module

    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    _, candidate, first = _import_first(session, registry, actor, project)
    before = _durable_state(session)

    real_find = scientific_object_module.find_external_identity
    calls = {"count": 0}

    def racing_find(s, authority, native_id):
        calls["count"] += 1
        if calls["count"] == 1:
            # The winner commits AFTER this lookup: we must not observe it.
            return None
        return real_find(s, authority, native_id)

    monkeypatch.setattr(scientific_object_module, "find_external_identity", racing_find)
    second = _import(session, registry, actor, project, candidate.authority, candidate.native_id)

    assert calls["count"] >= 2  # the recovery path really re-read the winner
    assert second.protein_series_id == first.protein_series_id
    assert second.sequence_series_id == first.sequence_series_id
    assert second.external_reference_id == first.external_reference_id
    # No second bundle was created.
    assert _durable_state(session) == before


def test_an_archived_bundle_series_fails_closed(session):
    """A retired global object is not a live import target."""
    from datetime import UTC, datetime

    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    result = _corrupt_bundle(session, actor, project, registry)
    series = session.get(ScientificObjectSeries, result.protein_series_id)
    assert series is not None
    series.archived_at = datetime.now(UTC)
    session.commit()
    before = _durable_state(session)
    with pytest.raises(ConflictError, match="incomplete"):
        _import(session, registry, actor, project, result.authority, result.native_id)
    assert _durable_state(session) == before


def test_a_disagreeing_identity_kind_fails_closed(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    result = _corrupt_bundle(session, actor, project, registry)
    identity = session.scalars(select(ExternalIdentity)).one()
    identity.kind = "nucleotide"
    session.commit()
    before = _durable_state(session)
    with pytest.raises(ConflictError, match="incomplete"):
        _import(session, registry, actor, project, result.authority, result.native_id)
    assert _durable_state(session) == before


def test_a_non_canonical_qualifier_mapping_fails_closed(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    result = _corrupt_bundle(session, actor, project, registry)
    identity = session.scalars(select(ExternalIdentity)).one()
    mapping = session.get(
        ScientificObjectExternalIdentity, (identity.external_identity_id, "sequence")
    )
    assert mapping is not None
    mapping.is_canonical = False
    session.commit()
    before = _durable_state(session)
    with pytest.raises(ConflictError, match="incomplete"):
        _import(session, registry, actor, project, result.authority, result.native_id)
    assert _durable_state(session) == before


def test_a_snapshot_digest_that_disagrees_with_its_revisions_fails_closed(session):
    """The stored digest must actually describe the stored revisions."""
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    result = _corrupt_bundle(session, actor, project, registry)
    reference = session.get(ExternalReference, result.external_reference_id)
    assert reference is not None
    reference.checksum = "0" * 64
    session.commit()
    before = _durable_state(session)
    with pytest.raises(ConflictError, match="incomplete"):
        _import(session, registry, actor, project, result.authority, result.native_id)
    assert _durable_state(session) == before


def test_a_missing_sequence_mapping_fails_closed(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    result = _corrupt_bundle(session, actor, project, registry)
    identity = session.scalars(select(ExternalIdentity)).one()
    mapping = session.get(
        ScientificObjectExternalIdentity, (identity.external_identity_id, "sequence")
    )
    assert mapping is not None
    session.delete(mapping)
    session.commit()
    before = _durable_state(session)
    with pytest.raises(ConflictError, match="incomplete"):
        _import(
            session, registry, actor, project, result.authority, result.native_id
        )
    assert _durable_state(session) == before


def test_a_wrong_object_type_in_the_bundle_fails_closed(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    result = _corrupt_bundle(session, actor, project, registry)
    identity = session.scalars(select(ExternalIdentity)).one()
    mapping = session.get(
        ScientificObjectExternalIdentity, (identity.external_identity_id, "sequence")
    )
    assert mapping is not None
    session.delete(mapping)
    session.commit()
    wrong = services.create_object(
        session, actor, project.id, "other", "Not a sequence", payload={"data": {}}
    )
    services.attach_external_identity(
        session, actor, project.id, wrong, result.authority, result.native_id, qualifier="sequence"
    )
    before = _durable_state(session)
    with pytest.raises(ConflictError, match="incomplete"):
        _import(session, registry, actor, project, result.authority, result.native_id)
    assert _durable_state(session) == before


def test_a_missing_represents_edge_fails_closed(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    result = _corrupt_bundle(session, actor, project, registry)
    edge = session.scalars(
        select(GlobalProvenanceEdge).where(
            GlobalProvenanceEdge.relation_type == RelationType.REPRESENTS.value
        )
    ).one()
    session.delete(edge)
    session.commit()
    before = _durable_state(session)
    with pytest.raises(ConflictError, match="incomplete"):
        _import(session, registry, actor, project, result.authority, result.native_id)
    assert _durable_state(session) == before


def test_a_missing_imported_as_edge_fails_closed(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    result = _corrupt_bundle(session, actor, project, registry)
    edge = session.scalars(
        select(GlobalProvenanceEdge).where(
            GlobalProvenanceEdge.relation_type == RelationType.IMPORTED_AS.value,
            GlobalProvenanceEdge.target_id == result.protein_revision_id,
        )
    ).one()
    session.delete(edge)
    session.commit()
    before = _durable_state(session)
    with pytest.raises(ConflictError, match="incomplete"):
        _import(session, registry, actor, project, result.authority, result.native_id)
    assert _durable_state(session) == before


def test_two_incompatible_source_snapshots_fail_closed(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    result = _corrupt_bundle(session, actor, project, registry)
    # A second resolver snapshot over the SAME identity is an ambiguous state.
    services.create_external_reference(
        session, actor, project.id, result.authority, result.native_id, checksum="0" * 64
    )
    before = _durable_state(session)
    with pytest.raises(ConflictError, match="incomplete"):
        _import(session, registry, actor, project, result.authority, result.native_id)
    assert _durable_state(session) == before


# ---------------------------------------------------------------------------
# Atomicity and provider-outage durability
# ---------------------------------------------------------------------------


def test_a_failure_after_staging_leaves_no_partial_durable_state(session, monkeypatch):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    discovered = _discover(session, registry, actor, project)
    candidate = discovered.candidates[0]
    before = _durable_state(session)

    from revolab import services as services_module

    def explode(*args: Any, **kwargs: Any):
        raise RuntimeError("injected failure after objects were staged")

    monkeypatch.setattr(services_module, "record_imported_as", explode)
    with pytest.raises(RuntimeError):
        _import(session, registry, actor, project, candidate.authority, candidate.native_id)
    # The caller (the request session) rolls back; the test mirrors that contract.
    session.rollback()
    assert _durable_state(session) == before


def test_provider_outage_after_import_does_not_damage_project_context(session):
    actor = _actor(session)
    project = _project(session, actor)
    driver = FakeProteinDriver()
    registry = _registry(driver)
    _, candidate, result = _import_first(session, registry, actor, project)

    # The provider disappears: live discovery/resolve becomes unavailable...
    driver._healthy = False
    with pytest.raises(CapabilityError):
        _discover(session, registry, actor, project, query=UNAVAILABLE_QUERY_TOKEN)
    unhealthy = _registry(FakeProteinDriver(healthy=False))
    with pytest.raises(CapabilityError):
        protein_service.discover_proteins(
            session,
            unhealthy,
            InMemorySecretStore(),
            actor,
            project.id,
            provider_key=FAKE_PROTEIN_PROVIDER_KEY,
            query=DEFAULT_QUERY,
        )

    # ...but every durable fact survives, and Project Search still succeeds.
    assert _count(session, ExternalIdentity) == 1
    assert _count(session, ExternalReference) == 1
    assert session.get(ScientificObjectSeries, result.protein_series_id) is not None
    assert session.get(ScientificObjectRevision, result.sequence_revision_id) is not None
    hits = search_service.search(
        session, actor, project.id, query=candidate.native_id, scope=SearchScope.PROJECT_SHARED
    ).hits
    assert hits
    context = build_context(
        session, actor, project.id, _registry(), ContextSelectionCreate(series_ids=[result.protein_series_id])
    )
    assert context is not None


# ---------------------------------------------------------------------------
# Project Search + Agent context integration
# ---------------------------------------------------------------------------


def test_imported_objects_become_project_searchable_by_accession_and_name(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    _, candidate, result = _import_first(session, registry, actor, project)

    by_accession = search_service.search(
        session, actor, project.id, query=candidate.native_id, scope=SearchScope.PROJECT_SHARED
    ).hits
    series_hits = {
        hit.target_id
        for hit in by_accession
        if hit.target_kind.value == "scientific_object_series"
    }
    # Both the Protein and the Sequence are discoverable through the canonical
    # external identity, with no new search index.
    assert {result.protein_series_id, result.sequence_series_id} <= series_hits

    by_name = search_service.search(
        session, actor, project.id, query=candidate.protein_name, scope=SearchScope.PROJECT_SHARED
    ).hits
    assert result.protein_series_id in {hit.target_id for hit in by_name}


def test_search_does_not_leak_the_bundle_to_another_project(session):
    owner_a = _actor(session)
    project_a = _project(session, owner_a, "Leak A")
    owner_b = _actor(session)
    project_b = _project(session, owner_b, "Leak B")
    registry = _protein_registry()
    _, candidate, _ = _import_first(session, registry, owner_a, project_a)
    hits = search_service.search(
        session, owner_b, project_b.id, query=candidate.native_id, scope=SearchScope.PROJECT_SHARED
    ).hits
    assert hits == []


def test_a_large_sequence_stays_bounded_in_project_context(session):
    actor = _actor(session)
    project = _project(session, actor)
    driver = FakeProteinDriver()
    registry = _registry(driver)
    native_id = "large-seq-1"
    length = 90_000
    driver.state.seed(
        ResolvedProteinRecord(
            provider_key=FAKE_PROTEIN_PROVIDER_KEY,
            authority=FAKE_PROTEIN_AUTHORITY,
            native_id=native_id,
            canonical_sequence=deterministic_sequence(native_id, length),
            protein_name="Very long synthetic protein",
            organism_name="Synthetic organism",
            sequence_length=length,
        )
    )
    result = _import(session, registry, actor, project, FAKE_PROTEIN_AUTHORITY, native_id)
    revision = session.get(ScientificObjectRevision, result.sequence_revision_id)
    assert revision is not None
    # Persistence keeps the COMPLETE bounded sequence...
    assert len(revision.payload["sequence"]) == length

    # ...while Agent context stays within the existing bounded rules and never
    # carries the sequence text.
    context = build_context(
        session,
        actor,
        project.id,
        _registry(),
        ContextSelectionCreate(series_ids=[result.sequence_series_id]),
    )
    serialized = context.model_dump_json()
    assert len(serialized) < 20_000
    assert revision.payload["sequence"][:40] not in serialized


def test_import_does_not_automatically_add_objects_to_agent_context(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    _, _, result = _import_first(session, registry, actor, project)

    # The existing Phase-6/12 flow is unchanged: import creates no ContextSelection
    # and no automatic inclusion. The implicit context is the visible SERIES
    # skeleton only — it carries no revision payload, so the imported canonical
    # sequence can never leak into a turn without an explicit human selection.
    implicit = build_context(session, actor, project.id, _registry(), ContextSelectionCreate())
    assert implicit.revisions == []
    serialized = implicit.model_dump_json()
    sequence_revision = session.get(ScientificObjectRevision, result.sequence_revision_id)
    assert sequence_revision is not None
    assert sequence_revision.payload["sequence"][:30] not in serialized

    # An explicit ContextSelection is still the only way to put them in context.
    explicit = build_context(
        session,
        actor,
        project.id,
        _registry(),
        ContextSelectionCreate(series_ids=[result.protein_series_id]),
    )
    assert result.protein_series_id in {ref.series_id for ref in explicit.series}
    assert result.protein_revision_id in {ref.revision_id for ref in explicit.revisions}


# ---------------------------------------------------------------------------
# Provider != authority (load-bearing architecture regression)
# ---------------------------------------------------------------------------


class _MirrorCapability:
    """A DIFFERENT resolver that resolves the REAL `uniprot` identity authority."""

    provider_key = "mirrorprotein"
    kind = CapabilityKind.PROTEIN_DISCOVERY

    def __init__(self) -> None:
        self._record = ResolvedProteinRecord(
            provider_key="mirrorprotein",
            authority="uniprot",
            native_id="P12345",
            canonical_sequence=deterministic_sequence("mirror", 30),
            protein_name="Mirrored protein",
            organism_name="Homo sapiens",
            sequence_length=30,
        )

    def search(self, query: str, limit: int, credentials: CredentialLease) -> ProteinSearchResult:
        return ProteinSearchResult(
            provider_key=self.provider_key,
            candidates=(
                ProteinCandidate(
                    provider_key=self.provider_key,
                    authority="uniprot",
                    native_id="P12345",
                    protein_name="Mirrored protein",
                    organism_name="Homo sapiens",
                    sequence_length=30,
                ),
            ),
        )

    def resolve(
        self, authority: str, native_id: str, credentials: CredentialLease
    ) -> ResolvedProteinRecord:
        if authority != "uniprot" or native_id != "P12345":
            raise CapabilityError(
                CapabilityErrorKind.NOT_FOUND,
                "not found",
                provider_key=self.provider_key,
                capability_kind=CapabilityKind.PROTEIN_DISCOVERY,
            )
        return self._record


class _MirrorDriver:
    name = "mirrorprotein"
    display_name = "Mirror Protein"
    description = "alternate resolver for the uniprot authority"
    authorities = ("uniprot",)
    required_credential_kinds: tuple[str, ...] = ()
    capabilities: ClassVar[dict] = {CapabilityKind.PROTEIN_DISCOVERY: _MirrorCapability()}

    def start(self, context) -> None:
        pass

    def stop(self) -> None:
        pass

    def probe_health(self):
        from revolab.enums import ProviderRuntimeHealth

        return ProviderRuntimeHealth.READY


def test_an_alternate_resolver_creates_the_uniprot_identity_not_its_own(session):
    """`provider_key == authority` holds for UniProt only by coincidence."""
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(_MirrorDriver())

    discovered = protein_service.discover_proteins(
        session,
        registry,
        InMemorySecretStore(),
        actor,
        project.id,
        provider_key="mirrorprotein",
        query="P12345",
    )
    candidate = discovered.candidates[0]
    assert candidate.provider_key == "mirrorprotein"
    assert candidate.authority == "uniprot"

    result = protein_service.import_protein(
        session,
        registry,
        InMemorySecretStore(),
        actor,
        project.id,
        provider_key="mirrorprotein",
        authority=candidate.authority,
        native_id=candidate.native_id,
    )
    identity = session.scalars(select(ExternalIdentity)).one()
    assert identity.authority == "uniprot"
    assert identity.native_id == "P12345"
    assert identity.authority != "mirrorprotein"
    # The resolver is recorded as RESOLVER metadata, never as durable identity.
    reference = session.get(ExternalReference, result.external_reference_id)
    assert reference is not None
    assert reference.cache_metadata is not None
    assert reference.cache_metadata["resolver_provider"] == "mirrorprotein"


def test_the_real_uniprot_authority_is_guarded_by_the_collision_check():
    """The fake's own namespace, plus the two real guards on the real namespace.

    `fakeprotein` claims `fakeuniprot`, so a synthetic fixture identity can never be
    mistaken for a real accession — and, precisely BECAUSE it claims its own
    namespace, registering it alongside the real driver does not collide. What guards
    the real namespace is (a) the authority-collision check biting any SECOND resolver
    that claims `uniprot`, and (b) the production refusal asserted in
    `test_bootstrap.py`.
    """
    from revolab.drivers.uniprot import UNIPROT_AUTHORITY, UniProtDriver

    assert FAKE_PROTEIN_AUTHORITY != UNIPROT_AUTHORITY
    assert FakeProteinDriver().authorities == (FAKE_PROTEIN_AUTHORITY,)
    assert set(FakeProteinDriver().authorities).isdisjoint(UniProtDriver().authorities)

    # The fake and the REAL driver coexist, because their authority namespaces are
    # disjoint: the fixture cannot be mistaken for a real accession, and it does not
    # suppress the real provider either.
    coexist = DriverRegistry()
    coexist.register(FakeProteinDriver())
    coexist.register(UniProtDriver())
    assert set(coexist.names()) == {"fakeprotein", "uniprot"}

    # (a) A second resolver for the REAL authority is refused alongside the real driver.
    registry = DriverRegistry()
    registry.register(UniProtDriver())
    with pytest.raises(ValueError, match="already resolved by driver"):
        registry.register(_MirrorDriver())

    # The mirror alone is fine, and it resolves the real authority.
    mirror_only = DriverRegistry()
    mirror_only.register(_MirrorDriver())
    assert mirror_only.get("mirrorprotein").driver.authorities == ("uniprot",)


# ---------------------------------------------------------------------------
# Untrusted provider text is inert
# ---------------------------------------------------------------------------


def test_hostile_provider_text_cannot_widen_authority_or_trigger_persistence(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    before = _durable_state(session)
    result = _discover(session, registry, actor, project, query=HOSTILE_QUERY_TOKEN)
    hostile = result.candidates[0].protein_name
    assert hostile is not None
    assert "SYSTEM" in hostile  # stored verbatim as INERT data
    # Discovered text changes nothing: zero persistence, no widened authority.
    assert _durable_state(session) == before
    with pytest.raises(Exception):
        _import(session, registry, actor, project, "not-an-authority", "nope")


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


def _api_actor(client) -> str:
    return client.post("/api/actors").json()["actor_id"]


def _api_project(client, actor: str, name: str = "Protein API") -> str:
    response = client.post(
        "/api/projects", json={"name": name, "visibility": "private"}, headers={"X-Actor-Id": actor}
    )
    assert response.status_code == 201
    return response.json()["id"]


def _override_registry(registry, secret_store):
    from revolab import api as api_module
    from revolab.main import app

    app.dependency_overrides[api_module.get_driver_registry] = lambda: registry
    app.dependency_overrides[api_module.get_secret_store] = lambda: secret_store


def _clear_overrides():
    from revolab.api import get_driver_registry as api_get_driver_registry
    from revolab.api import get_secret_store as api_get_secret_store
    from revolab.main import app

    app.dependency_overrides.pop(api_get_driver_registry, None)
    app.dependency_overrides.pop(api_get_secret_store, None)


def test_api_failed_import_rolls_back_through_the_real_request_lifecycle(
    client, engine, monkeypatch
):
    """Reviewer C P2-7: the request session — not the test — must roll back.

    The in-process regressions roll the session back themselves, which mirrors the
    contract rather than exercising it. Here the failure propagates out of the route,
    so the FastAPI session dependency's `with SessionLocal()` exit is what rolls back.
    """
    from sqlalchemy.orm import Session

    from revolab import services as services_module

    registry = _protein_registry()
    _override_registry(registry, InMemorySecretStore())
    try:
        actor = _api_actor(client)
        project = _api_project(client, actor, "Atomic request")
        headers = {"X-Actor-Id": actor}
        candidate = client.get(
            f"/api/projects/{project}/proteins/discover",
            params={"provider_key": FAKE_PROTEIN_PROVIDER_KEY, "q": DEFAULT_QUERY},
            headers=headers,
        ).json()["candidates"][0]

        with Session(engine) as pre:
            before = _durable_state(pre)

        def explode(*args: Any, **kwargs: Any):
            raise RuntimeError("injected failure after objects were staged")

        monkeypatch.setattr(services_module, "record_imported_as", explode)
        # `TestClient` re-raises an unhandled route error by default; the point is that
        # the dependency teardown still rolls the transaction back.
        with pytest.raises(RuntimeError, match="injected failure"):
            client.post(
                f"/api/projects/{project}/proteins/import",
                json={
                    "provider_key": FAKE_PROTEIN_PROVIDER_KEY,
                    "authority": candidate["authority"],
                    "native_id": candidate["native_id"],
                },
                headers=headers,
            )
    finally:
        _clear_overrides()

    with Session(engine) as verify:
        assert _durable_state(verify) == before
        assert (
            verify.scalar(
                select(func.count())
                .select_from(ExternalIdentity)
                .where(ExternalIdentity.native_id == candidate["native_id"])
            )
            == 0
        )


def test_api_discover_and_import_roundtrip(client):
    registry = _protein_registry()
    _override_registry(registry, InMemorySecretStore())
    try:
        actor = _api_actor(client)
        project = _api_project(client, actor)
        headers = {"X-Actor-Id": actor}

        discovered = client.get(
            f"/api/projects/{project}/proteins/discover",
            params={"provider_key": FAKE_PROTEIN_PROVIDER_KEY, "q": DEFAULT_QUERY, "limit": 3},
            headers=headers,
        )
        assert discovered.status_code == 200, discovered.text
        body = discovered.json()
        assert body["provider_key"] == FAKE_PROTEIN_PROVIDER_KEY
        assert body["query"] == DEFAULT_QUERY
        candidate = body["candidates"][0]
        assert candidate["authority"] == FAKE_PROTEIN_AUTHORITY
        # No sequence, no provider payload.
        assert "sequence" not in candidate

        imported = client.post(
            f"/api/projects/{project}/proteins/import",
            json={
                "provider_key": FAKE_PROTEIN_PROVIDER_KEY,
                "authority": candidate["authority"],
                "native_id": candidate["native_id"],
            },
            headers=headers,
        )
        assert imported.status_code == 201, imported.text
        result = imported.json()
        assert result["authority"] == FAKE_PROTEIN_AUTHORITY
        assert result["native_id"] == candidate["native_id"]
        assert UUID(result["protein_series_id"])
        assert UUID(result["sequence_series_id"])
        assert UUID(result["external_reference_id"])
        assert "sequence" not in result

        # The resulting objects are readable through the EXISTING object surfaces.
        detail = client.get(
            f"/api/projects/{project}/objects/{result['protein_series_id']}", headers=headers
        )
        assert detail.status_code == 200
        assert detail.json()["series"]["object_type"] == "protein"
        sequence_detail = client.get(
            f"/api/projects/{project}/objects/{result['sequence_series_id']}", headers=headers
        )
        assert sequence_detail.status_code == 200
        assert sequence_detail.json()["series"]["object_type"] == "sequence"

        # Repeat import is idempotent through the API too.
        again = client.post(
            f"/api/projects/{project}/proteins/import",
            json={
                "provider_key": FAKE_PROTEIN_PROVIDER_KEY,
                "authority": candidate["authority"],
                "native_id": candidate["native_id"],
            },
            headers=headers,
        )
        assert again.status_code == 201
        assert again.json() == result
    finally:
        _clear_overrides()


def test_api_import_request_carries_stable_identity_only(client):
    registry = _protein_registry()
    _override_registry(registry, InMemorySecretStore())
    try:
        actor = _api_actor(client)
        project = _api_project(client, actor, "Strict body")
        # An extra field (tampered scientific data) fails closed.
        extra = client.post(
            f"/api/projects/{project}/proteins/import",
            json={
                "provider_key": FAKE_PROTEIN_PROVIDER_KEY,
                "authority": FAKE_PROTEIN_AUTHORITY,
                "native_id": "x-1",
                "protein_name": "Injected name",
            },
            headers={"X-Actor-Id": actor},
        )
        assert extra.status_code == 422

        # Even a well-formed request cannot smuggle scientific content: the server
        # re-resolves and the provider decides every persisted value.
        driver = registry.get(FAKE_PROTEIN_PROVIDER_KEY).driver
        driver.state.seed(
            ResolvedProteinRecord(
                provider_key=FAKE_PROTEIN_PROVIDER_KEY,
                authority=FAKE_PROTEIN_AUTHORITY,
                native_id="server-truth-1",
                canonical_sequence=deterministic_sequence("server-truth", 21),
                protein_name="Server-side truth",
                organism_name="Synthetic organism",
                sequence_length=21,
            )
        )
        imported = client.post(
            f"/api/projects/{project}/proteins/import",
            json={
                "provider_key": FAKE_PROTEIN_PROVIDER_KEY,
                "authority": FAKE_PROTEIN_AUTHORITY,
                "native_id": "server-truth-1",
            },
            headers={"X-Actor-Id": actor},
        )
        assert imported.status_code == 201, imported.text
        assert imported.json()["protein_name"] == "Server-side truth"
    finally:
        _clear_overrides()


def test_api_viewer_may_discover_but_import_returns_authorization_failure(client):
    registry = _protein_registry()
    _override_registry(registry, InMemorySecretStore())
    try:
        owner = _api_actor(client)
        project = _api_project(client, owner, "Viewer negative")
        viewer = _api_actor(client)
        added = client.post(
            f"/api/projects/{project}/members",
            json={"actor_id": viewer, "role": "viewer"},
            headers={"X-Actor-Id": owner},
        )
        assert added.status_code == 201

        discovered = client.get(
            f"/api/projects/{project}/proteins/discover",
            params={"provider_key": FAKE_PROTEIN_PROVIDER_KEY, "q": DEFAULT_QUERY},
            headers={"X-Actor-Id": viewer},
        )
        assert discovered.status_code == 200
        candidate = discovered.json()["candidates"][0]

        denied = client.post(
            f"/api/projects/{project}/proteins/import",
            json={
                "provider_key": FAKE_PROTEIN_PROVIDER_KEY,
                "authority": candidate["authority"],
                "native_id": candidate["native_id"],
            },
            headers={"X-Actor-Id": viewer},
        )
        assert denied.status_code == 403
    finally:
        _clear_overrides()


def test_api_stranger_cannot_discover(client):
    registry = _protein_registry()
    _override_registry(registry, InMemorySecretStore())
    try:
        owner = _api_actor(client)
        project = _api_project(client, owner, "Stranger negative")
        stranger = _api_actor(client)
        denied = client.get(
            f"/api/projects/{project}/proteins/discover",
            params={"provider_key": FAKE_PROTEIN_PROVIDER_KEY, "q": DEFAULT_QUERY},
            headers={"X-Actor-Id": stranger},
        )
        assert denied.status_code == 403
    finally:
        _clear_overrides()


def test_api_provider_unavailable_reports_a_typed_non_destructive_error(client):
    registry = _protein_registry(healthy=False)
    _override_registry(registry, InMemorySecretStore())
    try:
        actor = _api_actor(client)
        project = _api_project(client, actor, "Unavailable")
        response = client.get(
            f"/api/projects/{project}/proteins/discover",
            params={"provider_key": FAKE_PROTEIN_PROVIDER_KEY, "q": DEFAULT_QUERY},
            headers={"X-Actor-Id": actor},
        )
        assert response.status_code == 503
        assert response.json()["detail"]
        assert client.get(f"/api/projects/{project}/objects", headers={"X-Actor-Id": actor}).json() == []
    finally:
        _clear_overrides()


def test_api_changed_snapshot_reports_a_conflict_instead_of_updating(client):
    driver = FakeProteinDriver()
    registry = _registry(driver)
    _override_registry(registry, InMemorySecretStore())
    try:
        actor = _api_actor(client)
        project = _api_project(client, actor, "Conflict")
        headers = {"X-Actor-Id": actor}
        candidate = client.get(
            f"/api/projects/{project}/proteins/discover",
            params={"provider_key": FAKE_PROTEIN_PROVIDER_KEY, "q": DEFAULT_QUERY},
            headers=headers,
        ).json()["candidates"][0]
        body = {
            "provider_key": FAKE_PROTEIN_PROVIDER_KEY,
            "authority": candidate["authority"],
            "native_id": candidate["native_id"],
        }
        assert client.post(
            f"/api/projects/{project}/proteins/import", json=body, headers=headers
        ).status_code == 201
        original = driver.state.resolve(candidate["native_id"])
        assert original is not None
        driver.state.seed(
            ResolvedProteinRecord(
                provider_key=original.provider_key,
                authority=original.authority,
                native_id=original.native_id,
                canonical_sequence=deterministic_sequence("changed", 55),
                protein_name=original.protein_name,
                organism_name=original.organism_name,
                sequence_length=55,
            )
        )
        conflict = client.post(
            f"/api/projects/{project}/proteins/import", json=body, headers=headers
        )
        assert conflict.status_code == 409
        assert "changed since the imported snapshot" in conflict.json()["detail"]
    finally:
        _clear_overrides()


def test_api_rejects_a_hostile_or_overselected_discovery_query(client):
    registry = _protein_registry()
    _override_registry(registry, InMemorySecretStore())
    try:
        actor = _api_actor(client)
        project = _api_project(client, actor, "Bounds")
        headers = {"X-Actor-Id": actor}
        over = client.get(
            f"/api/projects/{project}/proteins/discover",
            params={"provider_key": FAKE_PROTEIN_PROVIDER_KEY, "q": "x" * 400},
            headers=headers,
        )
        assert over.status_code == 422
        over_limit = client.get(
            f"/api/projects/{project}/proteins/discover",
            params={"provider_key": FAKE_PROTEIN_PROVIDER_KEY, "q": "a", "limit": 500},
            headers=headers,
        )
        assert over_limit.status_code == 422
    finally:
        _clear_overrides()


# ---------------------------------------------------------------------------
# Schema discipline
# ---------------------------------------------------------------------------


def test_import_request_schema_is_stable_identity_only():
    fields = ProteinImportCreate.model_fields
    assert set(fields) == {"provider_key", "authority", "native_id"}
    assert ProteinImportCreate.model_config["extra"] == "forbid"
    with pytest.raises(Exception):
        ProteinImportCreate(
            provider_key=FAKE_PROTEIN_PROVIDER_KEY,
            authority=FAKE_PROTEIN_AUTHORITY,
            native_id="x",
            sequence="MALW",
        )


def test_import_response_contains_no_canonical_sequence():
    assert set(ProteinImportRead.model_fields) == {
        "protein_series_id",
        "protein_revision_id",
        "sequence_series_id",
        "sequence_revision_id",
        "external_reference_id",
        "authority",
        "native_id",
        "protein_name",
    }


# ---------------------------------------------------------------------------
# Bounds and server-side validation
# ---------------------------------------------------------------------------


def test_import_rejects_an_over_bound_or_missing_identity(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    with pytest.raises(ValidationError):
        _import(session, registry, actor, project, "", "x")
    with pytest.raises(ValidationError):
        _import(session, registry, actor, project, "a" * 101, "x")
    with pytest.raises(ValidationError):
        _import(session, registry, actor, project, "a", "b" * 301)


def test_import_rejects_a_provider_that_substitutes_the_identity(session):
    actor = _actor(session)
    project = _project(session, actor)
    driver = FakeProteinDriver()
    registry = _registry(driver)
    discovered = _discover(session, registry, actor, project)
    candidate = discovered.candidates[0]
    # The provider now answers the requested id with a record carrying a DIFFERENT
    # durable identity; the identity match fails closed before any write.
    driver.state.seed_for(
        candidate.native_id,
        ResolvedProteinRecord(
            provider_key=FAKE_PROTEIN_PROVIDER_KEY,
            authority=FAKE_PROTEIN_AUTHORITY,
            native_id="substituted",
            canonical_sequence=deterministic_sequence("sub", 12),
            sequence_length=12,
        ),
    )
    before = _durable_state(session)
    with pytest.raises((ValidationError, CapabilityError)):
        _import(session, registry, actor, project, candidate.authority, candidate.native_id)
    assert _durable_state(session) == before


def test_import_rejects_a_provider_record_with_an_over_bound_sequence(session):
    actor = _actor(session)
    project = _project(session, actor)
    driver = FakeProteinDriver()
    registry = _registry(driver)
    native_id = "oversized-1"
    driver.state.seed(
        ResolvedProteinRecord(
            provider_key=FAKE_PROTEIN_PROVIDER_KEY,
            authority=FAKE_PROTEIN_AUTHORITY,
            native_id=native_id,
            canonical_sequence="A" * (MAX_PROTEIN_SEQUENCE_CHARS + 1),
            sequence_length=MAX_PROTEIN_SEQUENCE_CHARS + 1,
        )
    )
    before = _durable_state(session)
    with pytest.raises((ValidationError, CapabilityError)):
        _import(session, registry, actor, project, FAKE_PROTEIN_AUTHORITY, native_id)
    assert _durable_state(session) == before


def test_import_rejects_a_sequence_length_disagreement_server_side(session):
    actor = _actor(session)
    project = _project(session, actor)
    driver = FakeProteinDriver()
    registry = _registry(driver)
    native_id = "mismatch-1"
    driver.state.seed(
        ResolvedProteinRecord(
            provider_key=FAKE_PROTEIN_PROVIDER_KEY,
            authority=FAKE_PROTEIN_AUTHORITY,
            native_id=native_id,
            canonical_sequence=deterministic_sequence(native_id, 40),
            sequence_length=99,
        )
    )
    before = _durable_state(session)
    with pytest.raises(ValidationError, match="length"):
        _import(session, registry, actor, project, FAKE_PROTEIN_AUTHORITY, native_id)
    assert _durable_state(session) == before


def test_import_rejects_an_invalid_reported_length_server_side(session):
    """The application boundary re-checks the reported length (defense in depth).

    The real driver already fails closed on a present-but-invalid length, so this
    exercises the SERVICE-side guard with a deliberately mis-wired provider response.
    Mutation-sensitivity: without that guard the import fails later with the
    "disagrees" message instead, so the asserted message distinguishes the two paths.
    """
    actor = _actor(session)
    project = _project(session, actor)
    driver = FakeProteinDriver()
    registry = _registry(driver)
    native_id = "bad-length-1"
    driver.state.seed(
        ResolvedProteinRecord(
            provider_key=FAKE_PROTEIN_PROVIDER_KEY,
            authority=FAKE_PROTEIN_AUTHORITY,
            native_id=native_id,
            canonical_sequence=deterministic_sequence(native_id, 30),
            protein_name="Mis-wired provider record",
            organism_name="Synthetic organism",
            sequence_length=0,
        )
    )
    before = _durable_state(session)
    with pytest.raises(ValidationError, match="invalid sequence length"):
        _import(session, registry, actor, project, FAKE_PROTEIN_AUTHORITY, native_id)
    assert _durable_state(session) == before


def test_protein_series_name_falls_back_to_the_accession_when_unnamed(session):
    actor = _actor(session)
    project = _project(session, actor)
    driver = FakeProteinDriver()
    registry = _registry(driver)
    native_id = "unnamed-1"
    driver.state.seed(
        ResolvedProteinRecord(
            provider_key=FAKE_PROTEIN_PROVIDER_KEY,
            authority=FAKE_PROTEIN_AUTHORITY,
            native_id=native_id,
            canonical_sequence=deterministic_sequence(native_id, 18),
            protein_name=None,
            organism_name=None,
            sequence_length=18,
        )
    )
    result = _import(session, registry, actor, project, FAKE_PROTEIN_AUTHORITY, native_id)
    assert result.protein_name == native_id
    series = session.get(ScientificObjectSeries, result.protein_series_id)
    assert series is not None and series.name == native_id
    revision = session.get(ScientificObjectRevision, result.protein_revision_id)
    assert revision is not None and revision.payload["organism"] is None


def test_an_oversized_provider_name_yields_a_bounded_series_name(session):
    """`scientific_object_series.name` is varchar(200): a long provider name must be
    bounded BEFORE persistence, and the response must echo the stored name."""
    actor = _actor(session)
    project = _project(session, actor)
    driver = FakeProteinDriver()
    registry = _registry(driver)
    native_id = "long-name-1"
    driver.state.seed(
        ResolvedProteinRecord(
            provider_key=FAKE_PROTEIN_PROVIDER_KEY,
            authority=FAKE_PROTEIN_AUTHORITY,
            native_id=native_id,
            canonical_sequence=deterministic_sequence(native_id, 25),
            protein_name="N" * 5_000,
            organism_name="Synthetic organism",
            sequence_length=25,
        )
    )
    result = _import(session, registry, actor, project, FAKE_PROTEIN_AUTHORITY, native_id)
    assert len(result.protein_name) <= 200
    series = session.get(ScientificObjectSeries, result.protein_series_id)
    assert series is not None
    assert series.name == result.protein_name
    assert len(series.name) <= 200


def test_no_raw_provider_payload_is_persisted_anywhere(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _protein_registry()
    _, _, result = _import_first(session, registry, actor, project)
    reference = session.get(ExternalReference, result.external_reference_id)
    assert reference is not None and reference.cache_metadata is not None
    # Only the three bounded provenance/debugging keys, never the payload.
    assert set(reference.cache_metadata) <= {
        "resolver_provider",
        "source_release",
        "source_release_date",
    }
    for revision in session.scalars(select(ScientificObjectRevision)):
        if revision.object_type == ObjectType.PROTEIN.value:
            assert set(revision.payload) == {"organism", "source_sequence_ref", "chain"}
        else:
            assert set(revision.payload) == {"kind", "sequence"}
