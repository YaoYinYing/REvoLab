"""Application command boundary (the typed operation gate + Identity issuer).

This module is the single place where mutation authority is *derived*: it asks
Identity (`revolab.domain.identity`) to validate an Acting Actor and issue a
`MutationGrant`, orchestrates cross-domain sagas (the atomic
Series+Revision+Link+Stewardship creation, the import command, Project
tombstone), and delegates the identity-free domain writes to
`revolab.domain.scientific_object`, `revolab.domain.provenance`, and
`revolab.domain.knowledge` — none of which import Identity.

The acting Actor participates as an opaque UUID handed in by the API layer
(real authentication is deferred per ADR-0008).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from revolab.content_store import ContentStore
from revolab.domain import knowledge, persistence, provenance, scientific_object
from revolab.domain.errors import AuthorizationError, ConflictError, NotFoundError
from revolab.domain.identity import (
    can_mutate,
    mutation_capable_membership,
    owner_membership,
)
from revolab.domain.identity import (
    readable_membership as _readable_membership,
)
from revolab.enums import RelationType, ResourceKind, Role
from revolab.models import (
    Actor,
    Decision,
    DecisionTarget,
    Evidence,
    GlobalProvenanceEdge,
    Project,
    ProjectMembership,
    ProjectResourceLink,
    ResourceStewardship,
    ScientificObjectRevision,
)


def readable_membership(session: Session, actor_id: UUID, project_id: UUID) -> object:
    return _readable_membership(session, actor_id, project_id)


def _resource_kind(session: Session, resource_id: UUID) -> ResourceKind:
    return persistence.resource_kind(session, resource_id)


def _require_visible(session: Session, project_id: UUID, resource_id: UUID) -> None:
    persistence.require_visible(session, project_id, resource_id)


def _is_frozen(session: Session, evidence_id: UUID) -> bool:
    return provenance.is_frozen(session, evidence_id)


# ---------------------------------------------------------------------------
# Identity / Project
# ---------------------------------------------------------------------------


def create_actor(session: Session) -> UUID:
    actor = Actor()
    session.add(actor)
    session.commit()
    session.refresh(actor)
    return actor.actor_id


def create_project(
    session: Session,
    actor_id: UUID,
    name: str,
    description: str | None = None,
    *,
    visibility: str = "private",
) -> Project:
    project = Project(name=name, description=description, visibility=visibility)
    session.add(project)
    session.flush()
    session.add(ProjectMembership(project_id=project.id, actor_id=actor_id, role=Role.OWNER.value))
    session.commit()
    session.refresh(project)
    return project


def get_project(session: Session, project_id: UUID) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise NotFoundError("project not found")
    return project


def list_projects_for_actor(session: Session, actor_id: UUID) -> list[Project]:
    rows = session.scalars(
        select(Project)
        .join(ProjectMembership, ProjectMembership.project_id == Project.id)
        .where(ProjectMembership.actor_id == actor_id, Project.deleted_at.is_(None))
        .order_by(Project.created_at.desc())
    )
    return list(rows)


def add_membership(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    member_actor_id: UUID,
    role: str,
) -> ProjectMembership:
    owner_membership(session, actor_id, project_id)
    membership = ProjectMembership(
        project_id=project_id, actor_id=member_actor_id, role=Role(role).value
    )
    session.add(membership)
    session.commit()
    return membership


def delete_project(session: Session, actor_id: UUID, project_id: UUID) -> None:
    owner_membership(session, actor_id, project_id)
    project = get_project(session, project_id)
    project.deleted_at = datetime.now(UTC)

    for link in session.scalars(
        select(ProjectResourceLink).where(ProjectResourceLink.project_id == project_id)
    ):
        session.delete(link)
    for membership in session.scalars(
        select(ProjectMembership).where(ProjectMembership.project_id == project_id)
    ):
        session.delete(membership)

    archived_at = datetime.now(UTC)
    for evidence in session.scalars(select(Evidence).where(Evidence.project_id == project_id)):
        if evidence.archived_at is None:
            evidence.archived_at = archived_at
    for decision in session.scalars(select(Decision).where(Decision.project_id == project_id)):
        if decision.archived_at is None:
            decision.archived_at = archived_at

    for stewardship in session.scalars(
        select(ResourceStewardship).where(ResourceStewardship.steward_project_id == project_id)
    ):
        stewardship.steward_project_id = None

    session.commit()


# ---------------------------------------------------------------------------
# Scientific Objects (global resources)
# ---------------------------------------------------------------------------


def create_object(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    object_type: str,
    name: str,
    *,
    description: str | None = None,
    payload: dict[str, Any] | None = None,
    folder: str | None = None,
) -> UUID:
    mutation_capable_membership(session, actor_id, project_id)
    series_id, revision_id = scientific_object.create_spine(
        session, actor_id, object_type, name, description=description, payload=payload or {}
    )
    persistence.link(session, project_id, series_id, folder=folder)
    persistence.link(session, project_id, revision_id, folder=folder)
    persistence.steward(session, project_id, series_id)
    session.commit()
    return series_id


def append_revision(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    series_id: UUID,
    payload: dict[str, Any],
) -> Any:
    grant = can_mutate(session, actor_id, project_id, series_id, purpose="append revision")
    # Revision visibility closure: a revision may only be visible where its
    # owning Series is; the steward Project must already hold the series link.
    if not persistence.is_visible(session, project_id, series_id):
        raise AuthorizationError("series must be visible in the project before appending a revision")
    revision = scientific_object.append_revision(session, grant, series_id, payload)
    persistence.link(session, project_id, revision.revision_id)
    session.commit()
    session.refresh(revision)
    return revision


def update_series(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    series_id: UUID,
    *,
    name: str | None = None,
    description: str | None = None,
) -> Any:
    grant = can_mutate(session, actor_id, project_id, series_id, purpose="rename series")
    series = scientific_object.update_series(
        session, grant, series_id, name=name, description=description
    )
    session.commit()
    session.refresh(series)
    return series


def archive_series(session: Session, actor_id: UUID, project_id: UUID, series_id: UUID) -> None:
    grant = can_mutate(session, actor_id, project_id, series_id, purpose="archive series")
    revision_ids = set(
        session.scalars(
            select(ScientificObjectRevision.revision_id).where(
                ScientificObjectRevision.series_id == series_id
            )
        )
    )
    member_ids = [series_id, *revision_ids]
    if session.scalar(
        select(GlobalProvenanceEdge.edge_id)
        .where(
            GlobalProvenanceEdge.source_id.in_(member_ids)
            | GlobalProvenanceEdge.target_id.in_(member_ids)
        )
        .limit(1)
    ):
        raise ConflictError("series is referenced by provenance; it cannot be archived")
    if session.scalar(
        select(Evidence.id)
        .where(
            Evidence.source_resource_id.in_(member_ids) | Evidence.target_revision_id.in_(member_ids)
        )
        .limit(1)
    ):
        raise ConflictError("series is referenced by evidence; it cannot be archived")
    if session.scalar(
        select(DecisionTarget.id).where(DecisionTarget.target_id.in_(member_ids)).limit(1)
    ):
        raise ConflictError("series is referenced by a decision; it cannot be archived")
    scientific_object.mark_archived(session, grant, series_id)
    session.commit()


def link_series(session: Session, actor_id: UUID, project_id: UUID, resource_id: UUID) -> None:
    """Grant the read/context lens: a mutation-capable membership may bind a
    global resource into the Project's context (Project-owned link set)."""
    mutation_capable_membership(session, actor_id, project_id)
    provenance.link_series(session, project_id, resource_id)


