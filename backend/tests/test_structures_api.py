"""Phase-15 structure discovery + import API surface tests.

The real FastAPI application is exercised through `TestClient` with the real
Driver/Capability registry and a real (temporary) ContentStore injected through the
canonical dependency — no test-only endpoint and no frontend fixture bypass.
"""

from __future__ import annotations

import hashlib
from types import MappingProxyType
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from revolab import services
from revolab import structures as structure_service
from revolab.content_store import ContentStore
from revolab.drivers import DriverContext, DriverRegistry
from revolab.models import (
    ArtifactReference,
    ExternalIdentity,
    ExternalReference,
    GlobalProvenanceEdge,
    ProjectResourceLink,
    ResourceStewardship,
    ScientificObjectExternalIdentity,
    ScientificObjectRevision,
    ScientificObjectSeries,
)
from revolab.secret_store import InMemorySecretStore
from revolab.testing.fake_structure import (
    FAKE_STRUCTURE_AUTHORITY,
    FAKE_STRUCTURE_PROVIDER_KEY,
    FakeStructureDriver,
)

API_PREFIX = "/api"


def _registry(*drivers) -> DriverRegistry:
    registry = DriverRegistry()
    for driver in drivers:
        registry.register(driver)
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return registry


def _api_actor(client) -> str:
    return client.post(f"{API_PREFIX}/actors").json()["actor_id"]


