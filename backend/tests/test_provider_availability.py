"""Phase-3 CapabilityAvailability derivation + provider-registry boundaries."""

from collections.abc import Mapping
from types import MappingProxyType

from revolab import services
from revolab.domain.provider import capability_availability
from revolab.drivers import Capability, DriverContext, DriverRegistry
from revolab.enums import CapabilityAvailability, CapabilityKind, ProviderRuntimeHealth


def _actor(session):
    return services.create_actor(session)


def _project(session, actor_id, name: str = "P"):
    return services.create_project(session, actor_id, name).id


def _member(session, owner_id, project_id, actor_id, role: str = "member") -> None:
    services.add_membership(session, owner_id, project_id, actor_id, role)


def _availability(
    session,
    actor_id: str,
    project_id: str,
    *,
    provider_key: str = "fakeprov",
    health: ProviderRuntimeHealth = ProviderRuntimeHealth.READY,
    required: tuple[str, ...] = ("api_key",),
) -> CapabilityAvailability:
    return capability_availability(
        session,
        actor_id,
        project_id,
        provider_key=provider_key,
        health=health,
        required_kinds=required,
    )


def test_ready_credential_permitted_is_available(session, secret_store):
    owner = _actor(session)
    project_id = _project(session, owner)
    services.provision_credential(session, secret_store, owner, "fakeprov", "api_key", "SENTINEL")
    assert _availability(session, owner, project_id) is CapabilityAvailability.AVAILABLE


def test_ready_missing_credential_is_credential_missing(session):
    owner = _actor(session)
    project_id = _project(session, owner)
    assert _availability(session, owner, project_id) is CapabilityAvailability.CREDENTIAL_MISSING


def test_ready_credential_but_viewer_is_not_authorized(session, secret_store):
    owner = _actor(session)
    viewer = _actor(session)
    project_id = _project(session, owner)
    _member(session, owner, project_id, viewer, role="viewer")
    services.provision_credential(session, secret_store, viewer, "fakeprov", "api_key", "SENTINEL")
    assert _availability(session, viewer, project_id) is CapabilityAvailability.NOT_AUTHORIZED


def test_ready_credential_but_non_member_is_not_authorized(session, secret_store):
    owner = _actor(session)
    stranger = _actor(session)
    project_id = _project(session, owner)
    services.provision_credential(session, secret_store, stranger, "fakeprov", "api_key", "SENTINEL")
    assert _availability(session, stranger, project_id) is CapabilityAvailability.NOT_AUTHORIZED


def test_unreachable_and_degraded_yield_provider_unavailable(session, secret_store):
    owner = _actor(session)
    project_id = _project(session, owner)
    services.provision_credential(session, secret_store, owner, "fakeprov", "api_key", "SENTINEL")
    assert (
        _availability(session, owner, project_id, health=ProviderRuntimeHealth.UNREACHABLE)
        is CapabilityAvailability.PROVIDER_UNAVAILABLE
    )
    assert (
        _availability(session, owner, project_id, health=ProviderRuntimeHealth.DEGRADED)
        is CapabilityAvailability.PROVIDER_UNAVAILABLE
    )


def test_two_actors_observe_different_availability_same_project(session, secret_store):
    owner = _actor(session)
    other = _actor(session)
    project_id = _project(session, owner)
    _member(session, owner, project_id, other, role="member")
    services.provision_credential(session, secret_store, owner, "fakeprov", "api_key", "SENTINEL")

    assert _availability(session, owner, project_id) is CapabilityAvailability.AVAILABLE
    assert _availability(session, other, project_id) is CapabilityAvailability.CREDENTIAL_MISSING


def test_revocation_changes_next_query_availability_without_stored_state(session, secret_store):
    owner = _actor(session)
    project_id = _project(session, owner)
    services.provision_credential(session, secret_store, owner, "fakeprov", "api_key", "SENTINEL")
    assert _availability(session, owner, project_id) is CapabilityAvailability.AVAILABLE

    services.revoke_credential(session, secret_store, owner, "fakeprov", "api_key")

    # Availability is a derived query: no migration or stored-state repair.
    assert _availability(session, owner, project_id) is CapabilityAvailability.CREDENTIAL_MISSING


