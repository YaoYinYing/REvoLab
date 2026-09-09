"""Project ToolCatalog — the canonical executable-tool projection.

One Actor in one Project resolves exactly one ToolCatalog, consumed by both the
human workspace and the Agent (TODO.md section 16). Local tools are the closed
`LocalToolRegistry`; remote tools are the existing Provider capabilities
projected through the non-secret Provider Catalog (REvoCompute compute +
artifact resolution). Unavailable provider capabilities are omitted entirely;
local tools are always listed with their current availability. This is a
read-only projection: it never invokes anything.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, TypeAdapter
from sqlalchemy.orm import Session

from revolab import services
from revolab.domain.identity import readable_membership
from revolab.domain.provider import catalog_entries
from revolab.drivers import DriverRegistry
from revolab.enums import (
    AgentToolAutonomy,
    CapabilityAvailability,
    CapabilityKind,
    Role,
    ToolExecutionClass,
    ToolSideEffectClass,
    ToolSource,
)
from revolab.schemas import (
    ComputeArtifactRead,
    ComputeRunStatusRead,
    ComputeSubmissionCreate,
    ComputeSubmissionRead,
    ComputeTaskKindRead,
    ComputeTaskKindSchemaRead,
    ToolCatalogRead,
    ToolDescriptorRead,
)
from revolab.tools.registry import LocalToolRegistry

_EMPTY_OBJECT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
_BINARY_SCHEMA: dict[str, Any] = {"type": "string", "format": "binary"}


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()


def _list_schema(model: type[BaseModel]) -> dict[str, Any]:
    return TypeAdapter(list[model]).json_schema()  # type: ignore[valid-type]


def _uuid_input(field: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {field: {"type": "string", "format": "uuid"}},
        "required": [field],
        "additionalProperties": False,
    }


def _string_input(field: str, *, min_length: int = 1, max_length: int = 300) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {field: {"type": "string", "minLength": min_length, "maxLength": max_length}},
        "required": [field],
        "additionalProperties": False,
    }


def _descriptor(
    *,
    id: str,
    name: str,
    description: str,
    autonomy: AgentToolAutonomy,
    execution_class: ToolExecutionClass,
    side_effect_class: ToolSideEffectClass,
    available: bool,
    availability_reason: str | None,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    source: ToolSource = ToolSource.DOMAIN,
    provider_key: str | None = None,
    capability_kind: CapabilityKind | None = None,
) -> ToolDescriptorRead:
    return ToolDescriptorRead(
        id=id,
        name=name,
        description=description,
        source=source,
        provider_key=provider_key,
        capability_kind=capability_kind,
        autonomy=autonomy,
        execution_class=execution_class,
        side_effect_class=side_effect_class,
        available=available,
        availability_reason=availability_reason,
        input_schema=input_schema,
        output_schema=output_schema,
    )


def build_tool_catalog(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    registry: DriverRegistry,
    local_registry: LocalToolRegistry | None = None,
) -> ToolCatalogRead:
    """Project the executable tool set for this Actor in this Project.

    Fail-closed on non-membership/tombstone via `readable_membership`.
    Unavailable provider capabilities are omitted entirely.
    """
    _local_registry = local_registry if local_registry is not None else _default_local_registry()
    membership = readable_membership(session, actor_id, project_id)
    can_mutate = membership.role in {Role.OWNER, Role.MEMBER}

    tools: list[ToolDescriptorRead] = _local_tools(_local_registry, can_mutate)
    tools.extend(_provider_tools(session, actor_id, project_id, registry))
    return ToolCatalogRead(project_id=project_id, tools=tools)


class ToolCatalog:
    """Small stateless façade naming the Project Tool Harness abstraction."""

    def build(
        self,
        session: Session,
        actor_id: UUID,
        project_id: UUID,
        registry: DriverRegistry,
        local_registry: LocalToolRegistry | None = None,
    ) -> ToolCatalogRead:
        return build_tool_catalog(session, actor_id, project_id, registry, local_registry)


def _default_local_registry() -> LocalToolRegistry:
    from revolab.tools.registry import build_default_registry

    return build_default_registry()


def _local_tools(registry: LocalToolRegistry, can_mutate: bool) -> list[ToolDescriptorRead]:
    write_reason = None if can_mutate else "requires owner or member membership"
    descriptors: list[ToolDescriptorRead] = []
    for spec in registry.items():
        available = can_mutate if spec.requires_mutation else True
        descriptors.append(
            _descriptor(
                id=spec.id,
                name=spec.name,
                description=spec.description,
                autonomy=spec.autonomy,
                execution_class=spec.execution_class,
                side_effect_class=spec.side_effect_class,
                available=available,
                availability_reason=None if available else write_reason,
                input_schema=spec.input_schema,
                output_schema=spec.output_schema,
            )
        )
    return descriptors


def _provider_tools(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    registry: DriverRegistry,
) -> list[ToolDescriptorRead]:
    tools: list[ToolDescriptorRead] = []
    for entry in catalog_entries(
        session,
        actor_id,
        registry,
        policy_permits=lambda kind: services.project_policy_permits(
            session, actor_id, project_id, kind
        ),
    ):
        provider_key = entry["key"]
        display_name = entry["name"]
        for capability in entry["capabilities"]:
            if capability["availability"] is not CapabilityAvailability.AVAILABLE:
                continue
            kind = capability["kind"]
            if kind is CapabilityKind.COMPUTE:
                tools.extend(_compute_tools(provider_key, display_name))
            elif kind is CapabilityKind.ARTIFACT_RESOLUTION:
                tools.append(
                    _descriptor(
                        id=f"{provider_key}.artifact.resolve",
                        name=f"{display_name}: resolve artifact",
                        description=(
                            "Live-resolve an external ArtifactReference through its "
                            "owning provider (bytes are the provider's, never copied)."
                        ),
                        autonomy=AgentToolAutonomy.AUTOMATIC,
                        execution_class=ToolExecutionClass.REMOTE,
                        side_effect_class=ToolSideEffectClass.READ_ONLY,
                        available=True,
                        availability_reason=None,
                        input_schema=_uuid_input("artifact_id"),
                        output_schema=_BINARY_SCHEMA,
                        source=ToolSource.PROVIDER,
                        provider_key=provider_key,
                        capability_kind=CapabilityKind.ARTIFACT_RESOLUTION,
                    )
                )
    return tools


def _compute_tools(provider_key: str, display_name: str) -> list[ToolDescriptorRead]:
    return [
        _descriptor(
            id=f"{provider_key}.compute.list_task_kinds",
            name=f"{display_name}: list task kinds",
            description="Discover the provider's task kinds (schema-as-data).",
            autonomy=AgentToolAutonomy.AUTOMATIC,
            execution_class=ToolExecutionClass.REMOTE,
            side_effect_class=ToolSideEffectClass.READ_ONLY,
            available=True,
            availability_reason=None,
            input_schema=_EMPTY_OBJECT_SCHEMA,
            output_schema=_list_schema(ComputeTaskKindRead),
            source=ToolSource.PROVIDER,
            provider_key=provider_key,
            capability_kind=CapabilityKind.COMPUTE,
        ),
        _descriptor(
            id=f"{provider_key}.compute.task_schema",
            name=f"{display_name}: task schema",
            description="Read one task kind's parameter schema (provider data).",
            autonomy=AgentToolAutonomy.AUTOMATIC,
            execution_class=ToolExecutionClass.REMOTE,
            side_effect_class=ToolSideEffectClass.READ_ONLY,
            available=True,
            availability_reason=None,
            input_schema=_string_input("kind_id", min_length=1, max_length=300),
            output_schema=_schema(ComputeTaskKindSchemaRead),
            source=ToolSource.PROVIDER,
            provider_key=provider_key,
            capability_kind=CapabilityKind.COMPUTE,
        ),
        _descriptor(
            id=f"{provider_key}.compute.submit",
            name=f"{display_name}: submit task",
            description=(
                "Submit one compute task. External compute submission is an explicit "
                "action: never hidden, never auto-executed by the Agent loop."
            ),
            autonomy=AgentToolAutonomy.EXPLICIT_ACTION,
            execution_class=ToolExecutionClass.REMOTE,
            side_effect_class=ToolSideEffectClass.EXTERNAL_ACTION,
            available=True,
            availability_reason=None,
            input_schema=_schema(ComputeSubmissionCreate),
            output_schema=_schema(ComputeSubmissionRead),
            source=ToolSource.PROVIDER,
            provider_key=provider_key,
            capability_kind=CapabilityKind.COMPUTE,
        ),
        _descriptor(
            id=f"{provider_key}.compute.run_status",
            name=f"{display_name}: run status",
            description=(
                "Resolve live run state on demand (never copied into Core). "
                "`run_id` is the REvoLab RunReference resource UUID, resolved to "
                "the provider-native run id by the existing run-status path."
            ),
            autonomy=AgentToolAutonomy.AUTOMATIC,
            execution_class=ToolExecutionClass.REMOTE,
            side_effect_class=ToolSideEffectClass.READ_ONLY,
            available=True,
            availability_reason=None,
            input_schema=_uuid_input("run_id"),
            output_schema=_schema(ComputeRunStatusRead),
            source=ToolSource.PROVIDER,
            provider_key=provider_key,
            capability_kind=CapabilityKind.COMPUTE,
        ),
        _descriptor(
            id=f"{provider_key}.compute.list_artifacts",
            name=f"{display_name}: list artifacts",
            description=(
                "Enumerate a run's ArtifactReference cards + provenance edges. "
                "`run_id` is the REvoLab RunReference resource UUID, resolved to "
                "the provider-native run id by the existing run-artifact path."
            ),
            autonomy=AgentToolAutonomy.POLICY,
            execution_class=ToolExecutionClass.REMOTE,
            side_effect_class=ToolSideEffectClass.CREATES_DERIVED_RESULT,
            available=True,
            availability_reason=None,
            input_schema=_uuid_input("run_id"),
            output_schema=_list_schema(ComputeArtifactRead),
            source=ToolSource.PROVIDER,
            provider_key=provider_key,
            capability_kind=CapabilityKind.COMPUTE,
        ),
    ]


__all__ = ["ToolCatalog", "build_tool_catalog"]
