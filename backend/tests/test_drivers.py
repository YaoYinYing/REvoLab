from types import MappingProxyType

import pytest

from revolab.drivers import DriverContext, DriverRegistry, DriverState


class RecordingDriver:
    def __init__(self, name: str, *, fail_on_start: bool = False) -> None:
        self.name = name
        self.fail_on_start = fail_on_start
        self.started = False
        self.stopped = False

    def capabilities(self) -> set[str]:
        return set()

    def start(self, context: DriverContext) -> None:
        if self.fail_on_start:
            raise RuntimeError(f"{self.name} failed")
        self.started = True

    def stop(self) -> None:
        self.stopped = True


def context() -> DriverContext:
    return DriverContext(environment="test", settings=MappingProxyType({}))


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
    assert first_handle.state is DriverState.STOPPED
    assert registry.get("failing").state is DriverState.VALIDATED


def test_stop_all_stops_started_and_ready_drivers() -> None:
    driver = RecordingDriver("driver")
    registry = DriverRegistry()
    handle = registry.register(driver)
    handle.state = DriverState.STARTED

    registry.stop_all()

    assert driver.stopped
    assert handle.state is DriverState.STOPPED
