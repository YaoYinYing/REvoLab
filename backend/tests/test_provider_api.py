"""Phase-3 Provider Catalog + credential-management API surface."""

from collections.abc import Mapping
from types import MappingProxyType

from revolab.drivers import Capability, DriverContext, DriverRegistry
from revolab.enums import CapabilityKind, ProviderRuntimeHealth
from revolab.main import app

SENTINEL = "REVOLAB_SENTINEL_0f9e2a7c4b6d8e1f"


def _headers(actor_id: str) -> dict[str, str]:
    return {"X-Actor-Id": actor_id}


def _actor(client) -> str:
    return client.post("/api/actors").json()["actor_id"]


def _project(client, actor_id: str, name: str = "P") -> dict:
    return client.post("/api/projects", json={"name": name}, headers=_headers(actor_id)).json()


class _Capability:
    def __init__(self, provider_key: str, kind: CapabilityKind) -> None:
        self.provider_key = provider_key
        self.kind = kind


class _Driver:
    def __init__(
        self,
        name: str,
        kinds: tuple[str, ...],
        capability_kind: CapabilityKind = CapabilityKind.COMPUTE,
    ) -> None:
        self.name = name
        self.display_name = name.title()
        self.description = "Synthetic in-process driver."
        self.required_credential_kinds = kinds
        self.capabilities: Mapping[CapabilityKind, Capability] = {
            capability_kind: _Capability(name, capability_kind)
        }

    def start(self, context: DriverContext) -> None:
        pass

    def stop(self) -> None:
        pass

    def probe_health(self) -> ProviderRuntimeHealth:
        return ProviderRuntimeHealth.READY


def _started_registry() -> DriverRegistry:
    registry = DriverRegistry()
    registry.register(_Driver("stub", ("api_key", "org_token")))
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return registry


def _fakeprov_registry() -> DriverRegistry:
    registry = DriverRegistry()
    registry.register(_Driver("fakeprov", ("api_key",)))
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return registry


def test_empty_catalog_with_zero_providers(client):
    actor = _actor(client)
    project = _project(client, actor)
    response = client.get(f"/api/projects/{project['id']}/providers", headers=_headers(actor))
    assert response.status_code == 200
    assert response.json() == []


def test_catalog_is_project_scoped_and_requires_read_lens(client):
    owner = _actor(client)
    stranger = _actor(client)
    project = _project(client, owner)
    rejected = client.get(f"/api/projects/{project['id']}/providers", headers=_headers(stranger))
    assert rejected.status_code == 403


def test_catalog_entry_never_exposes_secret_material_or_secret_ref(client, secret_store):
    from revolab.api import get_driver_registry, get_secret_store

    owner = _actor(client)
    project = _project(client, owner)
    app.dependency_overrides[get_driver_registry] = _started_registry
    app.dependency_overrides[get_secret_store] = lambda: secret_store

    client.post(
        "/api/credentials",
        json={"provider_key": "stub", "kind": "api_key", "secret_value": SENTINEL},
        headers=_headers(owner),
    )

    catalog = client.get(f"/api/projects/{project['id']}/providers", headers=_headers(owner))
    assert catalog.status_code == 200
    entry = catalog.json()[0]
    assert entry["key"] == "stub"
    assert entry["health"] == "ready"
    assert entry["credential_presence"] == [
        {"kind": "api_key", "present": True},
        {"kind": "org_token", "present": False},
    ]
    # org_token still missing -> per-capability availability is CREDENTIAL_MISSING.
    assert entry["capabilities"] == [
        {"kind": "compute", "availability": "credential_missing"}
    ]
    assert SENTINEL not in catalog.text
    for key in ("secret_ref", "secret", "secret_value", "token"):
        assert key not in entry


def test_catalog_is_actor_contextual(client, secret_store):
    from revolab.api import get_driver_registry, get_secret_store

    owner = _actor(client)
    other = _actor(client)
    project = _project(client, owner)
    client.post(
        f"/api/projects/{project['id']}/members",
        json={"actor_id": other, "role": "member"},
        headers=_headers(owner),
    )
    app.dependency_overrides[get_driver_registry] = _started_registry
    app.dependency_overrides[get_secret_store] = lambda: secret_store

    client.post(
        "/api/credentials",
        json={"provider_key": "stub", "kind": "api_key", "secret_value": SENTINEL},
        headers=_headers(owner),
    )
    client.post(
        "/api/credentials",
        json={"provider_key": "stub", "kind": "org_token", "secret_value": "org-value"},
        headers=_headers(owner),
    )

    for_owner = client.get(f"/api/projects/{project['id']}/providers", headers=_headers(owner)).json()[0]
    for_other = client.get(f"/api/projects/{project['id']}/providers", headers=_headers(other)).json()[0]
    assert for_owner["credential_presence"] == [
        {"kind": "api_key", "present": True},
        {"kind": "org_token", "present": True},
    ]
    assert for_other["credential_presence"] == [
        {"kind": "api_key", "present": False},
        {"kind": "org_token", "present": False},
    ]
    assert for_owner["capabilities"][0]["availability"] == "available"
    assert for_other["capabilities"][0]["availability"] == "credential_missing"


