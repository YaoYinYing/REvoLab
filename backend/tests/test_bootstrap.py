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


def test_fake_protein_provider_refuses_production() -> None:
    settings = Settings(environment="production", e2e_fake_protein=True)
    registry = DriverRegistry()
    with pytest.raises(RuntimeError, match="must not be enabled in production"):
        install_drivers(registry, build_driver_context(settings), settings)


def test_fake_literature_provider_refuses_production() -> None:
    """The Phase-13 fake carries the same production guard as the Phase-14 one."""
    settings = Settings(environment="production", e2e_fake_literature=True)
    registry = DriverRegistry()
    with pytest.raises(RuntimeError, match="must not be enabled in production"):
        install_drivers(registry, build_driver_context(settings), settings)


def test_uniprot_driver_is_installed_only_when_enabled() -> None:
    """The real remote provider is opt-in, exactly like NCBI.

    A zero-config deployment keeps the Provider Catalog an honest empty set, so no
    test or browser run can perform a live UniProt request by accident.
    """
    default = Settings()
    registry = DriverRegistry()
    install_drivers(registry, build_driver_context(default), default)
    assert "uniprot" not in registry.names()

    enabled = Settings(uniprot_discovery_enabled=True)
    registry = DriverRegistry()
    install_drivers(registry, build_driver_context(enabled), enabled)
    assert "uniprot" in registry.names()

    fake = Settings(e2e_fake_protein=True)
    registry = DriverRegistry()
    install_drivers(registry, build_driver_context(fake), fake)
    assert "fakeprotein" in registry.names()
    # The fake NEVER brings the real remote provider with it.
    assert "uniprot" not in registry.names()


def test_fake_protein_provider_claims_its_own_authority() -> None:
    from revolab.drivers.uniprot import UNIPROT_AUTHORITY
    from revolab.testing.fake_protein import FAKE_PROTEIN_AUTHORITY, FakeProteinDriver

    assert FAKE_PROTEIN_AUTHORITY != UNIPROT_AUTHORITY
    assert FakeProteinDriver().authorities == (FAKE_PROTEIN_AUTHORITY,)


def test_fake_structure_provider_refuses_production() -> None:
    """The Phase-15 fake carries the same production guard as the earlier ones."""
    settings = Settings(environment="production", e2e_fake_structure=True)
    registry = DriverRegistry()
    with pytest.raises(RuntimeError, match="must not be enabled in production"):
        install_drivers(registry, build_driver_context(settings), settings)


def test_rcsb_driver_is_installed_only_when_enabled() -> None:
    """The real RCSB provider is opt-in, exactly like NCBI/UniProt.

    A zero-config deployment keeps the Provider Catalog an honest empty set, so no
    test or browser run can perform a live RCSB request by accident.
    """
    default = Settings()
    registry = DriverRegistry()
    install_drivers(registry, build_driver_context(default), default)
    assert "rcsb" not in registry.names()

    enabled = Settings(rcsb_discovery_enabled=True)
    registry = DriverRegistry()
    install_drivers(registry, build_driver_context(enabled), enabled)
    assert "rcsb" in registry.names()
    assert registry.get("rcsb").driver.authorities == ("pdb",)

    fake = Settings(e2e_fake_structure=True)
    registry = DriverRegistry()
    install_drivers(registry, build_driver_context(fake), fake)
    assert "fakepdb" in registry.names()
    # The fake NEVER brings the real remote provider with it.
    assert "rcsb" not in registry.names()


def test_fake_structure_provider_claims_its_own_authority() -> None:
    from revolab.drivers.rcsb import RCSB_AUTHORITY
    from revolab.testing.fake_structure import FAKE_STRUCTURE_AUTHORITY, FakeStructureDriver

    assert FAKE_STRUCTURE_AUTHORITY != RCSB_AUTHORITY
    assert FakeStructureDriver().authorities == (FAKE_STRUCTURE_AUTHORITY,)