# ---------------------------------------------------------------------------
# Reference nodes (global identity cards)
# ---------------------------------------------------------------------------


def _finalize_reference(session: Session, project_id: UUID, resource_id: UUID, row: Any) -> Any:
    persistence.link(session, project_id, resource_id)
    persistence.steward(session, project_id, resource_id)
    session.commit()
    session.refresh(row)
    return row


def _link_existing_reference(session: Session, project_id: UUID, resource_id: UUID, row: Any) -> Any:
    persistence.link(session, project_id, resource_id)
    session.commit()
    session.refresh(row)
    return row


def create_run_reference(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    authority: str,
    native_id: str,
    *,
    task_type: str | None = None,
    input_parameter_digest: str | None = None,
    submitted_at: datetime | None = None,
) -> Any:
    mutation_capable_membership(session, actor_id, project_id)
    existing = provenance.find_run_reference(session, authority, native_id)
    if existing is not None:
        return _link_existing_reference(session, project_id, existing.run_id, existing)
    row = provenance.create_run_reference_row(
        session,
        authority,
        native_id,
        task_type=task_type,
        input_parameter_digest=input_parameter_digest,
        submitted_at=submitted_at,
    )
    return _finalize_reference(session, project_id, row.run_id, row)


def create_session_reference(
    session: Session, actor_id: UUID, project_id: UUID, authority: str, native_id: str
) -> Any:
    mutation_capable_membership(session, actor_id, project_id)
    existing = provenance.find_session_reference(session, authority, native_id)
    if existing is not None:
        return _link_existing_reference(session, project_id, existing.session_id, existing)
    row = provenance.create_session_reference_row(session, authority, native_id)
    return _finalize_reference(session, project_id, row.session_id, row)