def test_credential_lifecycle_api_never_echoes_secret(client, secret_store):
    from revolab.api import get_driver_registry, get_secret_store

    app.dependency_overrides[get_driver_registry] = _fakeprov_registry
    app.dependency_overrides[get_secret_store] = lambda: secret_store
    actor = _actor(client)

    created = client.post(
        "/api/credentials",
        json={"provider_key": "fakeprov", "kind": "api_key", "secret_value": SENTINEL},
        headers=_headers(actor),
    )
    assert created.status_code == 201
    assert created.json() == {"provider_key": "fakeprov", "kind": "api_key"}
    assert SENTINEL not in created.text

    listed = client.get("/api/credentials", headers=_headers(actor))
    assert listed.status_code == 200
    assert listed.json() == [{"provider_key": "fakeprov", "kind": "api_key"}]

    replaced = client.put(
        "/api/credentials/fakeprov/api_key",
        json={"secret_value": "rotated-value"},
        headers=_headers(actor),
    )
    assert replaced.status_code == 200
    assert replaced.json() == {"provider_key": "fakeprov", "kind": "api_key"}

    deleted = client.delete("/api/credentials/fakeprov/api_key", headers=_headers(actor))
    assert deleted.status_code == 204
    assert client.get("/api/credentials", headers=_headers(actor)).json() == []


def test_unknown_provider_and_unknown_kind_rejected(client, secret_store):
    from revolab.api import get_driver_registry, get_secret_store

    app.dependency_overrides[get_driver_registry] = _fakeprov_registry
    app.dependency_overrides[get_secret_store] = lambda: secret_store
    actor = _actor(client)

    unknown_provider = client.post(
        "/api/credentials",
        json={"provider_key": "nope", "kind": "api_key", "secret_value": SENTINEL},
        headers=_headers(actor),
    )
    assert unknown_provider.status_code == 404

    unknown_kind = client.post(
        "/api/credentials",
        json={"provider_key": "fakeprov", "kind": "api-key", "secret_value": SENTINEL},
        headers=_headers(actor),
    )
    assert unknown_kind.status_code == 422
    assert SENTINEL not in unknown_kind.text


def test_duplicate_create_returns_conflict_without_echo(client, secret_store):
    from revolab.api import get_driver_registry, get_secret_store

    app.dependency_overrides[get_driver_registry] = _fakeprov_registry
    app.dependency_overrides[get_secret_store] = lambda: secret_store
    actor = _actor(client)
    body = {"provider_key": "fakeprov", "kind": "api_key", "secret_value": SENTINEL}
    assert client.post("/api/credentials", json=body, headers=_headers(actor)).status_code == 201
    duplicate = client.post("/api/credentials", json=body, headers=_headers(actor))
    assert duplicate.status_code == 409
    assert SENTINEL not in duplicate.text


def test_validation_error_keeps_http_validation_shape_and_never_echoes_secret(client, secret_store):
    from revolab.api import get_driver_registry, get_secret_store

    app.dependency_overrides[get_driver_registry] = _fakeprov_registry
    app.dependency_overrides[get_secret_store] = lambda: secret_store
    actor = _actor(client)
    invalid = client.post(
        "/api/credentials",
        json={"provider_key": "Bad Key!", "kind": "api_key", "secret_value": SENTINEL},
        headers=_headers(actor),
    )
    assert invalid.status_code == 422
    # Wire contract stays FastAPI's HTTPValidationError: detail is an error
    # array. Only the secret-bearing per-error `input`/`ctx` are stripped.
    detail = invalid.json()["detail"]
    assert isinstance(detail, list)
    assert all("input" not in error and "ctx" not in error for error in detail)
    assert SENTINEL not in invalid.text


def test_credential_queries_are_scoped_by_the_trusted_actor_seam(client, secret_store):
    # X-Actor-Id is the trusted header seam, NOT authentication (OIDC deferred).
    # This proves query/operation SCOPING, not anti-impersonation.
    from revolab.api import get_driver_registry, get_secret_store

    app.dependency_overrides[get_driver_registry] = _fakeprov_registry
    app.dependency_overrides[get_secret_store] = lambda: secret_store
    alice = _actor(client)
    bob = _actor(client)
    client.post(
        "/api/credentials",
        json={"provider_key": "fakeprov", "kind": "api_key", "secret_value": SENTINEL},
        headers=_headers(alice),
    )

    assert client.get("/api/credentials", headers=_headers(bob)).json() == []
    assert client.put(
        "/api/credentials/fakeprov/api_key",
        json={"secret_value": "steal"},
        headers=_headers(bob),
    ).status_code == 404
    assert client.delete("/api/credentials/fakeprov/api_key", headers=_headers(bob)).status_code == 404
    # Alice's binding and material are untouched.
    assert client.get("/api/credentials", headers=_headers(alice)).json() == [
        {"provider_key": "fakeprov", "kind": "api_key"}
    ]
    assert any(material == SENTINEL for material in secret_store._material.values())


def test_sentinel_absent_from_captured_logs(client, secret_store, caplog):
    from revolab.api import get_driver_registry, get_secret_store

    app.dependency_overrides[get_driver_registry] = _fakeprov_registry
    app.dependency_overrides[get_secret_store] = lambda: secret_store
    actor = _actor(client)
    with caplog.at_level("DEBUG"):
        client.post(
            "/api/credentials",
            json={"provider_key": "fakeprov", "kind": "api_key", "secret_value": SENTINEL},
            headers=_headers(actor),
        )
        client.get("/api/credentials", headers=_headers(actor))
        client.put(
            "/api/credentials/fakeprov/api_key",
            json={"secret_value": "rotated-value"},
            headers=_headers(actor),
        )
        client.delete("/api/credentials/fakeprov/api_key", headers=_headers(actor))
    assert SENTINEL not in caplog.text
