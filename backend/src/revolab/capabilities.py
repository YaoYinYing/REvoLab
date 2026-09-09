"""Provider-neutral capability protocols and value objects (Phase 4).

This leaf module defines the executable shapes a Provider Driver must realize for
the Core-owned `CapabilityKind` vocabulary. It imports only the neutral leaves
(`revolab.enums`, `revolab.credentials`) so any driver can implement these
protocols without importing Identity, the Secret store, or the scientific object
domain.

Provider-specific vocabulary (task names, parameter names, runner names,
artifact vocabulary, status strings, scheduler details) is NOT represented here:
it flows as opaque data through the value objects below — `TaskKindRef.kind_id`,
`TaskKindSchema.parameter_schema`, `RunView.status`, `ArtifactHandle.native_id` —
never as Core enums or fields.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from revolab.credentials import CredentialLease
from revolab.enums import CapabilityErrorKind, CapabilityKind, ResourceKind


class CapabilityError(Exception):
    """The typed failure boundary for provider invocations.

    A driver (or the Provider invocation layer) raises this with a stable Core
    `kind` (see `CapabilityErrorKind`) and a sanitized, non-secret `message`.
    The message must NEVER contain secret material, an Authorization/X-API-Key
    header value, a `secret_ref`, an upstream response body, or an exception
    traceback. `str`/`repr` are safe by construction and may be logged.
    """

    def __init__(
        self,
        kind: CapabilityErrorKind,
        message: str,
        *,
        provider_key: str,
        capability_kind: CapabilityKind,
        retryable: bool = False,
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.provider_key = provider_key
        self.capability_kind = capability_kind
        self.retryable = retryable
        self.upstream_status = upstream_status

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:
        return (
            f"CapabilityError(kind={self.kind.value!r}, "
            f"provider_key={self.provider_key!r}, capability_kind={self.capability_kind.value!r})"
        )


# ---------------------------------------------------------------------------
# Compute capability
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskKindRef:
    """Discovery-time identity of one provider task kind. `kind_id` is provider
    vocabulary (opaque to Core); everything else is presentation data."""

    kind_id: str
    display_name: str
    description: str | None = None
    category: str | None = None


@dataclass(frozen=True)
class InputSpec:
    """Provider-declared, neutral input-file contract for one task kind.

    The field values (extensions etc.) are provider data returned verbatim; Core
    assigns them no meaning and never interprets them."""

    label: str | None = None
    required: bool = False
    multiple: bool = False
    max_files: int | None = None
    accepted_extensions: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskKindSchema:
    """Schema-as-data for one task kind: a JSON Schema (Draft 2020-12) for the
    task parameters plus a neutral input contract. Both are provider data."""

    kind_id: str
    display_name: str
    description: str | None
    parameter_schema: Mapping[str, Any]
    input_spec: InputSpec


@dataclass(frozen=True)
class ExternalArtifactRef:
    """Durable external artifact identity (no bytes). `native_id`/`version_id`
    are provider-owned identity data, never a filesystem path."""

    authority: str
    native_id: str
    version_id: str = ""
    content_type: str | None = None
    size: int | None = None
    checksum: str | None = None


@dataclass(frozen=True)
class ResolvedInput:
    """One provider-neutral input materialized by Core before a capability call.

    Exactly one of `data` (locally owned bytes, e.g. a ScientificObjectRevision
    serialization or an internal revolab artifact) or `external` (an external
    artifact identity whose bytes stay owned by the provider) is present. `role`
    is the optional role named by the provider task-kind schema.
    """

    role: str | None = None
    filename: str = "input"
    content_type: str | None = None
    data: bytes | None = None
    external: ExternalArtifactRef | None = None


@dataclass(frozen=True)
class InputBinding:
    """REvoLab-neutral input declaration for a compute invocation.

    `kind` is the Core `ResourceKind` of the referenced global resource; only
    `LEGAL_COMPUTE_INPUT_KINDS` are legal; `resource_id` is its opaque
    GlobalResourceRegistry identity; `role` is an optional role named by the
    provider task-kind schema.
    """

    kind: ResourceKind
    resource_id: UUID
    role: str | None = None


# The single canonical "compute input" subset of the registry kinds.
LEGAL_COMPUTE_INPUT_KINDS = frozenset(
    {ResourceKind.SCIENTIFIC_OBJECT_REVISION, ResourceKind.ARTIFACT_REFERENCE}
)


@dataclass(frozen=True)
class RunHandle:
    """The immutable identity card REvoLab stores for an external run. It is a
    reference, never a snapshot of mutable execution state."""

    authority: str
    native_id: str
    task_type: str | None = None


@dataclass(frozen=True)
class RunView:
    """Live, on-demand view of an external run. `status` is provider vocabulary
    (opaque string data); REvoLab never persists it."""

    authority: str
    native_id: str
    status: str
    status_detail: str | None = None


@dataclass(frozen=True)
class ArtifactHandle:
    """One external artifact's immutable identity card (on-demand enumeration).

    `data` is populated only by `ArtifactResolutionCapability.resolve`; the
    enumeration path never downloads bytes."""

    authority: str
    native_id: str
    version_id: str = ""
    content_type: str | None = None
    size: int | None = None
    checksum: str | None = None
    data: bytes | None = field(default=None, repr=False, compare=False)


class ComputeCapability(Protocol):
    """The executable compute boundary a Compute provider driver realizes."""

    provider_key: str
    kind: CapabilityKind

    def list_task_kinds(self, credentials: CredentialLease) -> list[TaskKindRef]: ...

    def task_kind_schema(self, kind_id: str, credentials: CredentialLease) -> TaskKindSchema: ...

    def submit(
        self,
        kind_id: str,
        inputs: Sequence[ResolvedInput],
        params: Mapping[str, Any],
        credentials: CredentialLease,
    ) -> RunHandle: ...

    def get_run(self, native_id: str, credentials: CredentialLease) -> RunView: ...

    def list_artifacts(
        self, native_id: str, credentials: CredentialLease
    ) -> list[ArtifactHandle]: ...


class ArtifactResolutionCapability(Protocol):
    """The executable read boundary for resolving external artifact bytes."""

    provider_key: str
    kind: CapabilityKind

    def resolve(
        self, artifact: ExternalArtifactRef, credentials: CredentialLease
    ) -> ArtifactHandle: ...
