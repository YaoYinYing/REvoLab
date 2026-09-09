"""ContextBuilder — the read-only assembler of per-turn ProjectContext.

Responsibilities (TODO.md section 3):

    ContextSelection -> existing Project-scoped read/query services -> ProjectContext

It never writes database state, never changes ProjectResourceLink or membership,
never modifies Evidence/Decisions, never submits compute, and never resolves
credentials. Authorization is reused from the existing Identity/read-projection
policy (`readable_membership` + `ProjectResourceLink` visibility); there is no
second authorization implementation here.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from revolab import queries, services
from revolab.agent.skills import select_skills
from revolab.domain.errors import AuthorizationError, NotFoundError
from revolab.domain.identity import readable_membership
from revolab.domain.provenance import is_frozen
from revolab.domain.provider import catalog_entries
from revolab.drivers import DriverRegistry
from revolab.enums import DecisionStatus, EvidenceTargetKind, RelationType, ResourceKind, Role
from revolab.models import (
    Decision,
    DecisionEvidence,
    DecisionTarget,
    Evidence,
    GlobalProvenanceEdge,
    Project,
    ScientificObjectRevision,
    ScientificObjectSeries,
)
from revolab.schemas import (
    BudgetReportRead,
    ContextSelectionCreate,
    DecisionRefRead,
    EdgeRead,
    EvidenceRefRead,
    ProjectContextRead,
    ReferenceHeaderRead,
    RevisionRefRead,
    SeriesRefRead,
)

# Reference kinds that may surface as identity-card headers in context.
_REFERENCE_KINDS = frozenset(
    {
        ResourceKind.RUN_REFERENCE,
        ResourceKind.SESSION_REFERENCE,
        ResourceKind.ARTIFACT_REFERENCE,
        ResourceKind.LITERATURE_REFERENCE,
        ResourceKind.EXTERNAL_REFERENCE,
    }
)


def build_context(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    registry: DriverRegistry,
    selection: ContextSelectionCreate | None = None,
) -> ProjectContextRead:
    """Assemble one immutable, bounded ProjectContext for one Agent turn.

    Fail-closed: a non-member, a tombstoned Project, or any selection naming a
    resource outside this Project's read lens raises before any context is
    assembled.
    """
    selection = selection or ContextSelectionCreate()
    membership = readable_membership(session, actor_id, project_id)
    project = session.get(Project, project_id)
    if project is None or project.deleted_at is not None:
        # `readable_membership` already guards this; kept as a fail-closed backstop.
        raise AuthorizationError("project is not active")

    visible = queries.visible_resources(session, project_id)
    explicit = bool(selection.series_ids or selection.revision_ids)

    # Resolve and validate the declarative selection before assembling anything.
    selected_series_ids: set[UUID] = set()
    selected_revision_ids: set[UUID] = set()
    for series_id in selection.series_ids or []:
        if visible.get(series_id) is not ResourceKind.SCIENTIFIC_OBJECT_SERIES:
            raise AuthorizationError("selected series is not visible in this project")
        selected_series_ids.add(series_id)
    for revision_id in selection.revision_ids or []:
        if visible.get(revision_id) is not ResourceKind.SCIENTIFIC_OBJECT_REVISION:
            raise AuthorizationError("selected revision is not visible in this project")
        revision = session.get(ScientificObjectRevision, revision_id)
        if revision is None:
            raise NotFoundError("selected revision not found")
        selected_revision_ids.add(revision_id)
        # Revision => series closure: the owning series skeleton is implied, but
        # sibling revisions of that series are NEVER added (Phase-5 privacy).
        selected_series_ids.add(revision.series_id)

    if not explicit:
        return _implicit_context(session, project, project_id, membership, registry, selection, visible)

    # Explicit selection: the active question. Skeleton + visible revisions of
    # the selected series + bounded neighborhood of relations/references/evidence/
    # decisions. Selected revisions are included even if their series is capped.
    all_series = _visible_series(session, visible)
    series_by_id = {series.series_id: series for series in all_series}
    series_refs: list[SeriesRefRead] = []
    revision_refs: list[RevisionRefRead] = []
    truncated = len(selected_series_ids) > selection.max_series

    ordered_series_ids = sorted(selected_series_ids)
    for series_id in ordered_series_ids[: selection.max_series]:
        series = series_by_id.get(series_id) or session.get(ScientificObjectSeries, series_id)
        if series is None:
            raise NotFoundError("selected series not found")
        series_refs.append(_series_ref(series))
        # Visible revisions only (a Project without the revision link never sees
        # it, even when the series is shared). `max_revisions` is a global budget
        # across the whole turn, not a per-series budget.
        revisions = _visible_revisions(session, series.series_id, visible)
        for revision in revisions:
            if len(revision_refs) >= selection.max_revisions:
                truncated = True
                break
            revision_refs.append(_revision_ref(revision))
        if len(revisions) > selection.max_revisions:
            truncated = True

    # Explicitly selected revisions whose series was capped out still appear as
    # addressable revision refs (they were validated visible above), still obeying
    # the single global `max_revisions` budget.
    have_revision_ids = {ref.revision_id for ref in revision_refs}
    for revision_id in sorted(selected_revision_ids):
        if revision_id in have_revision_ids:
            continue
        if len(revision_refs) >= selection.max_revisions:
            truncated = True
            break
        revision = session.get(ScientificObjectRevision, revision_id)
        if revision is not None:
            revision_refs.append(_revision_ref(revision))

    start_nodes = {ref.series_id for ref in series_refs} | {ref.revision_id for ref in revision_refs}
    reachable = _reachable(session, start_nodes, visible, selection.graph_depth)

    relations: list[EdgeRead] = []
    if selection.include_relations and selection.graph_depth > 0:
        for edge in _visible_edges(session, visible):
            if edge.source_id in reachable or edge.target_id in reachable:
                if len(relations) >= selection.max_relations:
                    truncated = True
                    break
                relations.append(_edge_ref(edge))
        if len(relations) > selection.max_relations:
            relations = relations[: selection.max_relations]
            truncated = True

    references: list[ReferenceHeaderRead] = []
    if selection.include_references:
        reference_ids = sorted(
            resource_id
            for resource_id, kind in visible.items()
            if kind in _REFERENCE_KINDS and resource_id in reachable
        )
        for resource_id in reference_ids:
            if len(references) >= selection.max_references:
                truncated = True
                break
            references.append(_reference_header(session, resource_id, visible[resource_id], visible))
        if len(references) > selection.max_references:
            references = references[: selection.max_references]
            truncated = True

    evidence_refs: list[EvidenceRefRead] = []
    if selection.include_evidence:
        for evidence in session.scalars(
            select(Evidence)
            .where(Evidence.project_id == project_id, Evidence.archived_at.is_(None))
            .order_by(Evidence.created_at)
        ):
            if not _evidence_touches(evidence, reachable):
                continue
            if len(evidence_refs) >= selection.max_evidence:
                truncated = True
                break
            evidence_refs.append(_evidence_ref(session, evidence))
        if len(evidence_refs) > selection.max_evidence:
            evidence_refs = evidence_refs[: selection.max_evidence]
            truncated = True

    decision_refs: list[DecisionRefRead] = []
    if selection.include_decisions:
        for decision in session.scalars(
            select(Decision)
            .where(Decision.project_id == project_id, Decision.archived_at.is_(None))
            .order_by(Decision.created_at)
        ):
            if not _decision_touches(session, decision, reachable):
                continue
            if len(decision_refs) >= selection.max_decisions:
                truncated = True
                break
            decision_refs.append(_decision_ref(session, decision))
        if len(decision_refs) > selection.max_decisions:
            decision_refs = decision_refs[: selection.max_decisions]
            truncated = True

    provider_caps = []
    if selection.include_provider_capabilities:
        provider_caps = [
            _provider_capability_read(entry)
            for entry in catalog_entries(
                session,
                actor_id,
                registry,
                policy_permits=lambda kind: services.project_policy_permits(
                    session, actor_id, project_id, kind
                ),
            )
        ]

    budget = BudgetReportRead(
        series_count=len(series_refs),
        revision_count=len(revision_refs),
        relation_count=len(relations),
        evidence_count=len(evidence_refs),
        decision_count=len(decision_refs),
        reference_count=len(references),
        truncated=truncated,
    )

    return ProjectContextRead(
        project_id=project_id,
        project_name=project.name,
        membership_role=Role(membership.role),
        selection=selection,
        series=series_refs,
        revisions=revision_refs,
        relations=relations,
        evidence=evidence_refs,
        decisions=decision_refs,
        references=references,
        provider_capabilities=provider_caps,
        loaded_skill_ids=[skill.id for skill in select_skills(selection)],
        budget=budget,
    )


class ContextBuilder:
    """Small stateless façade so call sites can name the abstraction (ADR-0013)
    without inventing a second, stateful service."""

    def build(
        self,
        session: Session,
        actor_id: UUID,
        project_id: UUID,
        registry: DriverRegistry,
        selection: ContextSelectionCreate | None = None,
    ) -> ProjectContextRead:
        return build_context(session, actor_id, project_id, registry, selection)


# ---------------------------------------------------------------------------
# Implicit (automatic) context: small, structural, safe — project identity plus
# the visible series skeleton. No leaf payloads, no evidence/decision graph.
# ---------------------------------------------------------------------------


def _implicit_context(
    session: Session,
    project: Project,
    project_id: UUID,
    membership: Any,
    registry: DriverRegistry,
    selection: ContextSelectionCreate,
    visible: dict[UUID, ResourceKind],
) -> ProjectContextRead:
    all_series = _visible_series(session, visible)
    truncated = len(all_series) > selection.max_series
    series_refs = [_series_ref(series) for series in all_series[: selection.max_series]]

    provider_caps = []
    if selection.include_provider_capabilities:
        provider_caps = [
            _provider_capability_read(entry)
            for entry in catalog_entries(
                session,
                registry=registry,
                actor_id=membership.actor_id,
                policy_permits=lambda kind: services.project_policy_permits(
                    session, membership.actor_id, project_id, kind
                ),
            )
        ]

    return ProjectContextRead(
        project_id=project_id,
        project_name=project.name,
        membership_role=Role(membership.role),
        selection=selection,
        series=series_refs,
        provider_capabilities=provider_caps,
        loaded_skill_ids=[skill.id for skill in select_skills(selection)],
        budget=BudgetReportRead(
            series_count=len(series_refs),
            truncated=truncated,
        ),
    )


# ---------------------------------------------------------------------------
# Read helpers (all scoped through the Project visibility lens)
# ---------------------------------------------------------------------------


def _visible_series(session: Session, visible: dict[UUID, ResourceKind]) -> list[ScientificObjectSeries]:
    ids = [resource_id for resource_id, kind in visible.items() if kind is ResourceKind.SCIENTIFIC_OBJECT_SERIES]
    if not ids:
        return []
    return list(
        session.scalars(
            select(ScientificObjectSeries)
            .where(ScientificObjectSeries.series_id.in_(ids))
            .order_by(ScientificObjectSeries.created_at)
        )
    )


def _visible_revisions(
    session: Session, series_id: UUID, visible: dict[UUID, ResourceKind]
) -> list[ScientificObjectRevision]:
    return [
        revision
        for revision in session.scalars(
            select(ScientificObjectRevision)
            .where(ScientificObjectRevision.series_id == series_id)
            .order_by(ScientificObjectRevision.revision_seq)
        )
        if revision.revision_id in visible
    ]


def _visible_edges(session: Session, visible: dict[UUID, ResourceKind]) -> list[GlobalProvenanceEdge]:
    """Global provenance edges whose BOTH endpoints are visible through this
    Project's link lens. The double-endpoint filter is pushed into SQL so the
    assembler never loads the whole global edge table into Python."""
    resource_ids = list(visible.keys())
    if not resource_ids:
        return []
    return list(
        session.scalars(
            select(GlobalProvenanceEdge).where(
                and_(
                    GlobalProvenanceEdge.source_id.in_(resource_ids),
                    GlobalProvenanceEdge.target_id.in_(resource_ids),
                )
            )
        )
    )


def _reachable(
    session: Session,
    start: set[UUID],
    visible: dict[UUID, ResourceKind],
    depth: int,
) -> set[UUID]:
    adjacency: dict[UUID, set[UUID]] = {}
    for edge in _visible_edges(session, visible):
        adjacency.setdefault(edge.source_id, set()).add(edge.target_id)
        adjacency.setdefault(edge.target_id, set()).add(edge.source_id)

    seen: set[UUID] = set()
    frontier = set(start)
    for _ in range(max(depth, 0)):
        next_frontier: set[UUID] = set()
        for node in frontier:
            for neighbour in adjacency.get(node, ()):
                if neighbour not in seen and neighbour not in start:
                    next_frontier.add(neighbour)
        seen |= frontier
        frontier = next_frontier - seen
        if not frontier:
            break
    seen |= frontier
    return seen | start


def _series_ref(series: ScientificObjectSeries) -> SeriesRefRead:
    return SeriesRefRead(
        series_id=series.series_id,
        object_type=series.object_type,
        name=series.name,
        description=series.description,
        archived_at=series.archived_at,
    )


def _revision_ref(revision: ScientificObjectRevision) -> RevisionRefRead:
    return RevisionRefRead(
        revision_id=revision.revision_id,
        series_id=revision.series_id,
        revision_seq=revision.revision_seq,
        object_type=revision.object_type,
        schema_version=revision.schema_version,
    )


def _edge_ref(edge: GlobalProvenanceEdge) -> EdgeRead:
    return EdgeRead(
        edge_id=edge.edge_id,
        relation_type=edge.relation_type,
        source_id=edge.source_id,
        source_kind=edge.source_kind,
        target_id=edge.target_id,
        target_kind=edge.target_kind,
    )


def _reference_header(
    session: Session,
    resource_id: UUID,
    kind: ResourceKind,
    visible: dict[UUID, ResourceKind],
) -> ReferenceHeaderRead:
    summary = queries.reference_summary(session, resource_id, kind)
    originating_run: UUID | None = None
    if kind is ResourceKind.ARTIFACT_REFERENCE:
        producing = session.scalars(
            select(GlobalProvenanceEdge).where(
                GlobalProvenanceEdge.relation_type == RelationType.PRODUCED.value,
                GlobalProvenanceEdge.target_id == resource_id,
            )
        )
        for edge in producing:
            if edge.source_id in visible:
                originating_run = edge.source_id
                break
    return ReferenceHeaderRead(
        resource_id=resource_id,
        resource_kind=kind,
        authority=summary.get("authority"),
        native_id=summary.get("native_id"),
        checksum=summary.get("checksum"),
        size=summary.get("size"),
        content_type=summary.get("content_type"),
        version_id=summary.get("version_id"),
        task_type=summary.get("task_type"),
        title=summary.get("title"),
        created_at=summary.get("created_at"),
        revoked_at=summary.get("revoked_at"),
        originating_run_resource_id=originating_run,
    )


def _evidence_touches(evidence: Evidence, reachable: set[UUID]) -> bool:
    return any(
        value is not None and value in reachable
        for value in (
            evidence.source_resource_id,
            evidence.target_revision_id,
            evidence.target_decision_id,
            evidence.target_evidence_id,
        )
    )


def _evidence_ref(session: Session, evidence: Evidence) -> EvidenceRefRead:
    target_id, target_kind = _evidence_target(evidence)
    return EvidenceRefRead(
        evidence_id=evidence.id,
        kind=evidence.kind,
        role=evidence.role,
        source_kind=evidence.source_kind,
        source_id=evidence.source_resource_id,
        target_kind=target_kind,
        target_id=target_id,
        polarity=evidence.polarity,
        interpretation=evidence.interpretation,
        frozen=is_frozen(session, evidence.id),
    )


def _evidence_target(evidence: Evidence) -> tuple[UUID, EvidenceTargetKind]:
    if evidence.target_revision_id is not None:
        return evidence.target_revision_id, EvidenceTargetKind.SCIENTIFIC_OBJECT_REVISION
    if evidence.target_decision_id is not None:
        return evidence.target_decision_id, EvidenceTargetKind.DECISION
    if evidence.target_evidence_id is not None:
        return evidence.target_evidence_id, EvidenceTargetKind.EVIDENCE
    raise NotFoundError(f"evidence {evidence.id} has no target")


def _decision_touches(session: Session, decision: Decision, reachable: set[UUID]) -> bool:
    if decision.status == DecisionStatus.DRAFT.value:
        return any(
            item.get("target_id") is not None and UUID(str(item["target_id"])) in reachable
            for item in (decision.draft_selects or [])
        )
    return (
        session.scalar(
            select(DecisionTarget.id)
            .where(DecisionTarget.decision_id == decision.id)
            .where(DecisionTarget.target_id.in_(reachable))
            .limit(1)
        )
        is not None
    )


def _decision_ref(session: Session, decision: Decision) -> DecisionRefRead:
    if decision.status == DecisionStatus.DRAFT.value:
        selects = list(decision.draft_selects or [])
        cites = list(decision.draft_cites or [])
    else:
        selects = [
            {"target_id": str(target.target_id), "target_kind": target.target_kind}
            for target in session.scalars(
                select(DecisionTarget).where(DecisionTarget.decision_id == decision.id)
            )
        ]
        cites = [
            {"evidence_id": str(cite.evidence_id), "cited_as": cite.cited_as}
            for cite in session.scalars(
                select(DecisionEvidence).where(DecisionEvidence.decision_id == decision.id)
            )
        ]
    from revolab.models import DecisionSupersedes  # imported lazily to keep the top light

    superseded_by = session.scalar(
        select(DecisionSupersedes.decision_id).where(
            DecisionSupersedes.superseded_decision_id == decision.id,
            DecisionSupersedes.project_id == decision.project_id,
        )
    )
    evidence_ids = [UUID(cite["evidence_id"]) for cite in cites]
    return DecisionRefRead(
        decision_id=decision.id,
        title=decision.title,
        status=DecisionStatus(decision.status),
        statement=decision.statement,
        superseded_by=superseded_by,
        selects=selects,
        evidence_ids=evidence_ids,
    )


def _provider_capability_read(entry: dict[str, Any]) -> Any:
    from revolab.schemas import ProviderRead

    return ProviderRead(**entry)
