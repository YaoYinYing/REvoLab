"""Provider/Capability-domain invocation layer (Phase 4).

The one place that materializes the accepted capability invocation flow:

    READY driver -> project policy permits -> required credentials present
        -> ephemeral CredentialLease -> capability method

Every capability method is reached only through this module, so the
availability/credential gate and the Secret-boundary translation exist exactly
once (never duplicated in drivers). This module belongs to the Provider /
Capability domain: it imports Identity only through the credential-binding
queries (the same `PC -> IC` edge `domain/provider.py` already uses) and never
imports the scientific-object or application-command layers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast
from uuid import UUID

from sqlalchemy.orm import Session

from revolab.capabilities import (
    ArtifactHandle,
    ArtifactPreviewCapability,
    ArtifactResolutionCapability,
    CapabilityError,
    ComputeCapability,
    ExternalArtifactRef,
    ResolvedInput,
    RunHandle,
    RunView,
    TaskKindRef,
    TaskKindSchema,
)
from revolab.credentials import CredentialLease
from revolab.domain.errors import AuthorizationError, NotFoundError
from revolab.domain.provider import build_credential_lease, credentials_present
from revolab.drivers import DriverRegistry, DriverState
from revolab.enums import CapabilityErrorKind, CapabilityKind, ProviderRuntimeHealth
from revolab.secret_store import SecretMissingError, SecretStore


def _prepared_capability(
    *,
    session: Session,
    registry: DriverRegistry,
    store: SecretStore,
    actor_id: UUID,
    provider_key: str,
    kind: CapabilityKind,
    permitted: bool,
) -> tuple[Any, CredentialLease]:
    try:
        handle = registry.get(provider_key)
    except LookupError as exc:
        raise NotFoundError(f"unknown provider: {provider_key}") from exc
    if handle.state is not DriverState.READY:
        raise CapabilityError(
            CapabilityErrorKind.PROVIDER_UNAVAILABLE,
            "provider driver is not ready",
            provider_key=provider_key,
            capability_kind=kind,
        )
    if registry.health(provider_key) is not ProviderRuntimeHealth.READY:
        raise CapabilityError(
            CapabilityErrorKind.PROVIDER_UNAVAILABLE,
            "provider is currently unreachable",
            provider_key=provider_key,
            capability_kind=kind,
        )
    if not permitted:
        raise AuthorizationError("actor is not authorized for this capability in this project")
    if not credentials_present(session, actor_id, provider_key, handle.driver.required_credential_kinds):
        raise AuthorizationError("required provider credential is not present for the actor")
    try:
        lease = build_credential_lease(
            session,
            store,
            actor_id,
            provider_key,
            handle.driver.required_credential_kinds,
        )
    except SecretMissingError as exc:
        raise CapabilityError(
            CapabilityErrorKind.AUTH,
            "provider credential material is unavailable",
            provider_key=provider_key,
            capability_kind=kind,
        ) from exc
    capability = handle.driver.capabilities.get(kind)
    if capability is None:
        raise NotFoundError(f"provider {provider_key!r} does not realize capability {kind.value!r}")
    return capability, lease


def list_task_kinds(
    session: Session,
    registry: DriverRegistry,
    store: SecretStore,
    actor_id: UUID,
    provider_key: str,
    *,
    permitted: bool,
) -> list[TaskKindRef]:
    capability, lease = _prepared_capability(
        session=session,
        registry=registry,
        store=store,
        actor_id=actor_id,
        provider_key=provider_key,
        kind=CapabilityKind.COMPUTE,
        permitted=permitted,
    )
    return cast(ComputeCapability, capability).list_task_kinds(lease)


def task_kind_schema(
    session: Session,
    registry: DriverRegistry,
    store: SecretStore,
    actor_id: UUID,
    provider_key: str,
    kind_id: str,
    *,
    permitted: bool,
) -> TaskKindSchema:
    capability, lease = _prepared_capability(
        session=session,
        registry=registry,
        store=store,
        actor_id=actor_id,
        provider_key=provider_key,
        kind=CapabilityKind.COMPUTE,
        permitted=permitted,
    )
    return cast(ComputeCapability, capability).task_kind_schema(kind_id, lease)


def submit_compute(
    session: Session,
    registry: DriverRegistry,
    store: SecretStore,
    actor_id: UUID,
    provider_key: str,
    kind_id: str,
    inputs: Sequence[ResolvedInput],
    params: Mapping[str, Any],
    *,
    permitted: bool,
) -> RunHandle:
    capability, lease = _prepared_capability(
        session=session,
        registry=registry,
        store=store,
        actor_id=actor_id,
        provider_key=provider_key,
        kind=CapabilityKind.COMPUTE,
        permitted=permitted,
    )
    return cast(ComputeCapability, capability).submit(kind_id, inputs, params, lease)


def get_run(
    session: Session,
    registry: DriverRegistry,
    store: SecretStore,
    actor_id: UUID,
    provider_key: str,
    native_id: str,
    *,
    permitted: bool,
) -> RunView:
    capability, lease = _prepared_capability(
        session=session,
        registry=registry,
        store=store,
        actor_id=actor_id,
        provider_key=provider_key,
        kind=CapabilityKind.COMPUTE,
        permitted=permitted,
    )
    return cast(ComputeCapability, capability).get_run(native_id, lease)


def list_artifacts(
    session: Session,
    registry: DriverRegistry,
    store: SecretStore,
    actor_id: UUID,
    provider_key: str,
    native_id: str,
    *,
    permitted: bool,
) -> list[ArtifactHandle]:
    capability, lease = _prepared_capability(
        session=session,
        registry=registry,
        store=store,
        actor_id=actor_id,
        provider_key=provider_key,
        kind=CapabilityKind.COMPUTE,
        permitted=permitted,
    )
    return cast(ComputeCapability, capability).list_artifacts(native_id, lease)


def resolve_artifact(
    session: Session,
    registry: DriverRegistry,
    store: SecretStore,
    actor_id: UUID,
    provider_key: str,
    artifact: ExternalArtifactRef,
    *,
    permitted: bool,
) -> ArtifactHandle:
    capability, lease = _prepared_capability(
        session=session,
        registry=registry,
        store=store,
        actor_id=actor_id,
        provider_key=provider_key,
        kind=CapabilityKind.ARTIFACT_RESOLUTION,
        permitted=permitted,
    )
    return cast(ArtifactResolutionCapability, capability).resolve(artifact, lease)


def resolve_artifact_preview(
    session: Session,
    registry: DriverRegistry,
    store: SecretStore,
    actor_id: UUID,
    provider_key: str,
    artifact: ExternalArtifactRef,
    *,
    permitted: bool,
    offset: int = 0,
    limit: int,
) -> ArtifactHandle:
    """Bounded external-artifact read for the Agent inspect path.

    Requires the driver to realize `ArtifactPreviewCapability`; a provider that
    cannot bound its read fails closed (`PROVIDER_UNAVAILABLE`) instead of
    silently materializing the whole artifact.
    """
    capability, lease = _prepared_capability(
        session=session,
        registry=registry,
        store=store,
        actor_id=actor_id,
        provider_key=provider_key,
        kind=CapabilityKind.ARTIFACT_RESOLUTION,
        permitted=permitted,
    )
    preview = getattr(capability, "preview", None)
    if not callable(preview):
        raise CapabilityError(
            CapabilityErrorKind.PROVIDER_UNAVAILABLE,
            "provider does not support bounded artifact preview",
            provider_key=provider_key,
            capability_kind=CapabilityKind.ARTIFACT_RESOLUTION,
        )
    handle = cast(ArtifactPreviewCapability, capability).preview(
        artifact, lease, offset=offset, limit=limit
    )
    # Defense-in-depth: the preview boundary must never let a misbehaving driver
    # slide a whole artifact into Agent memory even if it ignores the contract.
    if handle.data is not None and len(handle.data) > max(limit, 0):
        raise CapabilityError(
            CapabilityErrorKind.PROVIDER_UNAVAILABLE,
            "provider returned more than the requested artifact preview bound",
            provider_key=provider_key,
            capability_kind=CapabilityKind.ARTIFACT_RESOLUTION,
        )
    return handle


__all__ = [
    "get_run",
    "list_artifacts",
    "list_task_kinds",
    "resolve_artifact",
    "resolve_artifact_preview",
    "submit_compute",
    "task_kind_schema",
]
