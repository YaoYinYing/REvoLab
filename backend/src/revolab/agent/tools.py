"""ToolCatalog — the typed projection of what one Actor may call in one Project.

Tools derive from exactly two existing sources (TODO.md section 6):

1. REvoLab typed domain operations (a fixed, procedurally declared subset);
2. available Provider capabilities (projected through the existing non-secret
   `catalog_entries`, only when the Actor's derived availability is AVAILABLE).

Input/output JSON Schemas are derived from the canonical Pydantic domain/OpenAPI
models (`model_json_schema`) or from small mechanical fragments for scalar
identity/opaque-string inputs (`_uuid_input` / `_string_input`) — never by copying
an enum or a full parameter list by hand. No raw SQL, arbitrary HTTP, shell,
credential CRUD, secret-store, generic relation writer, or generic database
update is ever projected. The catalog is a read-only projection: it does not
invoke anything.
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
    ToolSource,
)
from revolab.schemas import (
    ArtifactInspectRead,
    ComputeArtifactRead,
    ComputeRunStatusRead,
    ComputeSubmissionCreate,
    ComputeSubmissionRead,
    ComputeTaskKindRead,
    ComputeTaskKindSchemaRead,
    ContextSelectionCreate,
    DecisionCreate,
    DecisionRead,
    EvidenceCreate,
    EvidenceRead,
    ProjectContextRead,
    ToolCatalogRead,
    ToolDescriptorRead,
)

_EMPTY_OBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_BINARY_SCHEMA: dict[str, Any] = {"type": "string", "format": "binary"}

# artifact.inspect carries the same bounded preview_limit as the wire endpoint
# (api.py Query ge=0 le=65536), so the tool contract stays faithful to it.
_ARTIFACT_INSPECT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "artifact_id": {"type": "string", "format": "uuid"},
        "preview_limit": {
            "type": "integer",
            "minimum": 0,
            "maximum": 65536,
            "default": 2048,
        },
    },
    "required": ["artifact_id"],
    "additionalProperties": False,
}


def _uuid_input(field: str) -> dict[str, Any]:
    """A scalar opaque-UUID input schema for a tool whose only argument is one
    durable REvoLab identity. Identity is an opaque UUID, never a path."""
    return {
        "type": "object",
        "properties": {field: {"type": "string", "format": "uuid"}},
        "required": [field],
        "additionalProperties": False,
    }


def _string_input(field: str, *, min_length: int = 1, max_length: int = 300) -> dict[str, Any]:
    """A scalar opaque-string input schema (provider-native identity, e.g. a task
    `kind_id`) for a tool whose only argument is one provider-typed string."""
    return {
        "type": "object",
        "properties": {field: {"type": "string", "minLength": min_length, "maxLength": max_length}},
        "required": [field],
        "additionalProperties": False,
    }


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()


def _list_schema(model: type[BaseModel]) -> dict[str, Any]:
    # `model` is a runtime type variable; mypy cannot spell `list[model]`, but
    # pydantic's TypeAdapter accepts the runtime list type here.
    return TypeAdapter(list[model]).json_schema()  # type: ignore[valid-type]


def _descriptor(
    *,
    id: str,
    name: str,
    description: str,
    autonomy: AgentToolAutonomy,
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
) -> ToolCatalogRead:
    """Project the executable tool set for this Actor in this Project.

    Fail-closed on non-membership/tombstone via `readable_membership`. Unavailable
    provider capabilities are omitted entirely (never exposed as executable).
    """
    membership = readable_membership(session, actor_id, project_id)
    can_mutate = membership.role in {Role.OWNER, Role.MEMBER}

    tools: list[ToolDescriptorRead] = _domain_tools(can_mutate)
    tools.extend(
        _provider_tools(
            session,
            actor_id,
            project_id,
            registry,
        )
    )
    return ToolCatalogRead(project_id=project_id, tools=tools)


class ToolCatalog:
    """Small stateless façade naming the Phase-6 abstraction (ADR-0013)."""

    def build(
        self,
        session: Session,
        actor_id: UUID,
        project_id: UUID,
        registry: DriverRegistry,
    ) -> ToolCatalogRead:
        return build_tool_catalog(session, actor_id, project_id, registry)


def _domain_tools(can_mutate: bool) -> list[ToolDescriptorRead]:
    write_reason = None if can_mutate else "requires owner or member membership"
    return [
        _descriptor(
            id="context.build",
            name="Build project context",
            description="Read bounded, Project-scoped context for one Agent turn.",
            autonomy=AgentToolAutonomy.AUTOMATIC,
            available=True,
            availability_reason=None,
            input_schema=_schema(ContextSelectionCreate),
            output_schema=_schema(ProjectContextRead),
        ),
        _descriptor(
            id="artifact.inspect",
            name="Inspect artifact",
            description=(
                "Resolve one ArtifactReference through its owning provider or the "
                "REvoLab ContentStore and return a bounded preview — never a copy "
                "into Core and never credential material."
            ),
            autonomy=AgentToolAutonomy.AUTOMATIC,
            available=True,
            availability_reason=None,
            input_schema=_ARTIFACT_INSPECT_INPUT_SCHEMA,
            output_schema=_schema(ArtifactInspectRead),
        ),
        _descriptor(
            id="evidence.create",
            name="Create evidence",
            description="Record a project-scoped typed Evidence claim.",
            autonomy=AgentToolAutonomy.POLICY,
            available=can_mutate,
            availability_reason=write_reason,
            input_schema=_schema(EvidenceCreate),
            output_schema=_schema(EvidenceRead),
        ),
        _descriptor(
            id="decision.record_draft",
            name="Record decision draft",
            description=(
                "Record an Agent proposal as a Decision DRAFT through the existing "
                "Decision creation service. A draft is not committed project truth."
            ),
            autonomy=AgentToolAutonomy.POLICY,
            available=can_mutate,
            availability_reason=write_reason,
            input_schema=_schema(DecisionCreate),
            output_schema=_schema(DecisionRead),
        ),
        _descriptor(
            id="decision.commit",
            name="Commit decision",
            description=(
                "Explicitly promote a Decision draft to committed project truth "
                "(the promotion gate). Never auto-executed by the Agent loop."
            ),
            autonomy=AgentToolAutonomy.EXPLICIT_ACTION,
            available=can_mutate,
            availability_reason=write_reason,
            input_schema=_uuid_input("decision_id"),
            output_schema=_schema(DecisionRead),
        ),
    ]


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
                # Unavailable capabilities are not exposed as executable tools.
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
                        available=True,
                        availability_reason=None,
                        input_schema=_uuid_input("artifact_id"),
                        output_schema=_BINARY_SCHEMA,
                        source=ToolSource.PROVIDER,
                        provider_key=provider_key,
                        capability_kind=CapabilityKind.ARTIFACT_RESOLUTION,
                    )
                )
            # SEARCH / DESIGN / INTERACTIVE_HANDOFF have no Phase-6 DOMAIN driver
            # yet; they are intentionally not fabricated here (skills/providers
            # exist only when the provider exists).
    return tools


def _compute_tools(provider_key: str, display_name: str) -> list[ToolDescriptorRead]:
    return [
        _descriptor(
            id=f"{provider_key}.compute.list_task_kinds",
            name=f"{display_name}: list task kinds",
            description="Discover the provider's task kinds (schema-as-data).",
            autonomy=AgentToolAutonomy.AUTOMATIC,
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
            available=True,
            availability_reason=None,
            input_schema=_uuid_input("run_id"),
            output_schema=_list_schema(ComputeArtifactRead),
            source=ToolSource.PROVIDER,
            provider_key=provider_key,
            capability_kind=CapabilityKind.COMPUTE,
        ),
    ]
