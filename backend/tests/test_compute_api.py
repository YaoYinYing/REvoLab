"""Phase-4 compute HTTP API vertical slice (fake in-process provider boundary).

The provider used here is provider-neutral (`fakecompute`); the API must stay
unable to name any REvoCompute task/runner vocabulary.
"""

from __future__ import annotations

from types import MappingProxyType

from revolab.drivers import DriverContext, DriverRegistry
from revolab.main import app
from revolab.testing.fake_compute import FakeComputeDriver

SENTINEL = "REVOLAB_API_SENTINEL_9f8e7d6c5b4a3210"


def _headers(actor_id: str) -> dict[str, str]:
    return {"X-Actor-Id": actor_id}


def _actor(client) -> str:
    return client.post("/api/actors").json()["actor_id"]


def _project(client, actor_id: str) -> dict:
    return client.post("/api/projects", json={"name": "P"}, headers=_headers(actor_id)).json()


def _object(client, actor_id: str, project_id: str) -> dict:
    return client.post(
        f"/api/projects/{project_id}/objects",
        json={"object_type": "sequence", "name": "obj", "payload": {"sequence": "MEEPQ"}},
        headers=_headers(actor_id),
    ).json()


def _registry(*, healthy: bool = True, required: tuple[str, ...] = ()) -> DriverRegistry:
    registry = DriverRegistry()
    driver = FakeComputeDriver()
    driver.required_credential_kinds = required
    if not healthy:

        def unhealthy():
            from revolab.enums import ProviderRuntimeHealth

            return ProviderRuntimeHealth.UNREACHABLE

        driver.probe_health = unhealthy  # type: ignore[method-assign]
    registry.register(driver)
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return registry


def _override_registry(client, registry: DriverRegistry) -> None:
    from revolab.api import get_driver_registry, get_secret_store
    from revolab.secret_store import InMemorySecretStore

    app.dependency_overrides[get_driver_registry] = lambda: registry
    app.dependency_overrides[get_secret_store] = lambda: InMemorySecretStore()


def test_task_kind_discovery_and_schema(client) -> None:
    _override_registry(client, _registry())
    actor = _actor(client)
    project = _project(client, actor)

    listing = client.get(
        f"/api/projects/{project['id']}/providers/fakecompute/compute/task-kinds",
        headers=_headers(actor),
    )
    assert listing.status_code == 200
    assert [item["kind_id"] for item in listing.json()] == ["echo"]

    schema = client.get(
        f"/api/projects/{project['id']}/providers/fakecompute/compute/task-kinds/echo/schema",
        headers=_headers(actor),
    )
    assert schema.status_code == 200
    assert schema.json()["parameter_schema"]["type"] == "object"


def test_submit_status_artifacts_resolve_flow(client) -> None:
    _override_registry(client, _registry())
    actor = _actor(client)
    project = _project(client, actor)
    obj = _object(client, actor, project["id"])
    revision_id = obj["visible_revisions"][0]["revision_id"]

    submitted = client.post(
        f"/api/projects/{project['id']}/compute/submissions",
        json={
            "provider_key": "fakecompute",
            "task_kind": "echo",
            "inputs": [{"kind": "scientific_object_revision", "resource_id": revision_id}],
            "params": {"message": "hello"},
        },
        headers=_headers(actor),
    )
    assert submitted.status_code == 201, submitted.text
    run = submitted.json()
    assert run["authority"] == "fakecompute"
    assert run["native_id"].startswith("fake-")

    status = client.get(
        f"/api/projects/{project['id']}/runs/{run['run_resource_id']}/status",
        headers=_headers(actor),
    )
    assert status.status_code == 200
    assert status.json()["available"] is True
    assert status.json()["status"] == "finished"

    artifacts = client.post(
        f"/api/projects/{project['id']}/runs/{run['run_resource_id']}/artifacts",
        headers=_headers(actor),
    )
    assert artifacts.status_code == 201, artifacts.text
    artifact = artifacts.json()[0]
    assert artifact["authority"] == "fakecompute"

    resolved = client.get(
        f"/api/projects/{project['id']}/artifacts/{artifact['resource_id']}/resolve",
        headers=_headers(actor),
    )
    assert resolved.status_code == 200
    assert resolved.content.startswith(b"hello")


