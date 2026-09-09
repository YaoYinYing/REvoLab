"""Provider / Capability domain service: availability + last-mile lease + catalog.

This module composes three facts the ADRs keep distinct:

- Provider runtime health — actor-independent, driver-level (`DriverRegistry`).
- Credential presence — actor-scoped, derived over the Identity-owned binding set.
- Project policy — the Phase-1 `ProjectMembership` / authority model.

`CapabilityAvailability(actor, project)` is a derived query, never stored
(ADR-0012 / TODO.md section 5). Building the ephemeral `CredentialLease` is the
provider invocation layer: the lease never leaves this layer and is never
returned through the normal API surface (TODO.md section 3).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from revolab.credentials import CredentialLease
from revolab.domain.errors import AuthorizationError
from revolab.domain.identity import (
    credential_bindings,
    has_credential,
    mutation_capable_membership,
)
from revolab.drivers import DriverRegistry, DriverState
from revolab.enums import CapabilityAvailability, ProviderRuntimeHealth
from revolab.secret_store import SecretMissingError, SecretRef, SecretStore


def capability_availability(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    *,
    provider_key: str,
    health: ProviderRuntimeHealth,
    required_kinds: tuple[str, ...],
) -> CapabilityAvailability:
    """Derived, actor-contextual projection of whether the actor may call a
    provider's capability in this project. Never persisted; recomputed per query.

    Precedence: health first (PROVIDER_UNAVAILABLE), then project policy
    (NOT_AUTHORIZED), then credential presence (CREDENTIAL_MISSING). This keeps
    "unauthorized" authoritative even when a credentialed actor is also
    unauthorized, and never reports credential state for actors with no project
    access.
    """
    if health != ProviderRuntimeHealth.READY:
        return CapabilityAvailability.PROVIDER_UNAVAILABLE
    try:
        mutation_capable_membership(session, actor_id, project_id)
    except AuthorizationError:
        return CapabilityAvailability.NOT_AUTHORIZED
    for kind in required_kinds:
        if not has_credential(session, actor_id, provider_key, kind):
            return CapabilityAvailability.CREDENTIAL_MISSING
    return CapabilityAvailability.AVAILABLE


def build_credential_lease(
    session: Session,
    store: SecretStore,
    actor_id: UUID,
    provider_key: str,
    required_kinds: tuple[str, ...],
) -> CredentialLease:
    """Last-mile materialization for ONE invocation scope.

    Reads the Identity-owned binding set to obtain opaque `secret_ref` handles,
    then asks the Secret store to materialize only the required kinds. The lease
    is ephemeral and is never persisted, serialized, or returned via the API.
    A binding without live material raises `SecretMissingError` (never a crash),
    so a non-durable dev Secret store that lost state across a restart fails
    explicitly at the invocation boundary rather than returning partial creds.
    """
    bindings = credential_bindings(session, actor_id, provider_key)
    by_kind = {binding.kind: binding.secret_ref for binding in bindings}
    requested: dict[str, SecretRef] = {}
    for kind in required_kinds:
        ref = by_kind.get(kind)
        if ref is None:
            raise SecretMissingError(
                f"no credential binding for required kind: {kind!r}"
            )
        requested[kind] = SecretRef(ref)
    return store.lease(requested)


def catalog_entries(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    registry: DriverRegistry,
) -> list[dict[str, Any]]:
    """Project-scoped, Actor-contextual read-only Provider Catalog.

    Exposes only non-secret information. Secret material and `secret_ref` never
    appear in these entries; another Actor's bindings are never surfaced — only
    the calling Actor's own per-kind presence.
    """
    entries: list[dict[str, Any]] = []
    for key in registry.names():
        handle = registry.get(key)
        # Only started drivers are projected: a REGISTERED (never-started) driver
        # has no runtime yet and must not report health or availability. This
        # keeps in-process lifecycle separate from Provider/Capability domain
        # state (ADR-0012).
        if handle.state is not DriverState.READY:
            continue
        driver = handle.driver
        required = list(driver.required_credential_kinds)
        health = registry.health(key)
        realized = [kind for kind in sorted(driver.capabilities, key=lambda k: k.value)]
        entries.append(
            {
                "key": key,
                "name": driver.display_name,
                "description": driver.description,
                "required_credential_kinds": required,
                "realized_capability_kinds": realized,
                "health": health,
                "credential_presence": [
                    {"kind": kind, "present": has_credential(session, actor_id, key, kind)}
                    for kind in required
                ],
                "availability": capability_availability(
                    session,
                    actor_id,
                    project_id,
                    provider_key=key,
                    health=health,
                    required_kinds=tuple(required),
                ),
            }
        )
    return entries
