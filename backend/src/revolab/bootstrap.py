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
        "ncbi_tool": settings.ncbi_tool,
        "ncbi_email": settings.ncbi_email,
        "ncbi_timeout_seconds": settings.ncbi_timeout_seconds,
        "ncbi_min_request_interval_seconds": settings.ncbi_min_request_interval_seconds,
        "uniprot_timeout_seconds": settings.uniprot_timeout_seconds,
        "rcsb_timeout_seconds": settings.rcsb_timeout_seconds,
    }
    return DriverContext(
        environment=settings.environment,
        settings=MappingProxyType(settings_map),
    )


def install_drivers(registry: DriverRegistry, context: DriverContext, settings: Settings) -> None:
    """Register configured drivers. Must be called exactly once per process at
    startup; registering the same driver key twice is a hard error (fail-closed),
    which is what keeps a repeated lifespan from silently double-installing."""
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
    # Phase-13 NCBI PubMed literature discovery. Registered only when the required
    # operator identity/contact are configured; a HALF-configured deployment is a
    # deployment error and fails loudly here rather than probing NCBI anonymously.
    ncbi_configured = bool(settings.ncbi_tool) or bool(settings.ncbi_email)
    if ncbi_configured:
        if not (settings.ncbi_tool and settings.ncbi_email):
            raise RuntimeError(
                "NCBI literature discovery requires BOTH REVOLAB_NCBI_TOOL and "
                "REVOLAB_NCBI_EMAIL"
            )
        from revolab.drivers.ncbi import NCBIDriver

        registry.register(NCBIDriver())
    if settings.e2e_fake_literature:
        if settings.environment == "production":
            raise RuntimeError(
                "the in-process fake literature provider must not be enabled in production"
            )
        from revolab.testing.fake_literature import FakeLiteratureDriver

        registry.register(FakeLiteratureDriver())
    # Phase-14 UniProt protein discovery. The public REST surface needs no
    # credential and no operator identity, but the real remote driver is installed
    # only when the deployment explicitly enables it — exactly like NCBI — so a
    # zero-config deployment keeps the Provider Catalog an honest empty set and CI
    # never performs a live UniProt request. Its authority (`uniprot`) collides
    # loudly with any other driver claiming the same durable identity namespace.
    if settings.uniprot_discovery_enabled:
        from revolab.drivers.uniprot import UniProtDriver

        registry.register(UniProtDriver())
    if settings.e2e_fake_protein:
        if settings.environment == "production":
            raise RuntimeError(
                "the in-process fake protein provider must not be enabled in production"
            )
        from revolab.testing.fake_protein import FakeProteinDriver

        registry.register(FakeProteinDriver())
    # Phase-15 RCSB PDB structure discovery. The public Search/Data/file services
    # need no credential, but the real remote driver is installed only when the
    # deployment explicitly enables it — exactly like UniProt/NCBI — so a
    # zero-config deployment keeps the Provider Catalog an honest empty set and CI
    # never performs a live RCSB request. Its authority (`pdb`) collides loudly with
    # any other driver claiming the same durable identity namespace.
    if settings.rcsb_discovery_enabled:
        from revolab.drivers.rcsb import RcsbDriver

        registry.register(RcsbDriver())
    if settings.e2e_fake_structure:
        if settings.environment == "production":
            raise RuntimeError(
                "the in-process fake structure provider must not be enabled in production"
            )
        from revolab.testing.fake_structure import FakeStructureDriver

        registry.register(FakeStructureDriver())