def create_artifact_reference(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    authority: str,
    native_id: str,
    *,
    content_type: str | None = None,
    size: int | None = None,
    checksum: str | None = None,
    version_id: str | None = None,
) -> Any:
    mutation_capable_membership(session, actor_id, project_id)
    version_id = version_id or ""
    existing = provenance.find_artifact_reference(session, authority, native_id, version_id)
    if existing is not None:
        return _link_existing_reference(session, project_id, existing.artifact_id, existing)
    row = provenance.create_artifact_reference_row(
        session,
        authority,
        native_id,
        content_type=content_type,
        size=size,
        checksum=checksum,
        version_id=version_id,
    )
    return _finalize_reference(session, project_id, row.artifact_id, row)


def create_literature_reference(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    authority: str,
    native_id: str,
    *,
    title: str | None = None,
) -> Any:
    mutation_capable_membership(session, actor_id, project_id)
    existing = provenance.find_literature_reference(session, authority, native_id)
    if existing is not None:
        return _link_existing_reference(session, project_id, existing.literature_id, existing)
    row = provenance.create_literature_reference_row(session, authority, native_id, title=title)
    return _finalize_reference(session, project_id, row.literature_id, row)


def create_external_reference(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    authority: str,
    native_id: str,
    *,
    kind: str | None = None,
    checksum: str | None = None,
    cache_metadata: dict[str, Any] | None = None,
) -> Any:
    mutation_capable_membership(session, actor_id, project_id)
    identity = scientific_object.get_or_create_external_identity(
        session, authority, native_id, kind=kind
    )
    row = provenance.create_external_reference_row(
        session,
        identity.external_identity_id,
        checksum=checksum,
        as_of=None,
        cache_metadata=cache_metadata,
    )
    return _finalize_reference(session, project_id, row.external_reference_id, row)


# ---------------------------------------------------------------------------
# External identity
# ---------------------------------------------------------------------------


def get_or_create_external_identity(
    session: Session, authority: str, native_id: str, *, kind: str | None = None
) -> Any:
    return scientific_object.get_or_create_external_identity(session, authority, native_id, kind=kind)


def attach_external_identity(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    series_id: UUID,
    authority: str,
    native_id: str,
    *,
    qualifier: str = "identity",
    kind: str | None = None,
    is_canonical: bool = False,
) -> Any:
    grant = can_mutate(session, actor_id, project_id, series_id, purpose="attach external identity")
    mapping = scientific_object.attach_external_identity(
        session,
        grant,
        series_id,
        authority,
        native_id,
        qualifier=qualifier,
        kind=kind,
        is_canonical=is_canonical,
    )
    session.commit()
    session.refresh(mapping)
    return mapping


# ---------------------------------------------------------------------------
# Global provenance edges (#1-7) — typed commands only
# ---------------------------------------------------------------------------


def add_variant_of(
    session: Session, actor_id: UUID, project_id: UUID, source_series_id: UUID, target_series_id: UUID
) -> GlobalProvenanceEdge:
    grant = can_mutate(session, actor_id, project_id, source_series_id, purpose="variant_of")
    persistence.require_visible(session, project_id, target_series_id)
    return provenance.add_conceptual_edge(
        session, grant, RelationType.VARIANT_OF, source_series_id, target_series_id
    )


def add_represents(
    session: Session, actor_id: UUID, project_id: UUID, source_series_id: UUID, target_series_id: UUID
) -> GlobalProvenanceEdge:
    grant = can_mutate(session, actor_id, project_id, source_series_id, purpose="represents")
    persistence.require_visible(session, project_id, target_series_id)
    return provenance.add_conceptual_edge(
        session, grant, RelationType.REPRESENTS, source_series_id, target_series_id
    )


def add_derived_from(
    session: Session, actor_id: UUID, project_id: UUID, source_revision_id: UUID, target_revision_id: UUID
) -> GlobalProvenanceEdge:
    grant = can_mutate(
        session,
        actor_id,
        project_id,
        persistence.revision_series_id(session, source_revision_id),
        purpose="derived_from",
    )
    persistence.require_visible(session, project_id, target_revision_id)
    return provenance.add_revision_edge(
        session, grant, RelationType.DERIVED_FROM, source_revision_id, target_revision_id
    )


