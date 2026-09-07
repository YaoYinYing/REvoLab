from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from enum import StrEnum
from importlib.metadata import entry_points
from types import MappingProxyType
from typing import Any, Protocol


class DriverState(StrEnum):
    DISCOVERED = "discovered"
    VALIDATED = "validated"
    STARTED = "started"
    READY = "ready"
    STOPPED = "stopped"


class Driver(Protocol):
    name: str

    def capabilities(self) -> set[str]: ...

    def start(self, context: DriverContext) -> None: ...

    def stop(self) -> None: ...


@dataclass(frozen=True)
class DriverContext:
    environment: str
    settings: MappingProxyType[str, Any]


@dataclass
class DriverHandle:
    driver: Driver
    state: DriverState = DriverState.DISCOVERED


@dataclass
class DriverRegistry:
    """Application-scoped registry with explicit lifecycle and collision handling."""

    _drivers: dict[str, DriverHandle] = field(default_factory=dict)
    _resources: ExitStack = field(default_factory=ExitStack)

    def register(self, driver: Driver) -> DriverHandle:
        if driver.name in self._drivers:
            raise ValueError(f"driver already registered: {driver.name}")
        handle = DriverHandle(driver=driver, state=DriverState.VALIDATED)
        self._drivers[driver.name] = handle
        return handle

    def discover_entry_points(self) -> Iterator[DriverHandle]:
        for point in entry_points(group="revolab.drivers"):
            loaded = point.load()
            driver = loaded() if isinstance(loaded, type) else loaded
            yield self.register(driver)

    def start_all(self, context: DriverContext) -> None:
        started: list[DriverHandle] = []
        try:
            for handle in self._drivers.values():
                handle.driver.start(context)
                handle.state = DriverState.READY
                started.append(handle)
        except Exception:
            for handle in reversed(started):
                handle.driver.stop()
                handle.state = DriverState.STOPPED
            raise

    def stop_all(self) -> None:
        for handle in reversed(tuple(self._drivers.values())):
            if handle.state in {DriverState.STARTED, DriverState.READY}:
                handle.driver.stop()
                handle.state = DriverState.STOPPED
        self._resources.close()

    def get(self, name: str) -> DriverHandle:
        try:
            return self._drivers[name]
        except KeyError as exc:
            raise LookupError(f"unknown driver: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._drivers)
