"""Phase-4 bootstrap/lifecycle + authority-resolution guards."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from revolab.bootstrap import build_driver_context, install_drivers
from revolab.config import Settings
from revolab.drivers import DriverContext, DriverRegistry
from revolab.drivers.revocompute import REvoComputeDriver
from revolab.testing.fake_compute import FakeComputeDriver


def test_driver_for_authority_is_explicit_not_assumed() -> None:
    registry = DriverRegistry()
    registry.register(FakeComputeDriver())
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))

    assert registry.driver_for_authority("fakecompute") == "fakecompute"
    assert registry.driver_for_authority("revocompute") is None


def test_fake_provider_refuses_production() -> None:
    settings = Settings(environment="production", e2e_fake_compute=True)
    registry = DriverRegistry()
    with pytest.raises(RuntimeError, match="must not be enabled in production"):
        install_drivers(registry, build_driver_context(settings), settings)


def test_revocompute_driver_requires_base_url() -> None:
    driver = REvoComputeDriver()
    with pytest.raises(RuntimeError, match="revocompute_base_url"):
        driver.start(DriverContext(environment="test", settings=MappingProxyType({})))


def test_install_drivers_installs_revocompute_when_configured() -> None:
    settings = Settings(revocompute_base_url="https://revocompute.test")
    registry = DriverRegistry()
    install_drivers(registry, build_driver_context(settings), settings)
    assert "revocompute" in registry.names()
