"""Phase-13 external literature discovery + explicit import tests.

The provider is the in-process `FakeLiteratureDriver`, which realizes the SAME
`LiteratureDiscoveryCapability` boundary and enters through the SAME
Driver/Capability registry as the real NCBI driver (TODO.md section 36). No live
network access, no test-only search bypass.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import ClassVar
from uuid import UUID

import pytest
from sqlalchemy import event, func, select

from revolab import literature as literature_service
from revolab import search as search_service
from revolab import services
from revolab.capabilities import LiteratureCandidate
from revolab.domain.errors import AuthorizationError, NotFoundError, ValidationError
from revolab.drivers import DriverContext, DriverRegistry
from revolab.enums import CapabilityErrorKind, CapabilityKind, EvidenceKind, ResourceKind, Role
from revolab.models import (
    Decision,
    Evidence,
    ExternalReference,
    LiteratureReference,
    ProjectNote,
    ProjectResourceLink,
    ResourceStewardship,
    ScientificObjectSeries,
)
from revolab.schemas import LiteratureImportCreate, ReferenceRead
from revolab.secret_store import InMemorySecretStore
from revolab.testing.fake_literature import (
    FAKE_LITERATURE_AUTHORITY,
    FAKE_LITERATURE_PROVIDER_KEY,
    UNAVAILABLE_QUERY_TOKEN,
    FakeLiteratureDriver,
)


def _actor(session):
    return services.create_actor(session)


def _project(session, actor, name="Literature P"):
    return services.create_project(session, actor, name)


def _registry(*drivers) -> DriverRegistry:
    registry = DriverRegistry()
    for driver in drivers:
        registry.register(driver)
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return registry


def _literature_registry(*, healthy: bool = True) -> DriverRegistry:
    return _registry(FakeLiteratureDriver(healthy=healthy))


def _discover(session, registry, actor, project, query="enzyme active-site redesign", limit=5):
    return literature_service.discover_literature(
        session,
        registry,
        InMemorySecretStore(),
        actor,
        project.id,
        provider_key=FAKE_LITERATURE_PROVIDER_KEY,
        query=query,
        limit=limit,
    )


def _import(session, registry, actor, project, authority, native_id):
    return literature_service.import_literature(
        session,
        registry,
        InMemorySecretStore(),
        actor,
        project.id,
        provider_key=FAKE_LITERATURE_PROVIDER_KEY,
        authority=authority,
        native_id=native_id,
    )


def _import_first(session, registry, actor, project, query="enzyme active-site redesign"):
    discovered = _discover(session, registry, actor, project, query=query)
    candidate = discovered.candidates[0]
    row = _import(session, registry, actor, project, candidate.authority, candidate.native_id)
    return discovered, candidate, row


# ---------------------------------------------------------------------------
# Discovery: read-only, bounded, provider-neutral
# ---------------------------------------------------------------------------


def test_discovery_returns_bounded_provider_neutral_candidates(session):
    actor = _actor(session)
    project = _project(session, actor)
    result = _discover(session, _literature_registry(), actor, project, limit=3)

    assert result.provider_key == FAKE_LITERATURE_PROVIDER_KEY
    assert 1 <= len(result.candidates) <= 3
    for candidate in result.candidates:
        # provider key and durable authority are DISTINCT identity concepts.
        assert candidate.provider_key == FAKE_LITERATURE_PROVIDER_KEY
        assert candidate.authority == FAKE_LITERATURE_AUTHORITY
        assert candidate.provider_key != candidate.authority
        assert candidate.native_id
        # ESummary-style provider vocabulary never appears in the projection.
        for provider_field in ("pubdate", "articleids", "fulljournalname", "uid"):
            assert not hasattr(candidate, provider_field)


def test_discovery_makes_no_durable_write(session):
    actor = _actor(session)
    project = _project(session, actor)
    statements: list[str] = []

    def _record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement.strip().upper())

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", _record)
    try:
        _discover(session, _literature_registry(), actor, project)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    # The only statements allowed are reads. External discovery never persists a
    # candidate, a cache row, or a reference.
    assert not [s for s in statements if s.startswith(("INSERT", "UPDATE", "DELETE"))]
    assert session.scalar(select(func.count()).select_from(LiteratureReference)) == 0


def test_external_candidate_is_not_project_truth_before_import(session):
    actor = _actor(session)
    project = _project(session, actor)
    discovered = _discover(session, _literature_registry(), actor, project)
    candidate = discovered.candidates[0]

    # The candidate is NOT searchable Project context and NOT any durable row.
    hits = search_service.search_project_shared(
        session, actor, project.id, query=candidate.native_id
    )
    assert hits.hits == []
    assert session.scalar(select(func.count()).select_from(LiteratureReference)) == 0
    assert session.scalar(select(func.count()).select_from(ProjectResourceLink)) == 0


@pytest.mark.parametrize("query", ["", "   ", "x" * 301])
def test_over_bound_query_fails_closed(session, query):
    actor = _actor(session)
    project = _project(session, actor)
    with pytest.raises(ValidationError):
        _discover(session, _literature_registry(), actor, project, query=query)


@pytest.mark.parametrize("limit", [0, -1, 21])
def test_over_bound_limit_fails_closed(session, limit):
    actor = _actor(session)
    project = _project(session, actor)
    with pytest.raises(ValidationError):
        _discover(session, _literature_registry(), actor, project, limit=limit)


def test_unknown_provider_fails_closed(session):
    actor = _actor(session)
    project = _project(session, actor)
    with pytest.raises(NotFoundError):
        literature_service.discover_literature(
            session,
            _literature_registry(),
            InMemorySecretStore(),
            actor,
            project.id,
            provider_key="not-a-provider",
            query="x",
        )


def test_provider_unavailable_is_a_typed_failure(session):
    actor = _actor(session)
    project = _project(session, actor)
    from revolab.capabilities import CapabilityError

    with pytest.raises(CapabilityError) as excinfo:
        _discover(session, _literature_registry(healthy=False), actor, project)
    assert excinfo.value.kind is CapabilityErrorKind.PROVIDER_UNAVAILABLE


def test_provider_search_failure_is_a_typed_failure_without_a_write(session):
    actor = _actor(session)
    project = _project(session, actor)
    from revolab.capabilities import CapabilityError

    with pytest.raises(CapabilityError) as excinfo:
        _discover(
            session,
            _literature_registry(),
            actor,
            project,
            query=f"anything {UNAVAILABLE_QUERY_TOKEN}",
        )
    assert excinfo.value.kind is CapabilityErrorKind.PROVIDER_UNAVAILABLE
    assert session.scalar(select(func.count()).select_from(LiteratureReference)) == 0


# ---------------------------------------------------------------------------
# Import: authority, re-resolution, idempotency, global identity
# ---------------------------------------------------------------------------


def test_viewer_may_discover_but_may_not_import(session):
    owner = _actor(session)
    viewer = services.create_actor(session)
    project = _project(session, owner)
    services.add_membership(session, owner, project.id, viewer, Role.VIEWER.value)
    registry = _literature_registry()

    # A viewer may discover: the capability kind is a read-only kind.
    discovered = _discover(session, registry, viewer, project)
    candidate = discovered.candidates[0]

    # A viewer must NOT import.
    with pytest.raises(AuthorizationError):
        _import(session, registry, viewer, project, candidate.authority, candidate.native_id)
    assert session.scalar(select(func.count()).select_from(LiteratureReference)) == 0


def test_non_member_cannot_discover_or_import(session):
    owner = _actor(session)
    stranger = services.create_actor(session)
    project = _project(session, owner)
    registry = _literature_registry()
    with pytest.raises(AuthorizationError):
        _discover(session, registry, stranger, project)
    with pytest.raises(AuthorizationError):
        _import(session, registry, stranger, project, FAKE_LITERATURE_AUTHORITY, "1")


def test_member_may_import(session):
    owner = _actor(session)
    member = services.create_actor(session)
    project = _project(session, owner)
    services.add_membership(session, owner, project.id, member, Role.MEMBER.value)
    registry = _literature_registry()
    discovered = _discover(session, registry, member, project)
    candidate = discovered.candidates[0]
    row = _import(session, registry, member, project, candidate.authority, candidate.native_id)
    assert row.authority == FAKE_LITERATURE_AUTHORITY


def test_import_re_resolves_identity_and_never_trusts_candidate_metadata(session):
    actor = _actor(session)
    project = _project(session, actor)
    driver = FakeLiteratureDriver()
    registry = _registry(driver)
    discovered = _discover(session, registry, actor, project)
    candidate = discovered.candidates[0]

    # The provider's CURRENT record now differs from what the search returned.
    driver.state.seed(
        LiteratureCandidate(
            provider_key=FAKE_LITERATURE_PROVIDER_KEY,
            authority=FAKE_LITERATURE_AUTHORITY,
            native_id=candidate.native_id,
            title="CURRENT provider title (re-resolved)",
            authors=("Current Author",),
            journal="Current Journal",
            publication_year=2024,
            doi=None,
        )
    )
    row = _import(session, registry, actor, project, candidate.authority, candidate.native_id)

    # The persisted title is the RE-RESOLVED one, not the earlier search result.
    assert row.title == "CURRENT provider title (re-resolved)"
    assert row.title != candidate.title


def test_import_request_carries_no_client_supplied_bibliographic_metadata():
    # A tampered client title is impossible: the contract has no such field.
    assert set(LiteratureImportCreate.model_fields) == {
        "provider_key",
        "authority",
        "native_id",
    }
    with pytest.raises(ValueError):
        LiteratureImportCreate.model_validate(
            {
                "provider_key": FAKE_LITERATURE_PROVIDER_KEY,
                "authority": FAKE_LITERATURE_AUTHORITY,
                "native_id": "1",
                "title": "FORGED TITLE",
            }
        )


def test_resolve_identity_mismatch_fails_closed(session):
    actor = _actor(session)
    project = _project(session, actor)

    class _MismatchedCapability:
        provider_key = FAKE_LITERATURE_PROVIDER_KEY
        kind = CapabilityKind.LITERATURE_DISCOVERY

        def search(self, query, limit, credentials):  # pragma: no cover - unused
            raise AssertionError("search must not be used")

        def resolve(self, authority, native_id, credentials):
            return LiteratureCandidate(
                provider_key=FAKE_LITERATURE_PROVIDER_KEY,
                authority=FAKE_LITERATURE_AUTHORITY,
                native_id="99999999",
                title="Wrong record",
            )

    class _MismatchedDriver:
        name = FAKE_LITERATURE_PROVIDER_KEY
        display_name = "Mismatched"
        description = None
        authorities = (FAKE_LITERATURE_AUTHORITY,)
        required_credential_kinds: tuple[str, ...] = ()
        capabilities: ClassVar[dict] = {
            CapabilityKind.LITERATURE_DISCOVERY: _MismatchedCapability()
        }

        def start(self, context):
            pass

        def stop(self):
            pass

        def probe_health(self):
            from revolab.enums import ProviderRuntimeHealth

            return ProviderRuntimeHealth.READY

    with pytest.raises(ValidationError):
        _import(session, _registry(_MismatchedDriver()), actor, project, FAKE_LITERATURE_AUTHORITY, "1")
    assert session.scalar(select(func.count()).select_from(LiteratureReference)) == 0
    assert session.scalar(select(func.count()).select_from(ProjectResourceLink)) == 0


def test_repeated_import_is_idempotent(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _literature_registry()
    _, candidate, first = _import_first(session, registry, actor, project)
    second = _import(session, registry, actor, project, candidate.authority, candidate.native_id)

    assert second.literature_id == first.literature_id
    assert session.scalar(select(func.count()).select_from(LiteratureReference)) == 1
    links = session.scalars(
        select(ProjectResourceLink).where(
            ProjectResourceLink.project_id == project.id,
            ProjectResourceLink.resource_id == first.literature_id,
        )
    ).all()
    assert len(links) == 1


def test_two_projects_importing_the_same_publication_share_one_global_reference(session):
    owner_a = _actor(session)
    owner_b = services.create_actor(session)
    project_a = _project(session, owner_a, "Literature A")
    project_b = _project(session, owner_b, "Literature B")
    registry = _literature_registry()
    _, candidate, row_a = _import_first(session, registry, owner_a, project_a)
    row_b = _import(session, registry, owner_b, project_b, candidate.authority, candidate.native_id)

    assert row_b.literature_id == row_a.literature_id
    assert session.scalar(select(func.count()).select_from(LiteratureReference)) == 1
    links = session.scalars(
        select(ProjectResourceLink).where(ProjectResourceLink.resource_id == row_a.literature_id)
    ).all()
    assert {link.project_id for link in links} == {project_a.id, project_b.id}

    # Each Project sees the shared publication through its own read lens; the
    # other Project's link is not a cross-Project visibility leak.
    assert search_service.search_project_shared(
        session, owner_a, project_a.id, query=str(candidate.native_id)
    ).hits
    assert search_service.search_project_shared(
        session, owner_b, project_b.id, query=str(candidate.native_id)
    ).hits


def test_trusted_import_does_not_weaken_generic_manual_reference_share_authority(session):
    owner_a = _actor(session)
    owner_b = services.create_actor(session)
    project_a = _project(session, owner_a, "Share A")
    project_b = _project(session, owner_b, "Share B")
    registry = _literature_registry()
    _, candidate, row_a = _import_first(session, registry, owner_a, project_a)

    # The GENERIC request-derived path still enforces share authority: Project B's
    # owner cannot link an existing global reference they cannot read.
    with pytest.raises(AuthorizationError):
        services.create_literature_reference(
            session, owner_b, project_b.id, candidate.authority, candidate.native_id
        )
    assert session.scalar(
        select(func.count())
        .select_from(ProjectResourceLink)
        .where(
            ProjectResourceLink.project_id == project_b.id,
            ProjectResourceLink.resource_id == row_a.literature_id,
        )
    ) == 0

    # The PROVIDER-TRUSTED import path may, because the public resolver
    # independently confirmed the identity for this Actor.
    row_b = _import(session, registry, owner_b, project_b, candidate.authority, candidate.native_id)
    assert row_b.literature_id == row_a.literature_id


def test_import_persists_only_the_intended_durable_rows(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _literature_registry()
    _, _, row = _import_first(session, registry, actor, project)

    assert session.scalar(select(func.count()).select_from(LiteratureReference)) == 1
    assert session.scalar(
        select(func.count())
        .select_from(ProjectResourceLink)
        .where(ProjectResourceLink.resource_id == row.literature_id)
    ) == 1
    assert session.scalar(
        select(func.count())
        .select_from(ResourceStewardship)
        .where(ResourceStewardship.resource_id == row.literature_id)
    ) == 1
    # Import is NOT interpretation and NOT object creation.
    assert session.scalar(select(func.count()).select_from(Evidence)) == 0
    assert session.scalar(select(func.count()).select_from(Decision)) == 0
    assert session.scalar(select(func.count()).select_from(ProjectNote)) == 0
    assert session.scalar(select(func.count()).select_from(ExternalReference)) == 0
    assert session.scalar(select(func.count()).select_from(ScientificObjectSeries)) == 0


def test_literature_reference_schema_was_not_widened(session):
    """TODO.md section 23: the durable reference stays intentionally small."""
    columns = set(LiteratureReference.__table__.columns.keys())
    assert columns == {
        "literature_id",
        "authority",
        "native_id",
        "title",
        "created_at",
    }
    # No candidate/cache table was invented.
    from revolab.db import Base

    assert not any(
        name in Base.metadata.tables
        for name in (
            "literature_candidates",
            "external_search_results",
            "search_cache",
            "pubmed_records",
        )
    )


def test_existing_reference_title_is_not_overwritten_by_current_provider_metadata(session):
    actor = _actor(session)
    project = _project(session, actor)
    driver = FakeLiteratureDriver()
    registry = _registry(driver)
    discovered = _discover(session, registry, actor, project)
    candidate = discovered.candidates[0]
    first = _import(session, registry, actor, project, candidate.authority, candidate.native_id)
    stored_title = first.title

    # The provider's metadata changes...
    driver.state.seed(
        LiteratureCandidate(
            provider_key=FAKE_LITERATURE_PROVIDER_KEY,
            authority=FAKE_LITERATURE_AUTHORITY,
            native_id=candidate.native_id,
            title="A totally different current title",
        )
    )
    again = _import(session, registry, actor, project, candidate.authority, candidate.native_id)

    # ...but durable identity is (authority, native_id), so the stored reference is
    # linked unchanged: title disagreement is never durable-identity disagreement.
    assert again.literature_id == first.literature_id
    session.refresh(again)
    assert again.title == stored_title


# ---------------------------------------------------------------------------
# Provider outage / Phase-12 seam / Evidence handoff
# ---------------------------------------------------------------------------


def test_provider_outage_after_import_does_not_invalidate_the_reference(session):
    actor = _actor(session)
    project = _project(session, actor)
    _, candidate, row = _import_first(session, _literature_registry(), actor, project)

    from revolab.capabilities import CapabilityError

    # The provider is now unreachable: live discovery/resolution fails...
    with pytest.raises(CapabilityError) as excinfo:
        _discover(session, _literature_registry(healthy=False), actor, project)
    assert excinfo.value.kind is CapabilityErrorKind.PROVIDER_UNAVAILABLE
    with pytest.raises(CapabilityError):
        _import(
            session,
            _literature_registry(healthy=False),
            actor,
            project,
            candidate.authority,
            candidate.native_id,
        )

    # ...but the imported LiteratureReference is still valid Project context and
    # still searchable.
    session.refresh(row)
    assert row.authority == candidate.authority
    hits = search_service.search_project_shared(
        session, actor, project.id, query=str(candidate.native_id)
    )
    assert [hit.target_id for hit in hits.hits] == [row.literature_id]


def test_imported_literature_becomes_phase12_searchable(session):
    actor = _actor(session)
    project = _project(session, actor)
    _, candidate, row = _import_first(session, _literature_registry(), actor, project)

    by_native_id = search_service.search_project_shared(
        session, actor, project.id, query=str(candidate.native_id)
    )
    assert [hit.target_id for hit in by_native_id.hits] == [row.literature_id]
    assert by_native_id.hits[0].target_kind.value == "literature_reference"

    title_token = candidate.title.split()[2] if candidate.title else "Synthetic"
    by_title = search_service.search_project_shared(
        session, actor, project.id, query=title_token
    )
    assert row.literature_id in [hit.target_id for hit in by_title.hits]

    # The other Project still sees nothing.
    other = services.create_actor(session)
    project_other = _project(session, other, "Unrelated")
    assert (
        search_service.search_project_shared(
            session, other, project_other.id, query=str(candidate.native_id)
        ).hits
        == []
    )


def test_import_alone_creates_zero_evidence_and_explicit_evidence_uses_the_reference(session):
    actor = _actor(session)
    project = _project(session, actor)
    _, _, row = _import_first(session, _literature_registry(), actor, project)

    # Import alone: zero Evidence.
    assert session.scalar(select(func.count()).select_from(Evidence)) == 0

    # Explicit interpretation uses the EXISTING canonical Evidence operation.
    decision = services.create_decision(
        session, actor, project.id, title="Adopt the redesign", statement="s"
    )
    evidence = services.create_evidence(
        session,
        actor,
        project.id,
        kind=EvidenceKind.LITERATURE.value,
        role="primary_support",
        label="Imported paper supports the redesign",
        interpretation="The publication reports improved activity.",
        polarity="supports",
        source_kind=ResourceKind.LITERATURE_REFERENCE.value,
        source_id=row.literature_id,
        target_kind="decision",
        target_id=decision.id,
    )
    assert session.scalar(select(func.count()).select_from(Evidence)) == 1
    assert evidence.source_resource_id == row.literature_id
    assert evidence.source_kind == ResourceKind.LITERATURE_REFERENCE.value


def test_import_does_not_require_reading_the_reference_beforehand(session):
    """TODO.md section 38: provider import is not an existence oracle, and it is
    not gated on a pre-existing read path — but the GENERIC path still is."""
    owner_a = _actor(session)
    owner_b = services.create_actor(session)
    project_a = _project(session, owner_a, "Oracle A")
    project_b = _project(session, owner_b, "Oracle B")
    registry = _literature_registry()
    _, candidate, row = _import_first(session, registry, owner_a, project_a)

    # Project B's owner does not know the global UUID; the provider confirms it.
    assert not services._visible_project_ids(session, owner_b, row.literature_id)
    shared = _import(session, registry, owner_b, project_b, candidate.authority, candidate.native_id)
    assert shared.literature_id == row.literature_id

    # Guessing an unrelated literature UUID through the generic path stays denied.
    bogus = UUID("00000000-0000-4000-8000-000000000001")
    with pytest.raises(AuthorizationError):
        services.share_resource(session, owner_b, project_b.id, bogus)


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


def _api_actor(client, name="lit"):
    return client.post("/api/actors").json()["actor_id"]


def _api_project(client, actor, name="Literature API"):
    response = client.post(
        "/api/projects", json={"name": name, "visibility": "private"}, headers={"X-Actor-Id": actor}
    )
    assert response.status_code == 201
    return response.json()["id"]


def _override_registry(client, app_module, registry, secret_store):
    from revolab.main import app

    app.dependency_overrides[app_module.get_driver_registry] = lambda: registry
    app.dependency_overrides[app_module.get_secret_store] = lambda: secret_store


def _clear_overrides():
    from revolab.api import get_driver_registry as api_get_driver_registry
    from revolab.api import get_secret_store as api_get_secret_store
    from revolab.main import app

    app.dependency_overrides.pop(api_get_driver_registry, None)
    app.dependency_overrides.pop(api_get_secret_store, None)


def test_api_discover_and_import_roundtrip(client):
    from revolab import api as api_module

    registry = _literature_registry()
    _override_registry(client, api_module, registry, InMemorySecretStore())
    try:
        actor = _api_actor(client)
        project = _api_project(client, actor)
        headers = {"X-Actor-Id": actor}

        discovered = client.get(
            f"/api/projects/{project}/literature/discover",
            params={
                "provider_key": FAKE_LITERATURE_PROVIDER_KEY,
                "q": "enzyme active-site redesign",
                "limit": 3,
            },
            headers=headers,
        )
        assert discovered.status_code == 200, discovered.text
        body = discovered.json()
        assert body["provider_key"] == FAKE_LITERATURE_PROVIDER_KEY
        assert body["candidates"]
        candidate = body["candidates"][0]
        assert candidate["authority"] == FAKE_LITERATURE_AUTHORITY

        imported = client.post(
            f"/api/projects/{project}/literature/import",
            json={
                "provider_key": FAKE_LITERATURE_PROVIDER_KEY,
                "authority": candidate["authority"],
                "native_id": candidate["native_id"],
            },
            headers=headers,
        )
        assert imported.status_code == 201, imported.text
        reference = imported.json()
        assert reference["resource_kind"] == "literature_reference"
        assert reference["authority"] == FAKE_LITERATURE_AUTHORITY
        assert reference["native_id"] == candidate["native_id"]
        # `ReferenceRead` is the existing typed contract: uuid + title, no raw provider payload.
        assert UUID(reference["resource_id"])
        assert reference["title"] == candidate["title"]

        # Repeated import returns the SAME global identity.
        again = client.post(
            f"/api/projects/{project}/literature/import",
            json={
                "provider_key": FAKE_LITERATURE_PROVIDER_KEY,
                "authority": candidate["authority"],
                "native_id": candidate["native_id"],
            },
            headers=headers,
        )
        assert again.status_code == 201
        assert again.json()["resource_id"] == reference["resource_id"]

        # Phase-12 Project search now finds the imported publication.
        found = client.get(
            f"/api/projects/{project}/search",
            params={"q": candidate["native_id"]},
            headers=headers,
        )
        assert found.status_code == 200
        assert [hit["target_id"] for hit in found.json()["hits"]] == [reference["resource_id"]]
    finally:
        _clear_overrides()


def test_api_discover_is_readable_by_a_viewer_but_import_is_not(client):
    from revolab import api as api_module

    registry = _literature_registry()
    _override_registry(client, api_module, registry, InMemorySecretStore())
    try:
        owner = _api_actor(client)
        project = _api_project(client, owner)
        viewer = _api_actor(client)
        added = client.post(
            f"/api/projects/{project}/members",
            json={"actor_id": viewer, "role": "viewer"},
            headers={"X-Actor-Id": owner},
        )
        assert added.status_code == 201, added.text
        headers = {"X-Actor-Id": viewer}

        discovered = client.get(
            f"/api/projects/{project}/literature/discover",
            params={"provider_key": FAKE_LITERATURE_PROVIDER_KEY, "q": "kinase"},
            headers=headers,
        )
        assert discovered.status_code == 200
        candidate = discovered.json()["candidates"][0]

        refused = client.post(
            f"/api/projects/{project}/literature/import",
            json={
                "provider_key": FAKE_LITERATURE_PROVIDER_KEY,
                "authority": candidate["authority"],
                "native_id": candidate["native_id"],
            },
            headers=headers,
        )
        assert refused.status_code == 403
    finally:
        _clear_overrides()


def test_api_import_rejects_browser_supplied_metadata_and_unknown_provider(client):
    from revolab import api as api_module

    _override_registry(
        client, api_module, _literature_registry(), InMemorySecretStore()
    )
    try:
        actor = _api_actor(client)
        project = _api_project(client, actor)
        headers = {"X-Actor-Id": actor}

        forged = client.post(
            f"/api/projects/{project}/literature/import",
            json={
                "provider_key": FAKE_LITERATURE_PROVIDER_KEY,
                "authority": FAKE_LITERATURE_AUTHORITY,
                "native_id": "abc-1",
                "title": "FORGED TITLE",
            },
            headers=headers,
        )
        assert forged.status_code == 422
        assert "FORGED TITLE" not in forged.text

        unknown = client.get(
            f"/api/projects/{project}/literature/discover",
            params={"provider_key": "nope", "q": "x"},
            headers=headers,
        )
        assert unknown.status_code == 404
    finally:
        _clear_overrides()


def test_api_discover_provider_failure_is_a_typed_non_destructive_error(client):
    from revolab import api as api_module

    _override_registry(client, api_module, _literature_registry(), InMemorySecretStore())
    try:
        actor = _api_actor(client)
        project = _api_project(client, actor)
        headers = {"X-Actor-Id": actor}
        failed = client.get(
            f"/api/projects/{project}/literature/discover",
            params={
                "provider_key": FAKE_LITERATURE_PROVIDER_KEY,
                "q": f"anything {UNAVAILABLE_QUERY_TOKEN}",
            },
            headers=headers,
        )
        assert failed.status_code == 503
        assert "detail" in failed.json()
        assert "eutils" not in failed.text
    finally:
        _clear_overrides()


def test_api_discover_rejects_over_bound_input_and_missing_actor(client):
    from revolab import api as api_module

    _override_registry(client, api_module, _literature_registry(), InMemorySecretStore())
    try:
        actor = _api_actor(client)
        project = _api_project(client, actor)
        headers = {"X-Actor-Id": actor}
        too_long = client.get(
            f"/api/projects/{project}/literature/discover",
            params={"provider_key": FAKE_LITERATURE_PROVIDER_KEY, "q": "x" * 301},
            headers=headers,
        )
        assert too_long.status_code == 422
        too_many = client.get(
            f"/api/projects/{project}/literature/discover",
            params={"provider_key": FAKE_LITERATURE_PROVIDER_KEY, "q": "x", "limit": 21},
            headers=headers,
        )
        assert too_many.status_code == 422
        no_actor = client.get(
            f"/api/projects/{project}/literature/discover",
            params={"provider_key": FAKE_LITERATURE_PROVIDER_KEY, "q": "x"},
        )
        assert no_actor.status_code == 401
    finally:
        _clear_overrides()


def test_api_import_returns_the_existing_typed_reference_contract(client):
    from revolab import api as api_module

    _override_registry(client, api_module, _literature_registry(), InMemorySecretStore())
    try:
        actor = _api_actor(client)
        project = _api_project(client, actor)
        headers = {"X-Actor-Id": actor}
        discovered = client.get(
            f"/api/projects/{project}/literature/discover",
            params={"provider_key": FAKE_LITERATURE_PROVIDER_KEY, "q": "protease"},
            headers=headers,
        ).json()
        candidate = discovered["candidates"][0]
        imported = client.post(
            f"/api/projects/{project}/literature/import",
            json={
                "provider_key": FAKE_LITERATURE_PROVIDER_KEY,
                "authority": candidate["authority"],
                "native_id": candidate["native_id"],
            },
            headers=headers,
        ).json()
        # `ReferenceRead` already expresses literature_id + authority + native_id + title.
        parsed = ReferenceRead.model_validate(imported)
        assert parsed.resource_kind == "literature_reference"
        assert parsed.title == candidate["title"]
        # No raw provider metadata is returned as persisted truth.
        assert "pubdate" not in imported
        assert "articleids" not in imported
    finally:
        _clear_overrides()


# ---------------------------------------------------------------------------
# Provider-data robustness at the HTTP boundary (no unexpected payload -> 500)
# ---------------------------------------------------------------------------


def _ncbi_registry(handler):
    """A REAL NCBIDriver over an injected deterministic HTTP transport."""
    import httpx

    from revolab.drivers.ncbi import NCBIDriver

    registry = DriverRegistry()
    registry.register(NCBIDriver(transport=httpx.MockTransport(handler)))
    registry.start_all(
        DriverContext(
            environment="test",
            settings=MappingProxyType(
                {
                    "ncbi_tool": "revolab-test",
                    "ncbi_email": "operator@example.test",
                    "ncbi_timeout_seconds": 5.0,
                    "ncbi_min_request_interval_seconds": 0.0,
                }
            ),
        )
    )
    return registry


def test_api_discover_maps_an_undecodable_provider_body_to_a_typed_error(client):
    """TODO.md section 14: unexpected provider payload MUST become a typed
    capability failure, never a 500."""
    import httpx

    from revolab import api as api_module

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("einfo.fcgi"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(
            200, content=b"not-a-gzip-stream", headers={"content-encoding": "gzip"}
        )

    _override_registry(client, api_module, _ncbi_registry(handler), InMemorySecretStore())
    try:
        actor = _api_actor(client)
        project = _api_project(client, actor)
        response = client.get(
            f"/api/projects/{project}/literature/discover",
            params={"provider_key": "ncbi", "q": "enzyme"},
            headers={"X-Actor-Id": actor},
        )
        assert response.status_code != 500
        assert response.status_code == 502
        detail = response.json().get("detail")
        assert isinstance(detail, str) and detail
        # The envelope carries ONLY the generic typed message: no upstream body,
        # fixed-host URL, or operator contact leaks.
        assert "not-a-gzip" not in response.text
        assert "eutils" not in response.text
        assert "operator@example.test" not in response.text
        assert "Traceback" not in response.text
    finally:
        _clear_overrides()


def test_discovery_fails_closed_on_a_candidate_from_a_different_provider(session):
    """A miswired driver must not silently degrade to an empty candidate list."""

    class _WrongProviderCapability:
        provider_key = FAKE_LITERATURE_PROVIDER_KEY
        kind = CapabilityKind.LITERATURE_DISCOVERY

        def search(self, query, limit, credentials):
            from revolab.capabilities import LiteratureCandidate, LiteratureSearchResult

            return LiteratureSearchResult(
                provider_key=FAKE_LITERATURE_PROVIDER_KEY,
                candidates=(
                    LiteratureCandidate(
                        provider_key="someone-else",
                        authority=FAKE_LITERATURE_AUTHORITY,
                        native_id="1",
                        title="Wrong provider",
                    ),
                ),
            )

        def resolve(self, authority, native_id, credentials):  # pragma: no cover - unused
            raise AssertionError("resolve must not be used")

    class _WrongProviderDriver:
        name = FAKE_LITERATURE_PROVIDER_KEY
        display_name = "Wrong provider"
        description = None
        authorities = (FAKE_LITERATURE_AUTHORITY,)
        required_credential_kinds: tuple[str, ...] = ()
        capabilities: ClassVar[dict] = {
            CapabilityKind.LITERATURE_DISCOVERY: _WrongProviderCapability()
        }

        def start(self, context):
            pass

        def stop(self):
            pass

        def probe_health(self):
            from revolab.enums import ProviderRuntimeHealth

            return ProviderRuntimeHealth.READY

    actor = _actor(session)
    project = _project(session, actor)
    with pytest.raises(ValidationError):
        _discover(session, _registry(_WrongProviderDriver()), actor, project)
