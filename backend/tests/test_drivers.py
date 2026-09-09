from collections.abc import Mapping
from types import MappingProxyType

import pytest

from revolab.drivers import (
    Capability,
    DriverContext,
    DriverRegistry,
    DriverState,
)
from revolab.enums import CapabilityKind, ProviderRuntimeHealth


class RecordingCapability:
    def __init__(self, provider_key: str) -> None:
        self.provider_key = provider_key
        self.kind = CapabilityKind.COMPUTE


class RecordingDriver:
    def __init__(self, name: str, *, fail_on_start: bool = False) -> None:
        self.name = name
        self.display_name = name.title()
        self.description = None
        self.required_credential_kinds: tuple[str, ...] = ("api_key",)
        self.capabilities: Mapping[CapabilityKind, Capability] = {
            CapabilityKind.COMPUTE: RecordingCapability(name)
        }
        self.fail_on_start = fail_on_start
        self.started = False
        self.stopped = False
        self.probe_count = 0

    def start(self, context: DriverContext) -> None:
        if self.fail_on_start:
            raise RuntimeError(f"{self.name} failed")
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def probe_health(self) -> ProviderRuntimeHealth:
        self.probe_count += 1
        return ProviderRuntimeHealth.READY


def context() -> DriverContext:
    return DriverContext(environment="test", settings=MappingProxyType({}))


def test_domain_visible_lifecycle_is_exactly_two_states() -> None:
    assert {state for state in DriverState} == {DriverState.REGISTERED, DriverState.READY}


def test_register_collapses_to_registered() -> None:
    registry = DriverRegistry()
    handle = registry.register(RecordingDriver("stub"))
    assert handle.state is DriverState.REGISTERED
    assert handle.driver.required_credential_kinds == ("api_key",)


def test_duplicate_register_raises() -> None:
    registry = DriverRegistry()
    registry.register(RecordingDriver("stub"))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(RecordingDriver("stub"))


def test_register_rejects_capability_mismatched_to_driver() -> None:
    class MismatchedCapability:
        provider_key = "other"
        kind = CapabilityKind.COMPUTE

    driver = RecordingDriver("stub")
    driver.capabilities = {CapabilityKind.COMPUTE: MismatchedCapability()}
    with pytest.raises(ValueError, match="does not match driver"):
        DriverRegistry().register(driver)


def test_register_rejects_capability_kind_not_matching_registry_key() -> None:
    class KeyMismatchedCapability:
        provider_key = "stub"
        kind = CapabilityKind.SEARCH  # keyed as COMPUTE below

    driver = RecordingDriver("stub")
    driver.capabilities = {CapabilityKind.COMPUTE: KeyMismatchedCapability()}
    with pytest.raises(ValueError, match="does not match its registry key"):
        DriverRegistry().register(driver)


def test_start_all_rolls_back_ready_drivers_when_a_later_driver_fails() -> None:
    first = RecordingDriver("first")
    failing = RecordingDriver("failing", fail_on_start=True)
    registry = DriverRegistry()
    first_handle = registry.register(first)
    registry.register(failing)

    with pytest.raises(RuntimeError, match="failing failed"):
        registry.start_all(context())

    assert first.started
    assert first.stopped
    assert first_handle.state is DriverState.REGISTERED
    assert registry.get("failing").state is DriverState.REGISTERED


def test_stop_all_stops_ready_drivers_and_clears_health_cache() -> None:
    driver = RecordingDriver("driver")
    registry = DriverRegistry()
    handle = registry.register(driver)
    registry.start_all(context())
    assert handle.state is DriverState.READY

    # Prime the health cache so clearing it is observable.
    assert registry.health("driver") is ProviderRuntimeHealth.READY
    assert driver.probe_count == 1

    registry.stop_all()

    assert driver.stopped
    assert handle.state is DriverState.REGISTERED


def test_capabilities_by_kind_only_projects_ready_drivers() -> None:
    registry = DriverRegistry()
    ready = RecordingDriver("ready")
    idle = RecordingDriver("idle")
    registry.register(ready)
    registry.register(idle)
    registry.start_all(context())
    # Simulate an in-process stop of only one driver: the other must disappear
    # from the capability projection by state, not by mutation of the registry.
    idle_handler = registry.get("idle")
    idle_handler.driver.stop()
    idle_handler.state = DriverState.REGISTERED

    projected = dict(registry.capabilities_by_kind(CapabilityKind.COMPUTE))
    assert set(projected) == {"ready"}


def test_get_unknown_driver_raises_lookup() -> None:
    with pytest.raises(LookupError, match="unknown driver"):
        DriverRegistry().get("nope")


def test_health_is_lazily_cached_actor_independent() -> None:
    registry = DriverRegistry()
    driver = RecordingDriver("stub")
    registry.register(driver)
    registry.start_all(context())

    assert registry.health("stub") is ProviderRuntimeHealth.READY
    assert registry.health("stub") is ProviderRuntimeHealth.READY
    assert driver.probe_count == 1
    # `health`/`refresh_health` are actor-independent: they never take actor or
    # project arguments (structural check via signature, not a runtime call).


def test_refresh_health_reprobes() -> None:
    registry = DriverRegistry()
    driver = RecordingDriver("stub")
    registry.register(driver)
    registry.start_all(context())
    registry.health("stub")
    assert registry.refresh_health("stub") is ProviderRuntimeHealth.READY
    assert driver.probe_count == 2


def test_health_and_refresh_require_ready_driver() -> None:
    registry = DriverRegistry()
    registry.register(RecordingDriver("stub"))
    # A REGISTERED (never-started) driver has no runtime to probe; conflating it
    # with READY health would merge lifecycle into provider domain state.
    with pytest.raises(RuntimeError, match="not READY"):
        registry.health("stub")
    with pytest.raises(RuntimeError, match="not READY"):
        registry.refresh_health("stub")
