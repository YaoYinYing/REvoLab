"""Minimal authority substrate: roles, stewardship, tombstone, MutationGrant."""

import pytest
from sqlalchemy import select

from revolab import services
from revolab.domain.errors import AuthorizationError
from revolab.domain.identity import can_mutate, mutation_capable_membership
from revolab.enums import Role
from revolab.models import ProjectResourceLink, ResourceStewardship


def _setup(session):
    owner = services.create_actor(session)
    member = services.create_actor(session)
    viewer = services.create_actor(session)
    outsider = services.create_actor(session)
    project = services.create_project(session, owner, "P")
    services.add_membership(session, owner, project.id, member, Role.MEMBER.value)
    services.add_membership(session, owner, project.id, viewer, Role.VIEWER.value)
    return owner, member, viewer, outsider, project


def test_object_creation_atomically_creates_link_and_stewardship(session):
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "P")
    series_id = services.create_object(session, actor, project.id, "protein", "X", payload={})

    link = session.scalar(
        select(ProjectResourceLink).where(
            ProjectResourceLink.project_id == project.id,
            ProjectResourceLink.resource_id == series_id,
        )
    )
    assert link is not None
    stewardship = session.get(ResourceStewardship, series_id)
    assert stewardship is not None
    assert stewardship.steward_project_id == project.id


def test_can_mutate_roles(session):
    owner, member, viewer, outsider, project = _setup(session)
    series_id = services.create_object(session, owner, project.id, "protein", "X", payload={})

    # owner and member mutate; viewer and non-steward do not.
    assert can_mutate(session, owner, project.id, series_id).role is Role.OWNER
    assert can_mutate(session, member, project.id, series_id).role is Role.MEMBER
    with pytest.raises(AuthorizationError):
        can_mutate(session, viewer, project.id, series_id)
    with pytest.raises(AuthorizationError):
        can_mutate(session, outsider, project.id, series_id)


def test_non_steward_project_cannot_mutate(session):
    actor = services.create_actor(session)
    p1 = services.create_project(session, actor, "P1")
    p2 = services.create_project(session, actor, "P2")
    series_id = services.create_object(session, actor, p1.id, "protein", "X", payload={})
    # Link the series into P2 (read-only context) and prove P2 link != stewardship.
    services.link_series(session, actor, p2.id, series_id)
    with pytest.raises(AuthorizationError):
        services.append_revision(session, actor, p2.id, series_id, {"organism": None})


def test_mutation_command_rejects_missing_or_stale_grant(session):
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "P")
    series_id = services.create_object(session, actor, project.id, "protein", "X", payload={})

    # A valid grant passes.
    grant = can_mutate(session, actor, project.id, series_id)
    assert grant.resource_id == series_id

    # Freeze stewardship (steward_project_id = NULL) -> a subsequent grant
    # derivation fails closed (stale authority is not trusted).
    stewardship = session.get(ResourceStewardship, series_id)
    stewardship.steward_project_id = None
    session.commit()
    with pytest.raises(AuthorizationError):
        can_mutate(session, actor, project.id, series_id)
    # The mutation command itself also fails closed on the frozen resource.
    with pytest.raises(AuthorizationError):
        services.append_revision(session, actor, project.id, series_id, {"organism": None})


def test_viewer_creation_is_rejected(session):
    _owner, _member, viewer, _outsider, project = _setup(session)
    with pytest.raises(AuthorizationError):
        mutation_capable_membership(session, viewer, project.id)
    with pytest.raises(AuthorizationError):
        services.create_object(session, viewer, project.id, "protein", "X", payload={})


def test_project_deletion_tombstones_and_preserves_global_resources(session):
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "P")
    series_id = services.create_object(session, actor, project.id, "protein", "X", payload={})
    series_resource_id_before = series_id

    services.delete_project(session, actor, project.id)

    tombstoned = services.get_project(session, project.id)
    assert tombstoned.deleted_at is not None
    # Links and memberships hard-deleted; global series still present.
    links = session.scalars(
        select(ProjectResourceLink).where(ProjectResourceLink.project_id == project.id)
    ).all()
    assert links == []
    series = session.get(__import__("revolab.models", fromlist=["ScientificObjectSeries"]).ScientificObjectSeries, series_resource_id_before)
    assert series is not None


def test_tombstoned_project_cannot_mutate(session):
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "P")
    series_id = services.create_object(session, actor, project.id, "protein", "X", payload={})
    services.delete_project(session, actor, project.id)
    with pytest.raises(AuthorizationError):
        can_mutate(session, actor, project.id, series_id)


def test_membership_management_requires_owner(session):
    owner, member, _viewer, _outsider, project = _setup(session)
    new_actor = services.create_actor(session)
    with pytest.raises(AuthorizationError):
        services.add_membership(session, member, project.id, new_actor, Role.MEMBER.value)
    # Owner can add.
    services.add_membership(session, owner, project.id, new_actor, Role.MEMBER.value)
