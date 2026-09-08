"""Read-side projections: object-detail aggregate, bounded graph traversal, and
the derived `generated_by` chain. Every read that names a global resource is
mediated by the Project's ProjectResourceLink lens (ADR-0008) — never a bare
global scan.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from revolab.domain.errors import NotFoundError
from revolab.enums import RelationType, ResourceKind
from revolab.models import (
    ArtifactReference,
    Decision,
    DecisionEvidence,
    DecisionSupersedes,
    DecisionTarget,
    Evidence,
    ExternalIdentity,
    ExternalReference,
    GlobalProvenanceEdge,
    GlobalResourceRegistry,
    LiteratureReference,
    ProjectResourceLink,
    RunReference,
    ScientificObjectRevision,
    ScientificObjectSeries,
    SessionReference,
)


def visible_resources(session: Session, project_id: UUID) -> dict[UUID, ResourceKind]:
    """All global resources currently linked into a Project's context, keyed by
    resource_id with their registry kind."""
    rows = session.execute(
        select(GlobalResourceRegistry.resource_id, GlobalResourceRegistry.resource_kind)
        .join(
            ProjectResourceLink,
            ProjectResourceLink.resource_id == GlobalResourceRegistry.resource_id,
        )
        .where(ProjectResourceLink.project_id == project_id)
    )
    return {resource_id: ResourceKind(kind) for resource_id, kind in rows}


def is_edge_visible(
    session: Session, project_id: UUID, source_id: UUID, target_id: UUID, visible: dict[UUID, ResourceKind]
) -> bool:
    return source_id in visible and target_id in visible


def revision_series_map(session: Session) -> dict[UUID, UUID]:
    rows = session.execute(
        select(ScientificObjectRevision.revision_id, ScientificObjectRevision.series_id)
    )
    return {revision_id: series_id for revision_id, series_id in rows}


def series_summary(session: Session, series: ScientificObjectSeries) -> dict[str, Any]:
    return {
        "series_id": str(series.series_id),
        "object_type": series.object_type,
        "name": series.name,
        "description": series.description,
        "created_at": series.created_at,
        "archived_at": series.archived_at,
    }


def revision_summary(revision: ScientificObjectRevision) -> dict[str, Any]:
    return {
        "revision_id": str(revision.revision_id),
        "series_id": str(revision.series_id),
        "revision_seq": revision.revision_seq,
        "object_type": revision.object_type,
        "schema_version": revision.schema_version,
        "payload": revision.payload,
        "checksum": revision.checksum,
        "created_at": revision.created_at,
    }


def object_detail(session: Session, project_id: UUID, series_id: UUID) -> dict[str, Any]:
    series = session.get(ScientificObjectSeries, series_id)
    if series is None:
        raise NotFoundError("series not found")
    visible = visible_resources(session, project_id)
    if series_id not in visible:
        raise NotFoundError("series is not visible through this project")

    revisions = list(
        session.scalars(
            select(ScientificObjectRevision)
            .where(ScientificObjectRevision.series_id == series_id)
            .order_by(ScientificObjectRevision.revision_seq)
        )
    )
    visible_revisions = [r for r in revisions if r.revision_id in visible]

    revision_ids = {r.revision_id for r in visible_revisions}
    inbound_provenance: list[dict[str, Any]] = []
    outbound_provenance: list[dict[str, Any]] = []
    for edge in session.scalars(select(GlobalProvenanceEdge)):
        if not is_edge_visible(session, project_id, edge.source_id, edge.target_id, visible):
            continue
        row = {
            "edge_id": str(edge.edge_id),
            "relation_type": edge.relation_type,
            "source_id": str(edge.source_id),
            "source_kind": edge.source_kind,
            "target_id": str(edge.target_id),
            "target_kind": edge.target_kind,
        }
        if edge.target_id in revision_ids or edge.target_id == series_id:
            inbound_provenance.append(row)
        if edge.source_id in revision_ids or edge.source_id == series_id:
            outbound_provenance.append(row)

    project_evidence = list(
        session.scalars(
            select(Evidence).where(
                Evidence.project_id == project_id, Evidence.archived_at.is_(None)
            )
        )
    )
    related_evidence = [
        evidence_summary(e, frozen=_evidence_frozen(session, e.id))
        for e in project_evidence
        if e.target_revision_id in revision_ids
        or e.source_resource_id in revision_ids
        or e.source_resource_id == series_id
    ]

    decision_ids = {
        t.decision_id
        for t in session.scalars(
            select(DecisionTarget)
            .join(Decision, Decision.id == DecisionTarget.decision_id)
            .where(
                Decision.project_id == project_id,
                Decision.archived_at.is_(None),
                or_(DecisionTarget.target_id == series_id, DecisionTarget.target_id.in_(revision_ids)),
            )
        )
    }
    decisions = [
        decision_summary(session, d)
        for d in session.scalars(select(Decision).where(Decision.id.in_(decision_ids)))
    ]

    return {
        "series": series_summary(session, series),
        "visible_revisions": [revision_summary(r) for r in visible_revisions],
        "latest_revision_seq": visible_revisions[-1].revision_seq if visible_revisions else None,
        "provenance": {
            "inbound": inbound_provenance,
            "outbound": outbound_provenance,
        },
        "evidence": related_evidence,
        "decisions": decisions,
    }


def _evidence_frozen(session: Session, evidence_id: UUID) -> bool:
    from revolab.services import _is_frozen

    return _is_frozen(session, evidence_id)


def evidence_summary(evidence: Evidence, *, frozen: bool = False) -> dict[str, Any]:
    target_id = (
        evidence.target_revision_id or evidence.target_decision_id or evidence.target_evidence_id
    )
    target_kind = (
        "scientific_object_revision"
        if evidence.target_revision_id is not None
        else "decision"
        if evidence.target_decision_id is not None
        else "evidence"
    )
    return {
        "id": str(evidence.id),
        "project_id": str(evidence.project_id),
        "kind": evidence.kind,
        "role": evidence.role,
        "label": evidence.label,
        "interpretation": evidence.interpretation,
        "polarity": evidence.polarity,
        "confidence": evidence.confidence,
        "confidence_source": evidence.confidence_source,
        "scope": evidence.scope,
        "source_kind": evidence.source_kind,
        "source_id": str(evidence.source_resource_id) if evidence.source_resource_id else None,
        "target_kind": target_kind,
        "target_id": str(target_id),
        "created_at": evidence.created_at,
        "frozen": frozen,
    }


def decision_summary(session: Session, decision: Decision) -> dict[str, Any]:
    if decision.status == "draft":
        cites = list(decision.draft_cites or [])
        selects = list(decision.draft_selects or [])
    else:
        cites = [
            {"evidence_id": str(c.evidence_id), "cited_as": c.cited_as}
            for c in session.scalars(
                select(DecisionEvidence).where(DecisionEvidence.decision_id == decision.id)
            )
        ]
        selects = [
            {"target_id": str(t.target_id), "target_kind": t.target_kind}
            for t in session.scalars(select(DecisionTarget).where(DecisionTarget.decision_id == decision.id))
        ]
    superseded_by = session.scalar(
        select(DecisionSupersedes.decision_id).where(
            DecisionSupersedes.superseded_decision_id == decision.id,
            DecisionSupersedes.project_id == decision.project_id,
        )
    )
    return {
        "id": str(decision.id),
        "project_id": str(decision.project_id),
        "title": decision.title,
        "statement": decision.statement,
        "status": decision.status,
        "next_actions": list(decision.next_actions or []),
        "cites": cites,
        "selects": selects,
        "superseded": superseded_by is not None,
        "superseded_by": str(superseded_by) if superseded_by else None,
        "created_at": decision.created_at,
        "committed_at": decision.committed_at,
    }


def bounded_graph(
    session: Session,
    project_id: UUID,
    from_id: UUID,
    depth: int = 2,
    kinds: set[str] | None = None,
) -> dict[str, Any]:
    """Bounded typed subgraph through the Project authorization projection."""
    kinds = kinds or {"relation", "evidence", "decision"}
    depth = max(0, min(depth, 5))
    visible = visible_resources(session, project_id)
    if from_id not in visible:
        raise NotFoundError("starting resource is not visible through this project")
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    # Simple adjacency over visible global edges (undirected for neighborhood).
    adjacency: dict[UUID, set[UUID]] = {node: set() for node in visible}
    global_edges = list(session.scalars(select(GlobalProvenanceEdge)))
    for edge in global_edges:
        if edge.source_id in visible and edge.target_id in visible:
            adjacency.setdefault(edge.source_id, set()).add(edge.target_id)
            adjacency.setdefault(edge.target_id, set()).add(edge.source_id)

    reachable = _bfs(adjacency, from_id, depth)
    reachable.add(from_id)

    if "relation" in kinds:
        for edge in global_edges:
            if edge.source_id in visible and edge.target_id in visible and (
                edge.source_id in reachable or edge.target_id in reachable
            ):
                edges.append(
                    {
                        "kind": "relation",
                        "id": str(edge.edge_id),
                        "relation_type": edge.relation_type,
                        "source_id": str(edge.source_id),
                        "source_kind": edge.source_kind,
                        "target_id": str(edge.target_id),
                        "target_kind": edge.target_kind,
                    }
                )
                nodes.setdefault(str(edge.source_id), {"kind": edge.source_kind})
                nodes.setdefault(str(edge.target_id), {"kind": edge.target_kind})

    if "evidence" in kinds:
        for evidence in session.scalars(
            select(Evidence).where(
                Evidence.project_id == project_id, Evidence.archived_at.is_(None)
            )
        ):
            source = evidence.source_resource_id
            target = evidence.target_revision_id
            source_visible = source in visible if source is not None else True
            target_visible = target in visible if target is not None else False
            source_reachable = source in reachable if source is not None else False
            target_reachable = target in reachable if target is not None else False
            if (source_reachable or target_reachable) and source_visible and target_visible:
                edges.append(
                    {
                        "kind": "evidence",
                        "id": str(evidence.id),
                        "relation_type": "evidence",
                        "source_id": str(source) if source else None,
                        "target_id": str(target) if target else None,
                    }
                )

    if "decision" in kinds:
        project_decisions = session.scalars(
            select(Decision.id).where(
                Decision.project_id == project_id, Decision.archived_at.is_(None)
            )
        ).all()
        project_decision_ids = set(project_decisions)
        for select_ in session.scalars(select(DecisionTarget)):
            if select_.decision_id not in project_decision_ids:
                continue  # knowledge edges are project-scoped: never leak another Project's Decision
            if select_.target_id in reachable:
                edges.append(
                    {
                        "kind": "decision",
                        "id": str(select_.id),
                        "relation_type": "selects",
                        "source_id": str(select_.decision_id),
                        "target_id": str(select_.target_id),
                    }
                )
                nodes.setdefault(str(select_.decision_id), {"kind": "decision"})

    return {"nodes": nodes, "edges": edges}


def _bfs(adjacency: dict[UUID, set[UUID]], start: UUID, depth: int) -> set[UUID]:
    seen: set[UUID] = set()
    frontier = {start}
    for _ in range(depth):
        next_frontier: set[UUID] = set()
        for node in frontier:
            for neighbour in adjacency.get(node, ()):
                if neighbour not in seen and neighbour != start:
                    next_frontier.add(neighbour)
        seen |= frontier
        frontier = next_frontier - seen
        if not frontier:
            break
    seen |= frontier
    return seen


def generated_by(session: Session, project_id: UUID, revision_id: UUID) -> list[dict[str, Any]]:
    """Derived traversal (never persisted): Revision <-imported_as<- Artifact
    <-produced<- Run/Session. Returns the producing run/session chain. Every hop
    is filtered through the Project's visibility lens (global identity is never
    global readability)."""
    visible = visible_resources(session, project_id)
    if revision_id not in visible:
        return []
    chain: list[dict[str, Any]] = []
    imports = session.scalars(
        select(GlobalProvenanceEdge).where(
            GlobalProvenanceEdge.relation_type == RelationType.IMPORTED_AS.value,
            GlobalProvenanceEdge.target_id == revision_id,
        )
    )
    for imported_as in imports:
        artifact_id = imported_as.source_id
        if artifact_id not in visible:
            continue
        producers = session.scalars(
            select(GlobalProvenanceEdge).where(
                GlobalProvenanceEdge.relation_type == RelationType.PRODUCED.value,
                GlobalProvenanceEdge.target_id == artifact_id,
            )
        )
        for produced in producers:
            if produced.source_id not in visible:
                continue
            chain.append(
                {
                    "artifact_id": str(artifact_id),
                    "resource_id": str(produced.source_id),
                    "resource_kind": produced.source_kind,
                }
            )
    return chain


def reference_summary(session: Session, resource_id: UUID, kind: ResourceKind) -> dict[str, Any]:
    """A truthful, kind-appropriate summary for any global resource. Scientific
    objects return their object/revision shape; reference nodes return their
    durable identity card. Unsupported kinds are rejected, never fabricated."""
    if kind is ResourceKind.SCIENTIFIC_OBJECT_SERIES:
        series = session.get(ScientificObjectSeries, resource_id)
        if series is None:
            raise NotFoundError("series not found")
        return series_summary(session, series)
    if kind is ResourceKind.SCIENTIFIC_OBJECT_REVISION:
        revision = session.get(ScientificObjectRevision, resource_id)
        if revision is None:
            raise NotFoundError("revision not found")
        return revision_summary(revision)

    base: dict[str, Any] = {
        "resource_id": str(resource_id),
        "resource_kind": kind.value,
        "authority": None,
        "native_id": None,
        "checksum": None,
        "size": None,
        "content_type": None,
        "version_id": None,
        "task_type": None,
        "title": None,
        "created_at": None,
        "revoked_at": None,
    }
    if kind is ResourceKind.RUN_REFERENCE:
        run = session.get(RunReference, resource_id)
        if run is None:
            raise NotFoundError("run reference not found")
        base["authority"] = run.authority
        base["native_id"] = run.native_id
        base["task_type"] = run.task_type
        base["created_at"] = run.created_at
        base["revoked_at"] = run.revoked_at
    elif kind is ResourceKind.SESSION_REFERENCE:
        sess = session.get(SessionReference, resource_id)
        if sess is None:
            raise NotFoundError("session reference not found")
        base["authority"] = sess.authority
        base["native_id"] = sess.native_id
        base["created_at"] = sess.created_at
        base["revoked_at"] = sess.revoked_at
    elif kind is ResourceKind.ARTIFACT_REFERENCE:
        artifact = session.get(ArtifactReference, resource_id)
        if artifact is None:
            raise NotFoundError("artifact reference not found")
        base["authority"] = artifact.authority
        base["native_id"] = artifact.native_id
        base["checksum"] = artifact.checksum
        base["size"] = artifact.size
        base["content_type"] = artifact.content_type
        base["version_id"] = artifact.version_id
        base["created_at"] = artifact.created_at
        base["revoked_at"] = artifact.revoked_at
    elif kind is ResourceKind.LITERATURE_REFERENCE:
        literature = session.get(LiteratureReference, resource_id)
        if literature is None:
            raise NotFoundError("literature reference not found")
        base["authority"] = literature.authority
        base["native_id"] = literature.native_id
        base["title"] = literature.title
        base["created_at"] = literature.created_at
    elif kind is ResourceKind.EXTERNAL_REFERENCE:
        external = session.get(ExternalReference, resource_id)
        if external is None:
            raise NotFoundError("external reference not found")
        identity = session.get(ExternalIdentity, external.external_identity_id)
        if identity is not None:
            base["authority"] = identity.authority
            base["native_id"] = identity.native_id
        base["checksum"] = external.checksum
        base["created_at"] = external.created_at
    else:
        raise NotFoundError(f"unsupported resource kind {kind.value}")
    return base
