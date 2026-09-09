"""Provider / Capability domain service: availability + last-mile lease + catalog.

This module composes three facts the ADRs keep distinct:

- Provider runtime health — actor-independent, driver-level (`DriverRegistry`).
- Credential presence — actor-scoped, derived over the Identity-owned binding set.
- Project policy — the authorization result for a specific capability/operation,
  computed by the application policy layer and passed in, not re-derived here.

`CapabilityAvailability(actor, project, capability)` is a derived query, never
stored (ADR-0012 / TODO.md section 5). Building the ephemeral `CredentialLease`
is the provider invocation layer: the lease never leaves this layer and is never
returned through the normal API surface (TODO.md section 3).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from revolab.credentials import CredentialLease
from revolab.domain.identity import credential_bindings, has_credential
from revolab.drivers import DriverRegistry, DriverState
from revolab.enums import CapabilityAvailability, CapabilityKind, ProviderRuntimeHealth
from revolab.secret_store import SecretMissingError, SecretRef, SecretStore


def credentials_present(
    session: Session,
    actor_id: UUID,
    provider_key: str,
    required_kinds: tuple[str, ...],
) -> bool:
    """All required credential kinds are bound for the Actor (derived query)."""
    return all(
        has_credential(session, actor_id, provider_key, kind) for kind in required_kinds
    )


def capability_availability(
    *,
    health: ProviderRuntimeHealth,
    credentials_present: bool,
    permitted: bool,
) -> CapabilityAvailability:
    """Derived projection for ONE capability kind in one Project for one Actor.

    Pure composition of the three distinct inputs; it derives neither health,
    credential presence, nor authorization itself. Precedence is deliberate:
    provider health, then project policy (NOT_AUTHORIZED), then credential
    presence (CREDENTIAL_MISSING) — an actor without project access is never
    described as merely missing a credential, and credential state is never
    reported for actors with no project access.
    """
    if health != ProviderRuntimeHealth.READY:
        return CapabilityAvailability.PROVIDER_UNAVAILABLE
    if not permitted:
        return CapabilityAvailability.NOT_AUTHORIZED
    if not credentials_present:
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
    registry: DriverRegistry,
    policy_permits: Callable[[CapabilityKind], bool],
) -> list[dict[str, Any]]:
    """Project-scoped, Actor-contextual read-only Provider Catalog.

    Exposes only non-secret information. Secret material and `secret_ref` never
    appear in these entries; another Actor's bindings are never surfaced — only
    the calling Actor's own per-kind presence. Availability is reported
    per-capability because the policy component is operation-specific.
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
        present = credentials_present(session, actor_id, key, tuple(required))
        capabilities = [
            {
                "kind": kind,
                "availability": capability_availability(
                    health=health,
                    credentials_present=present,
                    permitted=policy_permits(kind),
                ),
            }
            for kind in sorted(driver.capabilities, key=lambda k: k.value)
        ]
        entries.append(
            {
                "key": key,
                "name": driver.display_name,
                "description": driver.description,
                "required_credential_kinds": required,
                "health": health,
                "credential_presence": [
                    {"kind": kind, "present": has_credential(session, actor_id, key, kind)}
                    for kind in required
                ],
                "capabilities": capabilities,
            }
        )
    return entries
