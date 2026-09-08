"""Minimal authority substrate (Identity / Collaboration domain).

Implements the Acting-Actor mutation formula and the MutationGrant issuance from
ADR-0008 / COLLABORATION_IDENTITY. This is the *only* module that derives
mutation authority; Scientific Object and Evidence/Provenance commands consume
the pre-validated grant and therefore do not import Identity.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from revolab.domain.errors import AuthorizationError
from revolab.domain.grants import MutationGrant
from revolab.enums import Role
from revolab.models import Project, ProjectMembership, ResourceStewardship

__all__ = [
    "MutationGrant",
    "can_mutate",
    "mutation_capable_membership",
    "owner_membership",
    "readable_membership",
]

MUTATION_ROLES = frozenset({Role.OWNER, Role.MEMBER})
READ_ROLES = frozenset({Role.OWNER, Role.MEMBER, Role.VIEWER})


def _active_membership(
    session: Session, actor_id: UUID, project_id: UUID, *, role_set: frozenset[Role]
) -> ProjectMembership:
    project = session.get(Project, project_id)
    if project is None or project.deleted_at is not None:
        raise AuthorizationError("project is not active")
    membership = session.scalar(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.actor_id == actor_id,
        )
    )
    if membership is None:
        raise AuthorizationError("actor is not a member of the project")
    if membership.role not in role_set:
        raise AuthorizationError("actor role does not authorize this operation")
    return membership


def mutation_capable_membership(
    session: Session, actor_id: UUID, project_id: UUID
) -> ProjectMembership:
    """A mutation-capable membership (owner/member) in an active Project — the
    authority required to *create* global resources (creation cannot require a
    pre-existing stewardship grant over a resource that does not exist)."""
    return _active_membership(session, actor_id, project_id, role_set=MUTATION_ROLES)


def owner_membership(session: Session, actor_id: UUID, project_id: UUID) -> ProjectMembership:
    """Owner-only membership — transfer/freeze, membership changes, tombstone."""
    return _active_membership(session, actor_id, project_id, role_set=frozenset({Role.OWNER}))


def readable_membership(session: Session, actor_id: UUID, project_id: UUID) -> ProjectMembership:
    """Any role in an active Project — the read/projection lens (visibility is
    membership-mediated; global identity is not global readability)."""
    return _active_membership(session, actor_id, project_id, role_set=READ_ROLES)


def can_mutate(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    resource_id: UUID,
    *,
    purpose: str = "mutate",
) -> MutationGrant:
    """The Acting-Actor formula (ADR-0008, round 6), frozen:

        membership(actor, project).role ∈ {owner, member}
        AND ResourceStewardship(resource).steward_project == project
        AND project.deleted_at IS NULL
    """
    membership = _active_membership(session, actor_id, project_id, role_set=MUTATION_ROLES)
    stewardship = session.get(ResourceStewardship, resource_id)
    if stewardship is None or stewardship.steward_project_id != project_id:
        raise AuthorizationError("project does not steward this resource")
    return MutationGrant(
        resource_id=resource_id,
        steward_project_id=project_id,
        actor_id=actor_id,
        role=membership.role,
        purpose=purpose,
    )
