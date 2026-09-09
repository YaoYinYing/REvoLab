"""Local-tool handlers that route through existing typed domain operations.

These handlers are the implementation adapter behind the Tool Harness for
operations that already exist as typed domain commands. They never perform a raw
write: `evidence.create`, `decision.record_draft`, and `decision.commit` call the
same `services` used by the ordinary API surface; `artifact.inspect` reuses the
bounded artifact-inspection boundary.
"""

from __future__ import annotations

from revolab import queries, services
from revolab.enums import ToolResultKind
from revolab.schemas import (
    ArtifactInspectCreate,
    DecisionCommitCreate,
    DecisionCreate,
    DecisionRead,
    EvidenceCreate,
    EvidenceRead,
)
from revolab.tools.artifact_inspection import inspect_artifact
from revolab.tools.types import HandlerOutput, InvocationContext


def handle_artifact_inspect(ctx: InvocationContext, parsed: ArtifactInspectCreate) -> HandlerOutput:
    result = inspect_artifact(
        ctx.session,
        ctx.registry,
        ctx.secret_store,
        ctx.content_store,
        ctx.actor_id,
        ctx.project_id,
        parsed.artifact_id,
        preview_limit=parsed.preview_limit,
    )
    return HandlerOutput(kind=ToolResultKind.EPHEMERAL, value=result)


def handle_evidence_create(ctx: InvocationContext, parsed: EvidenceCreate) -> HandlerOutput:
    evidence = services.create_evidence(
        ctx.session,
        ctx.actor_id,
        ctx.project_id,
        kind=parsed.kind.value,
        role=parsed.role.value,
        label=parsed.label,
        interpretation=parsed.interpretation,
        polarity=parsed.polarity.value,
        confidence=parsed.confidence.value if parsed.confidence else None,
        confidence_source=parsed.confidence_source,
        scope=parsed.scope,
        source_kind=parsed.source_kind.value if parsed.source_kind else None,
        source_id=parsed.source_id,
        target_kind=parsed.target_kind.value,
        target_id=parsed.target_id,
    )
    value = EvidenceRead(**queries.evidence_summary(evidence, frozen=False))
    return HandlerOutput(kind=ToolResultKind.EVIDENCE, value=value, resource_id=evidence.id)


def handle_decision_record_draft(ctx: InvocationContext, parsed: DecisionCreate) -> HandlerOutput:
    decision = services.create_decision(
        ctx.session,
        ctx.actor_id,
        ctx.project_id,
        title=parsed.title,
        statement=parsed.statement,
        next_actions=parsed.next_actions,
        cites=[{"evidence_id": c.evidence_id, "cited_as": c.cited_as.value} for c in parsed.cites],
        selects=[{"target_id": s.target_id, "target_kind": s.target_kind.value} for s in parsed.selects],
    )
    value = DecisionRead(**queries.decision_summary(ctx.session, decision))
    return HandlerOutput(kind=ToolResultKind.DECISION, value=value, resource_id=decision.id)


def handle_decision_commit(ctx: InvocationContext, parsed: DecisionCommitCreate) -> HandlerOutput:
    decision = services.commit_decision(ctx.session, ctx.actor_id, ctx.project_id, parsed.decision_id)
    value = DecisionRead(**queries.decision_summary(ctx.session, decision))
    return HandlerOutput(kind=ToolResultKind.DECISION, value=value, resource_id=decision.id)


__all__ = [
    "handle_artifact_inspect",
    "handle_decision_commit",
    "handle_decision_record_draft",
    "handle_evidence_create",
]
