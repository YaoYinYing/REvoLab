"""Neutral persistence primitives shared by domain commands.

These helpers read/write models and enforce Project-lens visibility reads, but
they never derive mutation authority (authority comes in as a pre-validated
MutationGrant). Importing this module does not import the Identity domain.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from revolab.domain.errors import AuthorizationError, NotFoundError, ValidationError
from revolab.enums import RelationType, ResourceKind
from revolab.models import (
    GlobalProvenanceEdge,
    GlobalResourceRegistry,
    ProjectResourceLink,
    ResourceStewardship,
    ScientificObjectRevision,
)

_GLOBAL_EDGE_SHAPE: dict[RelationType, tuple[frozenset[ResourceKind], frozenset[ResourceKind]]] = {
    RelationType.VARIANT_OF: (
        frozenset({ResourceKind.SCIENTIFIC_OBJECT_SERIES}),
        frozenset({ResourceKind.SCIENTIFIC_OBJECT_SERIES}),
    ),
    RelationType.REPRESENTS: (
        frozenset({ResourceKind.SCIENTIFIC_OBJECT_SERIES}),
        frozenset({ResourceKind.SCIENTIFIC_OBJECT_SERIES}),
    ),
    RelationType.DERIVED_FROM: (
        frozenset({ResourceKind.SCIENTIFIC_OBJECT_REVISION}),
        frozenset({ResourceKind.SCIENTIFIC_OBJECT_REVISION}),
    ),
    RelationType.EVALUATES: (
        frozenset({ResourceKind.SCIENTIFIC_OBJECT_REVISION}),
        frozenset({ResourceKind.SCIENTIFIC_OBJECT_REVISION}),
    ),
    RelationType.CONSUMED_AS_INPUT_BY: (
        frozenset({ResourceKind.SCIENTIFIC_OBJECT_REVISION, ResourceKind.ARTIFACT_REFERENCE}),
        frozenset({ResourceKind.RUN_REFERENCE, ResourceKind.SESSION_REFERENCE}),
    ),
    RelationType.PRODUCED: (
        frozenset({ResourceKind.RUN_REFERENCE, ResourceKind.SESSION_REFERENCE}),
        frozenset({ResourceKind.ARTIFACT_REFERENCE}),
    ),
    RelationType.IMPORTED_AS: (
        frozenset({ResourceKind.ARTIFACT_REFERENCE, ResourceKind.EXTERNAL_REFERENCE}),
        frozenset({ResourceKind.SCIENTIFIC_OBJECT_REVISION}),
    ),
}

_SELECT_TARGET_KINDS = frozenset(
    {ResourceKind.SCIENTIFIC_OBJECT_SERIES, ResourceKind.SCIENTIFIC_OBJECT_REVISION}
)


def resource_kind(session: Session, resource_id: UUID) -> ResourceKind:
    registry = session.get(GlobalResourceRegistry, resource_id)
    if registry is None:
        raise NotFoundError(f"unknown global resource {resource_id}")
    return ResourceKind(registry.resource_kind)


def is_visible(session: Session, project_id: UUID, resource_id: UUID) -> bool:
    return (
        session.scalar(
            select(ProjectResourceLink.id).where(
                ProjectResourceLink.project_id == project_id,
                ProjectResourceLink.resource_id == resource_id,
            )
        )
        is not None
    )


def require_visible(session: Session, project_id: UUID, resource_id: UUID) -> None:
    if not is_visible(session, project_id, resource_id):
        raise AuthorizationError(f"resource {resource_id} is not visible through this project")


def register(session: Session, resource_id: UUID, kind: ResourceKind) -> None:
    session.add(GlobalResourceRegistry(resource_id=resource_id, resource_kind=kind))


def link(session: Session, project_id: UUID, resource_id: UUID, *, folder: str | None = None) -> None:
    if not is_visible(session, project_id, resource_id):
        session.add(ProjectResourceLink(project_id=project_id, resource_id=resource_id, folder=folder))


def steward(session: Session, project_id: UUID, resource_id: UUID) -> None:
    if session.get(ResourceStewardship, resource_id) is None:
        session.add(ResourceStewardship(resource_id=resource_id, steward_project_id=project_id))


def revision_series_id(session: Session, revision_id: UUID) -> UUID:
    revision = session.get(ScientificObjectRevision, revision_id)
    if revision is None:
        raise NotFoundError(f"unknown revision {revision_id}")
    return revision.series_id


def object_type(value: str) -> Any:
    from revolab.enums import ObjectType

    try:
        return ObjectType(value)
    except ValueError as exc:
        raise ValidationError(f"unknown object_type {value!r}") from exc


def payload_checksum(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def validate_edge_shape(
    relation_type: RelationType, source_kind: ResourceKind, target_kind: ResourceKind
) -> None:
    legal_source, legal_target = _GLOBAL_EDGE_SHAPE[relation_type]
    if source_kind not in legal_source or target_kind not in legal_target:
        raise ValidationError(
            f"{relation_type.value} does not accept source={source_kind.value}, target={target_kind.value}"
        )


def insert_edge(
    session: Session,
    actor_id: UUID,
    relation_type: RelationType,
    source_id: UUID,
    source_kind: ResourceKind,
    target_id: UUID,
    target_kind: ResourceKind,
) -> GlobalProvenanceEdge:
    if source_id == target_id:
        raise ValidationError("a relation must connect two distinct nodes")
    edge = GlobalProvenanceEdge(
        relation_type=relation_type.value,
        source_id=source_id,
        source_kind=source_kind.value,
        target_id=target_id,
        target_kind=target_kind.value,
        created_by=actor_id,
    )
    session.add(edge)
    session.commit()
    session.refresh(edge)
    return edge


def new_id() -> UUID:
    return uuid4()