def test_provider_outage_keeps_reference_and_reports_unavailable(client) -> None:
    _override_registry(client, _registry())
    actor = _actor(client)
    project = _project(client, actor)
    obj = _object(client, actor, project["id"])
    revision_id = obj["visible_revisions"][0]["revision_id"]
    submitted = client.post(
        f"/api/projects/{project['id']}/compute/submissions",
        json={"provider_key": "fakecompute", "task_kind": "echo",
              "inputs": [{"kind": "scientific_object_revision", "resource_id": revision_id}], "params": {}},
        headers=_headers(actor),
    ).json()

    # Flip the provider to UNREACHABLE; stored reference must survive.
    _override_registry(client, _registry(healthy=False))
    status = client.get(
        f"/api/projects/{project['id']}/runs/{submitted['run_resource_id']}/status",
        headers=_headers(actor),
    )
    assert status.status_code == 200
    assert status.json()["available"] is False

    resources = client.get(
        f"/api/projects/{project['id']}/resources",
        params={"resource_kind": "run_reference"},
        headers=_headers(actor),
    )
    assert any(item["resource_id"] == submitted["run_resource_id"] for item in resources.json())


def test_viewer_cannot_submit(client) -> None:
    _override_registry(client, _registry())
    owner = _actor(client)
    project = _project(client, owner)
    obj = _object(client, owner, project["id"])
    revision_id = obj["visible_revisions"][0]["revision_id"]
    viewer = _actor(client)
    client.post(
        f"/api/projects/{project['id']}/members",
        json={"actor_id": viewer, "role": "viewer"},
        headers=_headers(owner),
    )

    response = client.post(
        f"/api/projects/{project['id']}/compute/submissions",
        json={"provider_key": "fakecompute", "task_kind": "echo",
              "inputs": [{"kind": "scientific_object_revision", "resource_id": revision_id}], "params": {}},
        headers=_headers(viewer),
    )
    assert response.status_code == 403


def test_missing_credential_blocks_submission(client) -> None:
    _override_registry(client, _registry(required=("api_key",)))
    actor = _actor(client)
    project = _project(client, actor)
    obj = _object(client, actor, project["id"])
    revision_id = obj["visible_revisions"][0]["revision_id"]

    response = client.post(
        f"/api/projects/{project['id']}/compute/submissions",
        json={"provider_key": "fakecompute", "task_kind": "echo",
              "inputs": [{"kind": "scientific_object_revision", "resource_id": revision_id}], "params": {}},
        headers=_headers(actor),
    )
    assert response.status_code == 403
    assert SENTINEL not in response.text


def test_compute_params_are_not_persisted_as_revolab_truth(client, session) -> None:
    _override_registry(client, _registry())
    actor = _actor(client)
    project = _project(client, actor)
    obj = _object(client, actor, project["id"])
    revision_id = obj["visible_revisions"][0]["revision_id"]

    submitted = client.post(
        f"/api/projects/{project['id']}/compute/submissions",
        json={"provider_key": "fakecompute", "task_kind": "echo",
              "inputs": [{"kind": "scientific_object_revision", "resource_id": revision_id}],
              "params": {"message": SENTINEL}},
        headers=_headers(actor),
    )
    assert submitted.status_code == 201
    assert SENTINEL not in submitted.text
    run_resource_id = submitted.json()["run_resource_id"]

    # REvoLab persists only RunReference/ArtifactReference identity cards: the
    # provider parameter vocabulary is never copied into Core.
    from uuid import UUID

    from revolab.models import RunReference

    run = session.get(RunReference, UUID(run_resource_id))
    assert run is not None
    identity_state = [run.authority, run.native_id, run.task_type or "", run.input_parameter_digest or ""]
    assert not any(SENTINEL in value for value in identity_state)
