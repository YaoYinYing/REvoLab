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
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from revolab import services
from revolab.domain.errors import ValidationError
from revolab.models import Decision
from revolab.schemas import ContextSelectionCreate, ProjectContextRead, ToolCatalogRead


@dataclass(frozen=True)
class AgentProposal:
    title: str
    statement: str
    next_actions: list[str]
    cites: list[dict[str, Any]]
    selects: list[dict[str, Any]]


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
            {
                "target_id": context.revisions[0].revision_id,
                "target_kind": "scientific_object_revision",
            }
        ]
    else:
        selects = [
            {
                "target_id": context.series[0].series_id,
                "target_kind": "scientific_object_series",
            }
        ]
    series = context.series[0]
    return AgentProposal(
        title=objective,
        statement=f"Select {series.name} for experimental validation.",
        next_actions=["validate experimentally"],
        cites=[
            {"evidence_id": evidence.evidence_id, "cited_as": "supports"}
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
        cites=proposal.cites,
        selects=proposal.selects,
    )
