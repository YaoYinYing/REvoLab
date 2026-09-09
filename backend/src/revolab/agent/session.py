"""Ephemeral AgentSession + deterministic proposal (TODO.md section 10 / 9).

AgentSession is a process-local value object, NOT a persistence model and NOT a
durable scientific truth store. Chat messages are never written into the
scientific graph. The `ProposalAgent` is deterministic (a real LLM integration is
out of scope): it reasons over a ProjectContext and emits an `AgentProposal` whose
only durable effect is recording a Decision **draft** through the existing typed
Decision creation service — never committed truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from revolab import services
from revolab.domain.errors import ValidationError
from revolab.enums import CitedAs, ResourceKind
from revolab.models import Decision
from revolab.schemas import (
    CitationCreate,
    ContextSelectionCreate,
    ProjectContextRead,
    SelectTargetCreate,
    ToolCatalogRead,
)


@dataclass(frozen=True)
class AgentProposal:
    """A typed proposal value over the canonical wire models (ADR-0007/0014:
    single source of truth, never a duplicated parameter list)."""

    title: str
    statement: str
    next_actions: list[str]
    cites: list[CitationCreate]
    selects: list[SelectTargetCreate]


@dataclass
class AgentSession:
    """Ephemeral per-turn assembly. Conversation is working memory, never truth."""

    actor_id: UUID
    project_id: UUID
    session_id: UUID = field(default_factory=uuid4)
    selection: ContextSelectionCreate | None = None
    context: ProjectContextRead | None = None
    tool_catalog: ToolCatalogRead | None = None
    loaded_skill_ids: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


def propose_selection(
    context: ProjectContextRead,
    *,
    objective: str = "Select a candidate for experimental validation",
) -> AgentProposal:
    """Deterministic fake Agent: choose the first selected series/revision and
    propose validating it, citing any Evidence already in context as support.
    This exists only to make the propose -> draft -> commit slice executable."""
    if not context.series:
        raise ValidationError("cannot propose without an object in project context")
    if context.revisions:
        selects = [
            SelectTargetCreate(
                target_id=context.revisions[0].revision_id,
                target_kind=ResourceKind.SCIENTIFIC_OBJECT_REVISION,
            )
        ]
    else:
        selects = [
            SelectTargetCreate(
                target_id=context.series[0].series_id,
                target_kind=ResourceKind.SCIENTIFIC_OBJECT_SERIES,
            )
        ]
    series = context.series[0]
    return AgentProposal(
        title=objective,
        statement=f"Select {series.name} for experimental validation.",
        next_actions=["validate experimentally"],
        cites=[
            CitationCreate(evidence_id=evidence.evidence_id, cited_as=CitedAs.SUPPORTS)
            for evidence in context.evidence
        ],
        selects=selects,
    )


def record_proposal(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    proposal: AgentProposal,
) -> Decision:
    """Record an Agent proposal as a Decision DRAFT via the existing typed domain
    service — the same application service normal API callers use. There is no
    Agent-specific persistence and no path to a committed Decision here."""
    return services.create_decision(
        session,
        actor_id,
        project_id,
        title=proposal.title,
        statement=proposal.statement,
        next_actions=proposal.next_actions,
        cites=[
            {"evidence_id": citation.evidence_id, "cited_as": citation.cited_as.value}
            for citation in proposal.cites
        ],
        selects=[
            {"target_id": select.target_id, "target_kind": select.target_kind.value}
            for select in proposal.selects
        ],
    )