def add_evaluates(
    session: Session, actor_id: UUID, project_id: UUID, source_revision_id: UUID, target_revision_id: UUID
) -> GlobalProvenanceEdge:
    grant = can_mutate(
        session,
        actor_id,
        project_id,
        persistence.revision_series_id(session, source_revision_id),
        purpose="evaluates",
    )
    persistence.require_visible(session, project_id, target_revision_id)
    return provenance.add_revision_edge(
        session, grant, RelationType.EVALUATES, source_revision_id, target_revision_id
    )


def add_consumed_input(
    session: Session, actor_id: UUID, project_id: UUID, source_id: UUID, target_id: UUID
) -> GlobalProvenanceEdge:
    source_kind = persistence.resource_kind(session, source_id)
    anchor = (
        persistence.revision_series_id(session, source_id)
        if source_kind is ResourceKind.SCIENTIFIC_OBJECT_REVISION
        else source_id
    )
    grant = can_mutate(session, actor_id, project_id, anchor, purpose="consumed_as_input_by")
    return provenance.add_consumed_input(session, grant, project_id, source_id, target_id)


def record_produced(
    session: Session, actor_id: UUID, project_id: UUID, source_id: UUID, artifact_id: UUID
) -> GlobalProvenanceEdge:
    grant = can_mutate(session, actor_id, project_id, source_id, purpose="produced")
    return provenance.record_produced(session, grant, project_id, source_id, artifact_id)


def import_revision(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    series_id: UUID,
    source_id: UUID,
    *,
    payload: dict[str, Any],
) -> Any:
    grant = can_mutate(session, actor_id, project_id, series_id, purpose="import object")
    return provenance.import_revision(
        session, grant, project_id, series_id, source_id, payload=payload
    )


# ---------------------------------------------------------------------------
# Evidence (project-scoped interpreted claim)
# ---------------------------------------------------------------------------


def create_evidence(session: Session, actor_id: UUID, project_id: UUID, **kwargs: Any) -> Evidence:
    mutation_capable_membership(session, actor_id, project_id)
    return provenance.create_evidence_row(session, project_id, actor_id, **kwargs)


def update_evidence(
    session: Session, actor_id: UUID, project_id: UUID, evidence_id: UUID, **fields: Any
) -> Evidence:
    mutation_capable_membership(session, actor_id, project_id)
    return provenance.update_evidence_row(session, project_id, evidence_id, **fields)


# ---------------------------------------------------------------------------
# Decision lifecycle (draft -> committed)
# ---------------------------------------------------------------------------


def create_decision(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    *,
    title: str,
    statement: str,
    next_actions: list[str] | None = None,
    cites: list[dict[str, Any]] | None = None,
    selects: list[dict[str, Any]] | None = None,
) -> Decision:
    mutation_capable_membership(session, actor_id, project_id)
    return knowledge.create_decision_row(
        session,
        project_id,
        actor_id,
        title=title,
        statement=statement,
        next_actions=next_actions or [],
        cites=cites or [],
        selects=selects or [],
    )


def update_decision(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    decision_id: UUID,
    *,
    title: str | None = None,
    statement: str | None = None,
    next_actions: list[str] | None = None,
    cites: list[dict[str, Any]] | None = None,
    selects: list[dict[str, Any]] | None = None,
) -> Decision:
    mutation_capable_membership(session, actor_id, project_id)
    return knowledge.update_decision_row(
        session,
        project_id,
        decision_id,
        title=title,
        statement=statement,
        next_actions=next_actions,
        cites=cites,
        selects=selects,
    )


def commit_decision(session: Session, actor_id: UUID, project_id: UUID, decision_id: UUID) -> Decision:
    mutation_capable_membership(session, actor_id, project_id)
    return knowledge.commit_decision_row(session, project_id, decision_id, actor_id)


def supersede_decision(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    superseding_decision_id: UUID,
    superseded_decision_id: UUID,
) -> Any:
    mutation_capable_membership(session, actor_id, project_id)
    return knowledge.supersede_decision_row(
        session, project_id, actor_id, superseding_decision_id, superseded_decision_id
    )


# ---------------------------------------------------------------------------
# Internal artifacts (ContentStore-backed, authority = revolab)
# ---------------------------------------------------------------------------


def create_internal_artifact(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    store: ContentStore,
    data: bytes,
    *,
    content_type: str | None = None,
) -> Any:
    mutation_capable_membership(session, actor_id, project_id)
    result = store.put(data, content_type=content_type)
    return create_artifact_reference(
        session,
        actor_id,
        project_id,
        "revolab",
        result["checksum"],
        content_type=result["content_type"],
        size=result["size"],
        checksum=result["checksum"],
    )
