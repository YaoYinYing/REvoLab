"""Local Tool registry: the closed, typed execution surface of the Project
Harness.

Only registered `LocalToolSpec` objects may be invoked. Each spec carries a
canonical Pydantic input model (validated at the boundary), a typed output model,
an authority/autonomy class, an execution class (always `local` here), a
side-effect class, and a handler callable. Registration rejects duplicate tool
ids; lookup is exact. There is deliberately NO generic execution entry point:
unknown tool ids, arbitrary code, paths, URLs, SQL or shell never reach a
handler.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from revolab.enums import AgentToolAutonomy, ToolExecutionClass, ToolSideEffectClass
from revolab.schemas import (
    ArtifactInspectCreate,
    ArtifactInspectRead,
    DecisionCommitCreate,
    DecisionCreate,
    DecisionRead,
    EvidenceCreate,
    EvidenceRead,
    PlotSpecRead,
    PlotXyCreate,
    TableDescribeCreate,
    TableDescribeRead,
    TableSelectCreate,
    TableSelectRead,
)
from revolab.tools import analysis, handlers
from revolab.tools.types import HandlerOutput, InvocationContext

Handler = Callable[[InvocationContext, BaseModel, bool], HandlerOutput]


def _inspect(ctx: InvocationContext, parsed: BaseModel, _persist: bool) -> HandlerOutput:
    return handlers.handle_artifact_inspect(ctx, _as(parsed, ArtifactInspectCreate))


def _describe(ctx: InvocationContext, parsed: BaseModel, _persist: bool) -> HandlerOutput:
    return analysis.handle_table_describe(ctx, _as(parsed, TableDescribeCreate))


def _select(ctx: InvocationContext, parsed: BaseModel, persist: bool) -> HandlerOutput:
    return analysis.handle_table_select(ctx, _as(parsed, TableSelectCreate), persist=persist)


def _plot(ctx: InvocationContext, parsed: BaseModel, persist: bool) -> HandlerOutput:
    return analysis.handle_plot_xy(ctx, _as(parsed, PlotXyCreate), persist=persist)


def _evidence(ctx: InvocationContext, parsed: BaseModel, _persist: bool) -> HandlerOutput:
    return handlers.handle_evidence_create(ctx, _as(parsed, EvidenceCreate))


def _record_draft(ctx: InvocationContext, parsed: BaseModel, _persist: bool) -> HandlerOutput:
    return handlers.handle_decision_record_draft(ctx, _as(parsed, DecisionCreate))


def _commit(ctx: InvocationContext, parsed: BaseModel, _persist: bool) -> HandlerOutput:
    return handlers.handle_decision_commit(ctx, _as(parsed, DecisionCommitCreate))


def _as[M: BaseModel](parsed: BaseModel, model: type[M]) -> M:
    # `parsed` is already the exact input_model instance constructed by the
    # runtime; this guarded cast keeps handler wiring type-safe.
    if not isinstance(parsed, model):
        raise TypeError(f"internal tool wiring error: expected {model.__name__}")
    return parsed


@dataclass(frozen=True)
class LocalToolSpec:
    """One registered local tool: its canonical descriptor contract + handler."""

    id: str
    name: str
    description: str
    autonomy: AgentToolAutonomy
    side_effect_class: ToolSideEffectClass
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    # Whether a plain invocation requires owner/member (beyond `persist`).
    requires_mutation: bool
    handler: Handler

    @property
    def execution_class(self) -> ToolExecutionClass:
        return ToolExecutionClass.LOCAL

    @property
    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    @property
    def output_schema(self) -> dict[str, Any]:
        return self.output_model.model_json_schema()


class LocalToolRegistry:
    """Duplicate-rejecting, exact-lookup registry of local Project Tools."""

    def __init__(self) -> None:
        self._tools: dict[str, LocalToolSpec] = {}

    def register(self, spec: LocalToolSpec) -> None:
        if spec.id in self._tools:
            raise ValueError(f"local tool already registered: {spec.id}")
        self._tools[spec.id] = spec

    def get(self, tool_id: str) -> LocalToolSpec:
        return self._tools[tool_id]

    def items(self) -> tuple[LocalToolSpec, ...]:
        return tuple(self._tools.values())

    def __contains__(self, tool_id: str) -> bool:
        return tool_id in self._tools


ALL_LOCAL_TOOLS: tuple[LocalToolSpec, ...] = (
    LocalToolSpec(
        id="artifact.inspect",
        name="Inspect artifact",
        description=(
            "Resolve one ArtifactReference through its owning provider or the "
            "REvoLab ContentStore and return a bounded preview — never a copy "
            "into Core and never credential material."
        ),
        autonomy=AgentToolAutonomy.AUTOMATIC,
        side_effect_class=ToolSideEffectClass.READ_ONLY,
        input_model=ArtifactInspectCreate,
        output_model=ArtifactInspectRead,
        requires_mutation=False,
        handler=_inspect,
    ),
    LocalToolSpec(
        id="table.describe",
        name="Describe table",
        description=(
            "Read a tabular (CSV) artifact and return bounded per-column statistics. "
            "Fails closed on unsupported formats and artifacts beyond the analysis bounds."
        ),
        autonomy=AgentToolAutonomy.AUTOMATIC,
        side_effect_class=ToolSideEffectClass.READ_ONLY,
        input_model=TableDescribeCreate,
        output_model=TableDescribeRead,
        requires_mutation=False,
        handler=_describe,
    ),
    LocalToolSpec(
        id="table.select",
        name="Select table columns",
        description=(
            "Select columns and optionally filter rows of a tabular artifact into a "
            "bounded projection; optionally persist the projection as a derived "
            "ArtifactReference (owner/member)."
        ),
        autonomy=AgentToolAutonomy.AUTOMATIC,
        side_effect_class=ToolSideEffectClass.CREATES_DERIVED_RESULT,
        input_model=TableSelectCreate,
        output_model=TableSelectRead,
        requires_mutation=False,
        handler=_select,
    ),
    LocalToolSpec(
        id="plot.xy",
        name="Plot X-Y",
        description=(
            "Build a structured X-Y plot specification (points, not an image) from "
            "numeric columns; optionally persist it as a derived ArtifactReference "
            "(owner/member). The Tool never owns scientific interpretation."
        ),
        autonomy=AgentToolAutonomy.AUTOMATIC,
        side_effect_class=ToolSideEffectClass.CREATES_DERIVED_RESULT,
        input_model=PlotXyCreate,
        output_model=PlotSpecRead,
        requires_mutation=False,
        handler=_plot,
    ),
    LocalToolSpec(
        id="evidence.create",
        name="Create evidence",
        description="Record a project-scoped typed Evidence claim.",
        autonomy=AgentToolAutonomy.POLICY,
        side_effect_class=ToolSideEffectClass.CREATES_PROJECT_TRUTH,
        input_model=EvidenceCreate,
        output_model=EvidenceRead,
        requires_mutation=True,
        handler=_evidence,
    ),
    LocalToolSpec(
        id="decision.record_draft",
        name="Record decision draft",
        description=(
            "Record a Decision DRAFT through the existing Decision creation service. "
            "A draft is not committed project truth."
        ),
        autonomy=AgentToolAutonomy.POLICY,
        side_effect_class=ToolSideEffectClass.CREATES_PROJECT_TRUTH,
        input_model=DecisionCreate,
        output_model=DecisionRead,
        requires_mutation=True,
        handler=_record_draft,
    ),
    LocalToolSpec(
        id="decision.commit",
        name="Commit decision",
        description=(
            "Explicitly promote a Decision draft to committed project truth (the "
            "promotion gate). Never auto-executed by the Agent loop."
        ),
        autonomy=AgentToolAutonomy.EXPLICIT_ACTION,
        side_effect_class=ToolSideEffectClass.CREATES_PROJECT_TRUTH,
        input_model=DecisionCommitCreate,
        output_model=DecisionRead,
        requires_mutation=True,
        handler=_commit,
    ),
)


def build_default_registry() -> LocalToolRegistry:
    """The fixed Phase-7 local tool set (closed; no dynamic discovery)."""
    registry = LocalToolRegistry()
    for spec in ALL_LOCAL_TOOLS:
        registry.register(spec)
    return registry
