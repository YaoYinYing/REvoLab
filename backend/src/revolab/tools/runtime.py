"""Local Tool Runtime (Phase 7): the closed, typed invocation path.

    ToolRegistry
        -> lookup exact tool_id
        -> validate input schema
        -> authorize Actor/Project/resource access
        -> invoke registered implementation
        -> validate typed output
        -> return ToolResult

Persistence semantics are declared by the tool and enforced here: read-only
analysis returns ephemeral results; a derived-result tool may persist its output
as a durable internal ArtifactReference (owner/member authority) plus a
`ToolInvocation` activity record; truth tools route through the existing typed
domain operations. A ToolResult is NEVER promoted to scientific truth
automatically (Evidence/Decision promotion stays deliberate and typed).

The durability of a persisted derived result is written in ONE transaction:
ContentStore put -> internal ArtifactReference (get-or-create + link +
stewardship) -> ToolInvocation row, then a single commit. The reproducibility
`parameters` stored are the CANONICAL validated model dump, never the raw
untrusted request dict.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from revolab import services
from revolab.domain.errors import ValidationError
from revolab.domain.identity import mutation_capable_membership, readable_membership
from revolab.enums import ResourceKind, ToolResultKind, ToolSideEffectClass
from revolab.models import ToolInvocation
from revolab.schemas import ToolInvocationCreate, ToolResultRead
from revolab.tools.registry import LocalToolRegistry
from revolab.tools.types import DerivedArtifact, InvocationContext

TOOL_VERSION = "1.0.0"


def _validation_locations(exc: BaseException) -> str:
    """Sanitized field-path summary for an input-validation failure. Never
    includes submitted values, only the error locations (no secret material)."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        locations = sorted({repr(tuple(error.get("loc", ()))) for error in errors()})
        return ", ".join(locations)
    return ""


class LocalToolRuntime:
    """The only entry point for executing registered local Project Tools."""

    def __init__(self, registry: LocalToolRegistry) -> None:
        self._registry = registry

    def invoke(self, ctx: InvocationContext, request: ToolInvocationCreate) -> ToolResultRead:
        readable_membership(ctx.session, ctx.actor_id, ctx.project_id)
        try:
            tool = self._registry.get(request.tool_id)
        except KeyError as exc:
            raise ValidationError(f"unknown tool id {request.tool_id!r}") from exc

        if tool.requires_mutation:
            mutation_capable_membership(ctx.session, ctx.actor_id, ctx.project_id)
        if request.persist:
            _authorize_persist(ctx, tool.side_effect_class)

        try:
            parsed = tool.input_model.model_validate(request.input)
        except (PydanticValidationError, ValueError) as exc:
            locations = _validation_locations(exc)
            raise ValidationError(
                f"invalid input for tool {tool.id!r}" + (f" ({locations})" if locations else "")
            ) from exc

        output = tool.handler(ctx, parsed, request.persist)
        value = _validate_typed_output(tool.id, tool.output_model, output.value)

        if request.persist and tool.side_effect_class is ToolSideEffectClass.CREATES_DERIVED_RESULT:
            if output.derived is None:  # pragma: no cover - handler wiring invariant
                raise ValidationError("tool did not produce a derivable result")
            artifact_id = _persist_derived_artifact(ctx, tool.id, parsed, output.derived)
            return ToolResultRead(
                tool_id=tool.id,
                status="completed",
                result_kind=ToolResultKind.ARTIFACT,
                resource_id=artifact_id,
                resource_kind=ResourceKind.ARTIFACT_REFERENCE,
                value=value,
                persisted=True,
            )

        return ToolResultRead(
            tool_id=tool.id,
            status="completed",
            result_kind=output.kind,
            resource_id=output.resource_id,
            resource_kind=output.resource_kind,
            value=value,
            persisted=False,
        )


def _validate_typed_output(
    tool_id: str, output_model: type[BaseModel], value: BaseModel | None
) -> dict[str, Any] | None:
    """Defense-in-depth typed-output gate: a mis-wired handler that returns the
    wrong model is a programming error, surfaced loudly rather than serialized."""
    if value is None:
        return None
    if not isinstance(value, output_model):
        raise RuntimeError(
            f"tool {tool_id!r} produced {type(value).__name__!r}, "
            f"expected {output_model.__name__!r}"
        )
    return value.model_dump(mode="json")


def _authorize_persist(ctx: InvocationContext, side_effect_class: ToolSideEffectClass) -> None:
    """Persisting a derived result is a mutation: owner/member authority is
    required, and only derived-result tools may persist at all."""
    if side_effect_class is not ToolSideEffectClass.CREATES_DERIVED_RESULT:
        raise ValidationError("this tool does not support persisting a derived result")
    mutation_capable_membership(ctx.session, ctx.actor_id, ctx.project_id)


def _persist_derived_artifact(
    ctx: InvocationContext, tool_id: str, parsed: BaseModel, derived: DerivedArtifact
) -> UUID:
    """Persist a derived result in ONE transaction: internal ArtifactReference
    (get-or-create + link + stewardship, uncommitted) + the `ToolInvocation`
    reproducibility record, then a single commit.

    `parameters` stores the canonical validated model dump — never the raw
    request dict — so extra/unknown keys never reach durable storage."""
    artifact = services.create_internal_artifact(
        ctx.session,
        ctx.actor_id,
        ctx.project_id,
        ctx.content_store,
        derived.data,
        content_type=derived.content_type,
        commit=False,
    )
    artifact_id = cast(UUID, artifact.artifact_id)
    input_resource_ids = [str(value) for value in _artifact_input_ids(parsed)]
    record_tool_invocation(
        ctx.session,
        actor_id=ctx.actor_id,
        project_id=ctx.project_id,
        tool_id=tool_id,
        input_resource_ids=input_resource_ids,
        parameters=parsed.model_dump(mode="json"),
        result_kind=ToolResultKind.ARTIFACT.value,
        result_resource_id=artifact_id,
        status="completed",
        commit=False,
    )
    # The single linearization point for the whole derived-result saga.
    ctx.session.commit()
    ctx.session.refresh(artifact)
    return artifact_id


def _artifact_input_ids(parsed: BaseModel) -> list[UUID]:
    artifact_id = getattr(parsed, "artifact_id", None)
    if isinstance(artifact_id, UUID):
        return [artifact_id]
    return []


def record_tool_invocation(
    session: Session,
    *,
    actor_id: UUID,
    project_id: UUID,
    tool_id: str,
    input_resource_ids: list[str],
    parameters: dict[str, Any],
    result_kind: str,
    result_resource_id: UUID | None,
    status: str,
    commit: bool = True,
) -> ToolInvocation:
    """Append one durable local-tool activity record. Never stores secrets:
    `parameters` is the canonical validated model dump (analysis parameters only),
    produced by the caller from the parsed input model, never the raw request.

    `commit=False` leaves the row uncommitted so the single-transaction saga can
    commit the derived artifact and its record together."""
    invocation = ToolInvocation(
        project_id=project_id,
        tool_id=tool_id,
        tool_version=TOOL_VERSION,
        actor_id=actor_id,
        input_resource_ids=input_resource_ids,
        parameters=parameters,
        result_kind=result_kind,
        result_resource_id=result_resource_id,
        status=status,
    )
    session.add(invocation)
    if commit:
        session.commit()
        session.refresh(invocation)
    return invocation


__all__ = ["TOOL_VERSION", "LocalToolRuntime", "record_tool_invocation"]
