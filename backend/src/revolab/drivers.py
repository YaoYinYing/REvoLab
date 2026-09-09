"""Provider / Capability domain: the in-process Driver registry.

Revised per ADR-0012 (the "Critique of ADR-0004" section):

- a thin lifecycle `Driver` + per-kind `Capability` realization;
- a typed `CapabilityKind -> Capability` map replaces `capabilities() -> set[str]`;
- two domain-visible lifecycle states (`REGISTERED`, `READY`); startup transients
  are local variables, never reified and never leaked into an API;
- each Driver declares its provider-declared `required_credential_kinds`.

Domain-visible lifecycle (in-process startup) is deliberately NOT the same thing
as Provider/Capability domain state: `ProviderRuntimeHealth` is the only
per-provider runtime state the registry tracks, and it is never persisted.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from importlib.metadata import entry_points
from types import MappingProxyType
from typing import Any, Protocol

from revolab.enums import CapabilityKind, ProviderRuntimeHealth


class DriverState(StrEnum):
    """Domain-visible driver lifecycle states (ADR-0012)."""

    REGISTERED = "registered"
    READY = "ready"


class Capability(Protocol):
    """Base capability identity: `(provider_key, kind)`.

    Per-kind method protocols (Compute/Search/…) are added in Phase 4 when the
    first real driver forces them — not before.
    """

    provider_key: str
    kind: CapabilityKind


class Driver(Protocol):
    """Thin registration/lifecycle shape. Capability realization delegates to
    `Capability` instances keyed by the Core-owned `CapabilityKind`."""

    name: str  # stable lowercase provider slug — the provider key
    display_name: str
    description: str | None
    required_credential_kinds: tuple[str, ...]  # provider-declared, never Core vocabulary
    capabilities: Mapping[CapabilityKind, Capability]

    def start(self, context: DriverContext) -> None: ...

    def stop(self) -> None: ...

    def probe_health(self) -> ProviderRuntimeHealth: ...


@dataclass(frozen=True)
class DriverContext:
    environment: str
    settings: MappingProxyType[str, Any]


@dataclass
class DriverHandle:
    driver: Driver
    state: DriverState = DriverState.REGISTERED


@dataclass
class DriverRegistry:
    """Application-scoped registry with explicit lifecycle and collision checks."""

    _drivers: dict[str, DriverHandle] = field(default_factory=dict)
    _health: dict[str, ProviderRuntimeHealth] = field(default_factory=dict)

    def register(self, driver: Driver) -> DriverHandle:
        if driver.name in self._drivers:
            raise ValueError(f"driver already registered: {driver.name}")
        for kind, capability in driver.capabilities.items():
            if capability.provider_key != driver.name:
                raise ValueError(
                    f"capability provider_key {capability.provider_key!r} does not "
                    f"match driver {driver.name!r}"
                )
            if capability.kind is not kind:
                raise ValueError(
                    f"capability kind {capability.kind!r} does not match its "
                    f"registry key {kind!r} for driver {driver.name!r}"
                )
        handle = DriverHandle(driver=driver)
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
                handle.state = DriverState.REGISTERED
            raise

    def stop_all(self) -> None:
        for handle in reversed(tuple(self._drivers.values())):
            if handle.state is DriverState.READY:
                handle.driver.stop()
                handle.state = DriverState.REGISTERED
        self._health.clear()

    def get(self, name: str) -> DriverHandle:
        try:
            return self._drivers[name]
        except KeyError as exc:
            raise LookupError(f"unknown driver: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._drivers)

    def capabilities_by_kind(self, kind: CapabilityKind) -> Iterator[tuple[str, Capability]]:
        """Project realized capabilities of the Core kind from READY drivers only."""
        for name, handle in self._drivers.items():
            if handle.state is not DriverState.READY:
                continue
            capability = handle.driver.capabilities.get(kind)
            if capability is not None:
                yield name, capability

    def health(self, name: str) -> ProviderRuntimeHealth:
        """Lazily probe, then cache, per-provider runtime health.

        Actor-independent by construction: it takes no actor/project argument.
        The cache is process memory only, cleared on `stop_all`, never persisted.
        Probing is defined only for started (READY) drivers: a REGISTERED driver
        has no runtime yet, and conflating that with `READY` health would merge
        in-process lifecycle with Provider/Capability domain state.
        """
        handle = self.get(name)
        if handle.state is not DriverState.READY:
            raise RuntimeError(f"driver is not READY: {name}")
        if name not in self._health:
            self._health[name] = handle.driver.probe_health()
        return self._health[name]

    def refresh_health(self, name: str) -> ProviderRuntimeHealth:
        """Explicitly re-probe and overwrite cached health (failure path)."""
        handle = self.get(name)
        if handle.state is not DriverState.READY:
            raise RuntimeError(f"driver is not READY: {name}")
        observed = handle.driver.probe_health()
        self._health[name] = observed
        return observed


# The production workflow registers no providers, so the ordinary catalog is the
# honest empty set. Tests inject their own registry; see `revolab.api`.
default_registry = DriverRegistry()