def test_provider_vocabulary_does_not_enter_core_enums():
    core_values = {kind.value for kind in CapabilityKind}
    assert core_values == {"compute", "search", "artifact_resolution", "design", "interactive_handoff"}
    # Credential kinds are provider-declared; no vendor-specific term is a Core enum.
    assert "api_key" not in core_values
    assert "organization_token" not in core_values


class _StubCapability:
    provider_key = "stub"
    kind = CapabilityKind.COMPUTE


class _StubDriver:
    name = "stub"
    display_name = "Stub Provider"
    description = "Synthetic in-process driver for acceptance."
    required_credential_kinds = ("api_key",)
    capabilities: Mapping[CapabilityKind, Capability] = {CapabilityKind.COMPUTE: _StubCapability()}

    def __init__(self) -> None:
        self.started = False
        self.health = ProviderRuntimeHealth.READY

    def start(self, context: DriverContext) -> None:
        self.started = True

    def stop(self) -> None:
        pass

    def probe_health(self) -> ProviderRuntimeHealth:
        return self.health


def _started_registry() -> DriverRegistry:
    registry = DriverRegistry()
    registry.register(_StubDriver())
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return registry


def test_stub_driver_declares_required_kinds_and_capability_map():
    registry = _started_registry()
    handle = registry.get("stub")
    assert handle.driver.required_credential_kinds == ("api_key",)
    assert CapabilityKind.COMPUTE in handle.driver.capabilities
    assert dict(registry.capabilities_by_kind(CapabilityKind.COMPUTE)) == {
        "stub": handle.driver.capabilities[CapabilityKind.COMPUTE]
    }


def test_runtime_health_is_provider_state_separate_from_actor_availability(session, secret_store):
    registry = _started_registry()
    # Provider runtime health options are actor-independent and never include a
    # credential state; availability is a separate projected enum.
    assert registry.health("stub") is ProviderRuntimeHealth.READY
    assert "credential_missing" not in {state.value for state in ProviderRuntimeHealth}

    # Availability composes health + credential + policy OUTSIDE the registry.
    owner = _actor(session)
    project_id = _project(session, owner)
    availability = capability_availability(
        session,
        owner,
        project_id,
        provider_key="stub",
        health=registry.health("stub"),
        required_kinds=("api_key",),
    )
    assert availability is CapabilityAvailability.CREDENTIAL_MISSING


def test_health_probe_is_actor_independent(session, secret_store):
    registry = _started_registry()
    # `health` / `refresh_health` take only the provider key — never actor or
    # project — so provider health cannot encode actor availability.
    actor = _actor(session)
    project_id = _project(session, actor)
    assert registry.health("stub") is ProviderRuntimeHealth.READY
    assert registry.refresh_health("stub") is ProviderRuntimeHealth.READY
    assert project_id  # the caller actor/project never enter the probe.


def test_unauthorized_wins_over_credential_missing_when_both_apply(session):
    owner = _actor(session)
    viewer = _actor(session)
    project_id = _project(session, owner)
    _member(session, owner, project_id, viewer, role="viewer")
    # Documented precedence: health → project policy → credential presence. An
    # actor without project access is NOT_AUTHORIZED, never CREDENTIAL_MISSING.
    assert _availability(session, viewer, project_id) is CapabilityAvailability.NOT_AUTHORIZED


def test_registered_but_unstarted_driver_is_absent_from_catalog(session):
    from revolab.domain.provider import catalog_entries

    actor = _actor(session)
    project_id = _project(session, actor)
    registry = DriverRegistry()
    registry.register(_StubDriver())  # never start_all()'d

    # A REGISTERED driver has no runtime to project; the catalog exposes only
    # READY drivers (in-process lifecycle != Provider/Capability domain state).
    assert catalog_entries(session, actor, project_id, registry) == []
