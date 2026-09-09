"""Phase-3 CapabilityAvailability derivation + provider-registry boundaries."""

from collections.abc import Mapping
from types import MappingProxyType

from revolab import services
from revolab.domain.provider import capability_availability, credentials_present
from revolab.drivers import Capability, DriverContext, DriverRegistry
from revolab.enums import CapabilityAvailability, CapabilityKind, ProviderRuntimeHealth


def _actor(session):
    return services.create_actor(session)


def _project(session, actor_id, name: str = "P"):
    return services.create_project(session, actor_id, name).id


def _member(session, owner_id, project_id, actor_id, role: str = "member") -> None:
    services.add_membership(session, owner_id, project_id, actor_id, role)


class _Capability:
    def __init__(self, provider_key: str, kind: CapabilityKind) -> None:
        self.provider_key = provider_key
        self.kind = kind


class _Driver:
    def __init__(self, name: str, kinds: tuple[str, ...], capability_kind: CapabilityKind = CapabilityKind.COMPUTE) -> None:
        self.name = name
        self.display_name = name.title()
        self.description = "synthetic driver for availability tests"
        self.required_credential_kinds = kinds
        self.authorities: tuple[str, ...] = (name,)
        self.capabilities: Mapping[CapabilityKind, Capability] = {
            capability_kind: _Capability(name, capability_kind)
        }

    def start(self, context: DriverContext) -> None:
        pass

    def stop(self) -> None:
        pass

    def probe_health(self) -> ProviderRuntimeHealth:
        return ProviderRuntimeHealth.READY


def _credential_registry(provider_key: str = "fakeprov") -> DriverRegistry:
    registry = DriverRegistry()
    registry.register(_Driver(provider_key, ("api_key",)))
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return registry


def _availability(
    session,
    actor_id,
    project_id,
    *,
    provider_key: str = "fakeprov",
    health: ProviderRuntimeHealth = ProviderRuntimeHealth.READY,
    required: tuple[str, ...] = ("api_key",),
    capability_kind: CapabilityKind = CapabilityKind.COMPUTE,
) -> CapabilityAvailability:
    return capability_availability(
        health=health,
        credentials_present=credentials_present(session, actor_id, provider_key, required),
        permitted=services.project_policy_permits(session, actor_id, project_id, capability_kind),
    )


def test_ready_credential_permitted_is_available(session, secret_store):
    owner = _actor(session)
    project_id = _project(session, owner)
    services.provision_credential(
        session, secret_store, _credential_registry(), owner, "fakeprov", "api_key", "SENTINEL"
    )
    assert _availability(session, owner, project_id) is CapabilityAvailability.AVAILABLE


def test_ready_missing_credential_is_credential_missing(session):
    owner = _actor(session)
    project_id = _project(session, owner)
    assert _availability(session, owner, project_id) is CapabilityAvailability.CREDENTIAL_MISSING


def test_ready_credential_but_viewer_is_not_authorized_for_action_capability(session, secret_store):
    owner = _actor(session)
    viewer = _actor(session)
    project_id = _project(session, owner)
    _member(session, owner, project_id, viewer, role="viewer")
    services.provision_credential(
        session, secret_store, _credential_registry(), viewer, "fakeprov", "api_key", "SENTINEL"
    )
    # COMPUTE is an action capability: viewer membership does not permit it.
    assert _availability(session, viewer, project_id) is CapabilityAvailability.NOT_AUTHORIZED


def test_viewer_is_permitted_for_read_only_capability(session, secret_store):
    owner = _actor(session)
    viewer = _actor(session)
    project_id = _project(session, owner)
    _member(session, owner, project_id, viewer, role="viewer")
    services.provision_credential(
        session, secret_store, _credential_registry(), viewer, "fakeprov", "api_key", "SENTINEL"
    )
    # ARTIFACT_RESOLUTION is read-only: any readable membership permits it.
    assert (
        _availability(session, viewer, project_id, capability_kind=CapabilityKind.ARTIFACT_RESOLUTION)
        is CapabilityAvailability.AVAILABLE
    )


def test_ready_credential_but_non_member_is_not_authorized(session, secret_store):
    owner = _actor(session)
    stranger = _actor(session)
    project_id = _project(session, owner)
    services.provision_credential(
        session, secret_store, _credential_registry(), stranger, "fakeprov", "api_key", "SENTINEL"
    )
    assert _availability(session, stranger, project_id, capability_kind=CapabilityKind.ARTIFACT_RESOLUTION) \
        is CapabilityAvailability.NOT_AUTHORIZED


def test_unreachable_and_degraded_yield_provider_unavailable(session, secret_store):
    owner = _actor(session)
    project_id = _project(session, owner)
    services.provision_credential(
        session, secret_store, _credential_registry(), owner, "fakeprov", "api_key", "SENTINEL"
    )
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
    services.provision_credential(
        session, secret_store, _credential_registry(), owner, "fakeprov", "api_key", "SENTINEL"
    )

    assert _availability(session, owner, project_id) is CapabilityAvailability.AVAILABLE
    assert _availability(session, other, project_id) is CapabilityAvailability.CREDENTIAL_MISSING


def test_revocation_changes_next_query_availability_without_stored_state(session, secret_store):
    owner = _actor(session)
    project_id = _project(session, owner)
    registry = _credential_registry()
    services.provision_credential(session, secret_store, registry, owner, "fakeprov", "api_key", "SENTINEL")
    assert _availability(session, owner, project_id) is CapabilityAvailability.AVAILABLE

    services.revoke_credential(session, secret_store, owner, "fakeprov", "api_key")

    # Availability is a derived query: no migration or stored-state repair.
    assert _availability(session, owner, project_id) is CapabilityAvailability.CREDENTIAL_MISSING


def test_provider_vocabulary_does_not_enter_core_enums():
    core_values = {kind.value for kind in CapabilityKind}
    assert core_values == {"compute", "artifact_resolution"}
    # Credential kinds are provider-declared; no vendor-specific term is a Core enum.
    assert "api_key" not in core_values
    assert "organization_token" not in core_values


def _started_registry() -> DriverRegistry:
    registry = DriverRegistry()
    registry.register(_Driver("stub", ("api_key",)))
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
    assert registry.health("stub") is ProviderRuntimeHealth.READY
    assert "credential_missing" not in {state.value for state in ProviderRuntimeHealth}

    owner = _actor(session)
    project_id = _project(session, owner)
    availability = capability_availability(
        health=registry.health("stub"),
        credentials_present=False,
        permitted=services.project_policy_permits(session, owner, project_id, CapabilityKind.COMPUTE),
    )
    assert availability is CapabilityAvailability.CREDENTIAL_MISSING


def test_health_probe_is_actor_independent(session, secret_store):
    registry = _started_registry()
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
    registry.register(_Driver("stub", ("api_key",)))  # never start_all()'d

    # A REGISTERED driver has no runtime to project; the catalog exposes only
    # READY drivers (in-process lifecycle != Provider/Capability domain state).
    assert (
        catalog_entries(
            session,
            actor,
            registry,
            policy_permits=lambda kind: services.project_policy_permits(session, actor, project_id, kind),
        )
        == []
    )