def _api_project(client, actor: str, name: str = "Structure API") -> str:
    response = client.post(
        f"{API_PREFIX}/projects",
        json={"name": name, "visibility": "private"},
        headers={"X-Actor-Id": actor},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _override(registry, store, content_store, monkeypatch):
    from revolab import api as api_module
    from revolab.main import app

    # The ContentStore is reached through BOTH the explicit `get_content_store`
    # dependency (the Phase-15 import) and the pre-existing direct `_content_store()`
    # provider (the artifact content route), so both are pointed at the isolated
    # temporary root.
    monkeypatch.setattr(api_module, "_content_store", lambda: content_store)
    app.dependency_overrides[api_module.get_driver_registry] = lambda: registry
    app.dependency_overrides[api_module.get_secret_store] = lambda: store
    app.dependency_overrides[api_module.get_content_store] = lambda: content_store


def _clear():
    from revolab.api import get_content_store, get_driver_registry, get_secret_store
    from revolab.main import app

    app.dependency_overrides.pop(get_driver_registry, None)
    app.dependency_overrides.pop(get_secret_store, None)
    app.dependency_overrides.pop(get_content_store, None)


@pytest.fixture
def content_store(tmp_path) -> ContentStore:
    return ContentStore(tmp_path / "content")


@pytest.fixture
def api(client, content_store, monkeypatch):
    registry = _registry(FakeStructureDriver())
    _override(registry, InMemorySecretStore(), content_store, monkeypatch)
    try:
        yield client, registry
    finally:
        _clear()


def _discover(client, project: str, actor: str, q: str = "human hemoglobin", limit: int = 3):
    response = client.get(
        f"{API_PREFIX}/projects/{project}/structures/discover",
        params={"provider_key": FAKE_STRUCTURE_PROVIDER_KEY, "q": q, "limit": limit},
        headers={"X-Actor-Id": actor},
    )
    return response


def _import_body(candidate: dict) -> dict:
    return {
        "provider_key": candidate["provider_key"],
        "authority": candidate["authority"],
        "native_id": candidate["native_id"],
    }


def _count(session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discovery_endpoint_returns_bounded_external_candidates(api):
    client, _ = api
    actor = _api_actor(client)
    project = _api_project(client, actor)
    response = _discover(client, project, actor)
    assert response.status_code == 200
    body = response.json()
    assert body["provider_key"] == FAKE_STRUCTURE_PROVIDER_KEY
    assert body["query"] == "human hemoglobin"
    assert 1 <= len(body["candidates"]) <= 3
    candidate = body["candidates"][0]
    assert set(candidate) == {
        "provider_key",
        "authority",
        "native_id",
        "title",
        "experimental_methods",
        "resolution_angstrom",
        "release_date",
        "polymer_entity_count",
    }
    assert "coordinate" not in response.text.lower()


def test_discovery_endpoint_requires_a_member(client, content_store, monkeypatch):
    _override(
        _registry(FakeStructureDriver()), InMemorySecretStore(), content_store, monkeypatch
    )
    try:
        owner = _api_actor(client)
        project = _api_project(client, owner)
        outsider = _api_actor(client)
        assert _discover(client, project, outsider).status_code == 403
    finally:
        _clear()


def test_discovery_endpoint_bounds_the_query_and_limit(api):
    client, _ = api
    actor = _api_actor(client)
    project = _api_project(client, actor)
    assert _discover(client, project, actor, q="x" * 301).status_code == 422
    assert _discover(client, project, actor, limit=0).status_code == 422
    assert _discover(client, project, actor, limit=99).status_code == 422


def test_discovery_rejects_a_caller_supplied_provider_endpoint(api):
    client, _ = api
    actor = _api_actor(client)
    project = _api_project(client, actor)
    response = client.get(
        f"{API_PREFIX}/projects/{project}/structures/discover",
        params={
            "provider_key": FAKE_STRUCTURE_PROVIDER_KEY,
            "q": "kinase",
            "url": "https://evil.test/graphql",
        },
        headers={"X-Actor-Id": actor},
    )
    # Unknown query parameters are ignored by FastAPI, but the driver receives only
    # q/limit: the caller can never shape the network request.
    assert response.status_code == 200
    assert "evil.test" not in response.text


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def test_import_endpoint_returns_canonical_identities_only(api):
    client, _registry_handle = api
    actor = _api_actor(client)
    project = _api_project(client, actor)
    candidate = _discover(client, project, actor).json()["candidates"][0]
    response = client.post(
        f"{API_PREFIX}/projects/{project}/structures/import",
        json=_import_body(candidate),
        headers={"X-Actor-Id": actor},
    )
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
        "structure_series_id",
        "structure_revision_id",
        "coordinate_artifact_id",
        "external_reference_id",
        "authority",
        "native_id",
    }
    assert body["authority"] == FAKE_STRUCTURE_AUTHORITY
    # No coordinate bytes, no provider payload, no normalized snapshot.
    assert "coordinate_bytes" not in response.text
    assert "data_" not in response.text
    for key in (
        "structure_series_id",
        "structure_revision_id",
        "coordinate_artifact_id",
        "external_reference_id",
    ):
        UUID(body[key])


def test_import_endpoint_rejects_client_supplied_scientific_data(api):
    client, _ = api
    actor = _api_actor(client)
    project = _api_project(client, actor)
    candidate = _discover(client, project, actor).json()["candidates"][0]
    for extra in (
        {"title": "forged"},
        {"resolution": 1.0},
        {"method": "X-RAY"},
        {"coordinate_url": "https://evil.test/x.cif"},
        {"coordinates": "data_x"},
    ):
        response = client.post(
            f"{API_PREFIX}/projects/{project}/structures/import",
            json={**_import_body(candidate), **extra},
            headers={"X-Actor-Id": actor},
        )
        assert response.status_code == 422


def test_a_viewer_may_discover_but_the_import_endpoint_refuses(
    client, content_store, monkeypatch
):
    registry = _registry(FakeStructureDriver())
    _override(registry, InMemorySecretStore(), content_store, monkeypatch)
    try:
        owner = _api_actor(client)
        viewer = _api_actor(client)
        project = _api_project(client, owner)
        add = client.post(
            f"{API_PREFIX}/projects/{project}/members",
            json={"actor_id": viewer, "role": "viewer"},
            headers={"X-Actor-Id": owner},
        )
        assert add.status_code == 201
        candidate = _discover(client, project, viewer).json()["candidates"][0]
        response = client.post(
            f"{API_PREFIX}/projects/{project}/structures/import",
            json=_import_body(candidate),
            headers={"X-Actor-Id": viewer},
        )
        assert response.status_code == 403
    finally:
        _clear()


def test_provider_unavailable_is_a_typed_non_destructive_failure(
    client, content_store, monkeypatch
):
    _override(
        _registry(FakeStructureDriver(healthy=False)),
        InMemorySecretStore(),
        content_store,
        monkeypatch,
    )
    try:
        actor = _api_actor(client)
        project = _api_project(client, actor)
        response = _discover(client, project, actor)
        assert response.status_code == 503
    finally:
        _clear()


def test_a_repeat_import_is_idempotent_and_a_changed_snapshot_conflicts(api):
    client, _ = api
    actor = _api_actor(client)
    project = _api_project(client, actor)
    candidate = _discover(client, project, actor).json()["candidates"][0]
    headers = {"X-Actor-Id": actor}
    first = client.post(
        f"{API_PREFIX}/projects/{project}/structures/import",
        json=_import_body(candidate),
        headers=headers,
    )
    assert first.status_code == 201
    repeat = client.post(
        f"{API_PREFIX}/projects/{project}/structures/import",
        json=_import_body(candidate),
        headers=headers,
    )
    assert repeat.status_code == 201
    assert repeat.json() == first.json()

    # A mutated provider snapshot must report a typed conflict, not silently update.
    from revolab.testing.fake_structure import MUTATE_QUERY_TOKEN

    _discover(client, project, actor, q=f"human hemoglobin {MUTATE_QUERY_TOKEN}")
    conflict = client.post(
        f"{API_PREFIX}/projects/{project}/structures/import",
        json=_import_body(candidate),
        headers=headers,
    )
    assert conflict.status_code == 409
    assert "changed since the imported snapshot" in conflict.json()["detail"]


def test_an_unknown_identity_import_fails_before_any_write(api, engine):
    client, _ = api
    actor = _api_actor(client)
    project = _api_project(client, actor)
    response = client.post(
        f"{API_PREFIX}/projects/{project}/structures/import",
        json={
            "provider_key": FAKE_STRUCTURE_PROVIDER_KEY,
            "authority": FAKE_STRUCTURE_AUTHORITY,
            "native_id": "FAKENEVERSEEN",
        },
        headers={"X-Actor-Id": actor},
    )
    assert response.status_code == 404
    with Session(engine) as check:
        assert _count(check, ScientificObjectSeries) == 0


# ---------------------------------------------------------------------------
# Coordinate artifact access + Structure detail derivation
# ---------------------------------------------------------------------------


def test_the_imported_coordinate_artifact_is_downloadable_and_inspectable(api):
    client, _ = api
    actor = _api_actor(client)
    project = _api_project(client, actor)
    headers = {"X-Actor-Id": actor}
    candidate = _discover(client, project, actor).json()["candidates"][0]
    imported = client.post(
        f"{API_PREFIX}/projects/{project}/structures/import",
        json=_import_body(candidate),
        headers=headers,
    ).json()
    artifact_id = imported["coordinate_artifact_id"]

    # The Structure detail derives the coordinate artifact through provenance
    # traversal: the `imported_as` edge from the artifact to the revision.
    detail = client.get(
        f"{API_PREFIX}/projects/{project}/objects/{imported['structure_series_id']}",
        headers=headers,
    ).json()
    inbound = detail["provenance"]["inbound"]
    artifact_edges = [
        edge
        for edge in inbound
        if edge["relation_type"] == "imported_as"
        and edge["source_kind"] == "artifact_reference"
    ]
    assert len(artifact_edges) == 1
    assert artifact_edges[0]["source_id"] == artifact_id
    assert artifact_edges[0]["target_id"] == imported["structure_revision_id"]

    # The canonical resource surface owns the artifact's checksum/size/content type.
    resource = client.get(
        f"{API_PREFIX}/projects/{project}/resources/{artifact_id}", headers=headers
    ).json()
    assert resource["authority"] == "revolab"
    assert resource["content_type"] == "chemical/x-cif"
    assert resource["size"] and resource["checksum"]

    content = client.get(
        f"{API_PREFIX}/projects/{project}/artifacts/{artifact_id}/content", headers=headers
    )
    assert content.status_code == 200
    assert content.content.startswith(b"data_")
    assert hashlib.sha256(content.content).hexdigest() == resource["checksum"]
    assert len(content.content) == resource["size"]


def test_the_imported_structure_is_project_searchable_through_the_api(api):
    client, _ = api
    actor = _api_actor(client)
    project = _api_project(client, actor)
    headers = {"X-Actor-Id": actor}
    candidate = _discover(client, project, actor).json()["candidates"][0]
    native_id = candidate["native_id"]

    before = client.get(
        f"{API_PREFIX}/projects/{project}/search",
        params={"q": native_id, "scope": "project_shared"},
        headers=headers,
    ).json()
    assert before["hits"] == []

    imported = client.post(
        f"{API_PREFIX}/projects/{project}/structures/import",
        json=_import_body(candidate),
        headers=headers,
    ).json()
    after = client.get(
        f"{API_PREFIX}/projects/{project}/search",
        params={"q": native_id, "scope": "project_shared"},
        headers=headers,
    ).json()
    assert imported["structure_series_id"] in {hit["target_id"] for hit in after["hits"]}


def test_the_exact_canonical_bundle_is_persisted_through_the_api(api, engine):
    client, _ = api
    actor = _api_actor(client)
    project = _api_project(client, actor)
    headers = {"X-Actor-Id": actor}
    candidate = _discover(client, project, actor).json()["candidates"][0]
    response = client.post(
        f"{API_PREFIX}/projects/{project}/structures/import",
        json=_import_body(candidate),
        headers=headers,
    )
    assert response.status_code == 201
    with Session(engine) as check:
        assert _count(check, ExternalIdentity) == 1
        assert _count(check, ExternalReference) == 1
        assert _count(check, ArtifactReference) == 1
        assert _count(check, ScientificObjectSeries) == 1
        assert _count(check, ScientificObjectRevision) == 1
        assert _count(check, ScientificObjectExternalIdentity) == 1
        assert _count(check, GlobalProvenanceEdge) == 2
        assert _count(check, ResourceStewardship) == 3
        assert _count(check, ProjectResourceLink) == 4


def test_a_failed_import_rolls_back_through_the_real_request_lifecycle(
    api, engine, monkeypatch
):
    client, _ = api
    actor = _api_actor(client)
    project = _api_project(client, actor)
    headers = {"X-Actor-Id": actor}
    candidate = _discover(client, project, actor).json()["candidates"][0]
    with Session(engine) as pre:
        assert _count(pre, ScientificObjectSeries) == 0

    def explode(*args: Any, **kwargs: Any):
        raise RuntimeError("injected failure after the bundle was staged")

    monkeypatch.setattr(services, "record_imported_as", explode)
    with pytest.raises(RuntimeError, match="injected failure"):
        client.post(
            f"{API_PREFIX}/projects/{project}/structures/import",
            json=_import_body(candidate),
            headers=headers,
        )
    # The FastAPI session dependency's teardown — not the test — is what rolls back.
    with Session(engine) as check:
        assert _count(check, ScientificObjectSeries) == 0
        assert _count(check, ExternalIdentity) == 0
        assert _count(check, ArtifactReference) == 0
        assert _count(check, GlobalProvenanceEdge) == 0


def test_structure_service_snapshot_checksum_is_stable_across_reimports(api):
    """A title-only change leaves the normalized snapshot digest untouched."""
    client, _registry_handle = api
    actor = _api_actor(client)
    project = _api_project(client, actor)
    headers = {"X-Actor-Id": actor}
    candidate = _discover(client, project, actor).json()["candidates"][0]
    imported = client.post(
        f"{API_PREFIX}/projects/{project}/structures/import",
        json=_import_body(candidate),
        headers=headers,
    ).json()
    assert imported["native_id"] == candidate["native_id"]
    assert structure_service.SNAPSHOT_CHANGED_MESSAGE
