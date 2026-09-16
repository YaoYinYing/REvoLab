"""Literature-discovery capability invocation layer (Phase 13).

The ONE place the Provider / Capability domain invokes a
`LiteratureDiscoveryCapability`. Like `revolab.domain.compute`, every call is
reached only through the shared gate

    READY driver -> project policy permits -> required credentials present
        -> ephemeral CredentialLease -> capability method

so availability/credential/secret translation exists exactly once and is never
duplicated in a driver or in the application layer.

The external read performs no durable write and returns only EPHEMERAL
`LiteratureCandidate` data; neither the candidate nor the provider response is
ever persisted here.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy.orm import Session

from revolab.capabilities import (
    LiteratureCandidate,
    LiteratureDiscoveryCapability,
    LiteratureSearchResult,
)
from revolab.credentials import CredentialLease
from revolab.domain.compute import prepared_capability
from revolab.drivers import DriverRegistry
from revolab.enums import CapabilityKind
from revolab.secret_store import SecretStore


def _literature_capability(
    *,
    session: Session,
    registry: DriverRegistry,
    store: SecretStore,
    actor_id: UUID,
    provider_key: str,
    permitted: bool,
) -> tuple[LiteratureDiscoveryCapability, CredentialLease]:
    capability, lease = prepared_capability(
        session=session,
        registry=registry,
        store=store,
        actor_id=actor_id,
        provider_key=provider_key,
        kind=CapabilityKind.LITERATURE_DISCOVERY,
        permitted=permitted,
    )
    return cast(LiteratureDiscoveryCapability, capability), lease


def search_literature(
    session: Session,
    registry: DriverRegistry,
    store: SecretStore,
    actor_id: UUID,
    provider_key: str,
    query: str,
    limit: int,
    *,
    permitted: bool,
) -> LiteratureSearchResult:
    """One bounded read-only external literature search (no persistence)."""
    capability, lease = _literature_capability(
        session=session,
        registry=registry,
        store=store,
        actor_id=actor_id,
        provider_key=provider_key,
        permitted=permitted,
    )
    return capability.search(query, limit, lease)


def resolve_literature(
    session: Session,
    registry: DriverRegistry,
    store: SecretStore,
    actor_id: UUID,
    provider_key: str,
    authority: str,
    native_id: str,
    *,
    permitted: bool,
) -> LiteratureCandidate:
    """Re-read ONE publication by its durable external identity (no persistence).

    This is the CURRENT provider re-resolution an explicit import performs before
    any durable write; it never trusts client-supplied bibliographic metadata.
    """
    capability, lease = _literature_capability(
        session=session,
        registry=registry,
        store=store,
        actor_id=actor_id,
        provider_key=provider_key,
        permitted=permitted,
    )
    return capability.resolve(authority, native_id, lease)


__all__ = ["resolve_literature", "search_literature"]
