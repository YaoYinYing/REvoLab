"""Knowledge / Decision domain operations (identity-free).

Decision draft/commit/supersede lifecycle. Membership and commit authority are
validated at the command boundary; this module enforces the frozen lifecycle
rules and the project-context write-time invariance (no ghost knowledge), and
materializes the immutable knowledge edges only at commit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from revolab.domain import persistence
from revolab.domain.errors import ConflictError, NotFoundError, ValidationError
from revolab.enums import CitedAs, DecisionStatus, ResourceKind
from revolab.models import (
    Decision,
    DecisionEvidence,
    DecisionSupersedes,
    DecisionTarget,
    Evidence,
)

_SELECT_TARGET_KINDS = {ResourceKind.SCIENTIFIC_OBJECT_SERIES, ResourceKind.SCIENTIFIC_OBJECT_REVISION}


def create_decision_row(
    session: Session,
    project_id: UUID,
    actor_id: UUID,
    *,
    title: str,
    statement: str,
    next_actions: list[str],
    cites: list[dict[str, Any]],
    selects: list[dict[str, Any]],
) -> Decision:
    decision = Decision(
        project_id=project_id,
        title=title,
        statement=statement,
        status=DecisionStatus.DRAFT.value,
        next_actions=next_actions,
        created_by=actor_id,
    )
    session.add(decision)
    session.flush()
    _apply_draft_cites(session, project_id, decision, cites)
    _apply_draft_selects(session, project_id, decision, selects)
    session.commit()
    session.refresh(decision)
    return decision


def update_decision_row(
    session: Session,
    project_id: UUID,
    decision_id: UUID,
    *,
    title: str | None,
    statement: str | None,
    next_actions: list[str] | None,
    cites: list[dict[str, Any]] | None,
    selects: list[dict[str, Any]] | None,
) -> Decision:
    decision = _require_project_decision(session, project_id, decision_id)
    if decision.status != DecisionStatus.DRAFT.value:
        raise ConflictError("only a draft decision can be edited")
    if title is not None:
        decision.title = title
    if statement is not None:
        decision.statement = statement
    if next_actions is not None:
        decision.next_actions = next_actions
    if cites is not None:
        _apply_draft_cites(session, project_id, decision, cites)
    if selects is not None:
        _apply_draft_selects(session, project_id, decision, selects)
    session.commit()
    session.refresh(decision)
    return decision


def commit_decision_row(session: Session, project_id: UUID, decision_id: UUID, actor_id: UUID) -> Decision:
    decision = _require_project_decision(session, project_id, decision_id)
    if decision.status == DecisionStatus.COMMITTED.value:
        raise ConflictError("decision is already committed")
    if decision.status != DecisionStatus.DRAFT.value:
        raise ConflictError("only a draft decision can be committed")

    cites = decision.draft_cites or []
    selects = decision.draft_selects or []
    for cite in cites:
        _require_project_evidence(session, project_id, UUID(cite["evidence_id"]))
    for item in selects:
        target_id = UUID(item["target_id"])
        if item["target_kind"] not in {k.value for k in _SELECT_TARGET_KINDS}:
            raise ValidationError("decision select target must be a series or revision")
        persistence.require_visible(session, project_id, target_id)

    decision.status = DecisionStatus.COMMITTED.value
    decision.committed_by = actor_id
    decision.committed_at = datetime.now(UTC)
    decision.draft_cites = []
    decision.draft_selects = []
    for cite in cites:
        session.add(
            DecisionEvidence(
                decision_id=decision.id,
                evidence_id=UUID(cite["evidence_id"]),
                cited_as=CitedAs(cite["cited_as"]).value,
            )
        )
    for item in selects:
        session.add(
            DecisionTarget(
                decision_id=decision.id,
                target_id=UUID(item["target_id"]),
                target_kind=item["target_kind"],
            )
        )
    session.commit()
    session.refresh(decision)
    return decision


def supersede_decision_row(
    session: Session,
    project_id: UUID,
    actor_id: UUID,
    superseding_decision_id: UUID,
    superseded_decision_id: UUID,
) -> DecisionSupersedes:
    new_decision = _require_project_decision(session, project_id, superseding_decision_id)
    old_decision = _require_project_decision(session, project_id, superseded_decision_id)
    if new_decision.id == old_decision.id:
        raise ValidationError("a decision cannot supersede itself")
    if old_decision.status != DecisionStatus.COMMITTED.value:
        raise ConflictError("a decision can only supersede a committed decision")
    if new_decision.status != DecisionStatus.COMMITTED.value:
        raise ConflictError("a superseding decision must itself be committed")
    link = DecisionSupersedes(
        project_id=project_id,
        decision_id=new_decision.id,
        superseded_decision_id=old_decision.id,
        created_by=actor_id,
    )
    session.add(link)
    session.commit()
    session.refresh(link)
    return link


def _require_project_decision(session: Session, project_id: UUID, decision_id: UUID) -> Decision:
    decision = session.get(Decision, decision_id)
    if decision is None or decision.project_id != project_id:
        raise NotFoundError("decision not found in project")
    return decision


def _require_project_evidence(session: Session, project_id: UUID, evidence_id: UUID) -> Evidence:
    evidence = session.get(Evidence, evidence_id)
    if evidence is None or evidence.project_id != project_id:
        raise NotFoundError("evidence not found in project")
    return evidence


def _apply_draft_cites(
    session: Session, project_id: UUID, decision: Decision, cites: list[dict[str, Any]]
) -> None:
    cleaned: list[dict[str, Any]] = []
    seen: set[UUID] = set()
    for cite in cites:
        evidence_id = cite["evidence_id"]
        _require_project_evidence(session, project_id, evidence_id)
        if evidence_id in seen:
            raise ValidationError("duplicate evidence citation in decision draft")
        seen.add(evidence_id)
        cited_as = cite.get("cited_as", CitedAs.SUPPORTS.value)
        try:
            CitedAs(cited_as)
        except ValueError as exc:
            raise ValidationError(f"unknown cited_as {cited_as!r}") from exc
        cleaned.append({"evidence_id": str(evidence_id), "cited_as": cited_as})
    decision.draft_cites = cleaned


def _apply_draft_selects(
    session: Session, project_id: UUID, decision: Decision, selects: list[dict[str, Any]]
) -> None:
    cleaned: list[dict[str, Any]] = []
    seen: set[UUID] = set()
    for item in selects:
        target_id = item["target_id"]
        target_kind = item["target_kind"]
        if target_kind not in {k.value for k in _SELECT_TARGET_KINDS}:
            raise ValidationError("decision select target must be a series or revision")
        if persistence.resource_kind(session, target_id).value != target_kind:
            raise ValidationError("select target kind does not match the registered resource kind")
        persistence.require_visible(session, project_id, target_id)
        if target_id in seen:
            raise ValidationError("duplicate select target in decision draft")
        seen.add(target_id)
        cleaned.append({"target_id": str(target_id), "target_kind": target_kind})
    decision.draft_selects = cleaned
