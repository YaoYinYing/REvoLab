"""Application-lifespan driver installation (Phase 4).

Explicit, deterministic registry wiring: no global module import side effects.
`install_drivers` is called exactly once by the FastAPI lifespan, registers the
configured drivers, and `start_all`/`stop_all` own the in-process lifecycle.
A configured REvoCompute driver that cannot start fails loudly (the lifespan
propagates the exception) rather than silently dropping the provider.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from revolab.config import Settings
from revolab.drivers import DriverContext, DriverRegistry


def build_driver_context(settings: Settings) -> DriverContext:
    settings_map: dict[str, Any] = {
        "revocompute_base_url": settings.revocompute_base_url,
        "revocompute_timeout_seconds": settings.revocompute_timeout_seconds,
    }
    return DriverContext(
        environment=settings.environment,
        settings=MappingProxyType(settings_map),
    )


def install_drivers(registry: DriverRegistry, context: DriverContext, settings: Settings) -> None:
    """Register configured drivers. Called once at startup; idempotent by
    construction because the registry rejects duplicate keys."""
    if settings.revocompute_base_url:
        from revolab.drivers.revocompute import REvoComputeDriver

        registry.register(REvoComputeDriver())
    if settings.e2e_fake_compute:
        if settings.environment == "production":
            raise RuntimeError(
                "the in-process fake compute provider must not be enabled in production"
            )
        from revolab.testing.fake_compute import FakeComputeDriver

        registry.register(FakeComputeDriver())
