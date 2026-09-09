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

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from revolab.capabilities import (
    LEGAL_COMPUTE_INPUT_KINDS,
    ArtifactHandle,
    ExternalArtifactRef,
    InputBinding,
    ResolvedInput,
    RunHandle,
)
from revolab.content_store import ContentStore
from revolab.domain import compute as compute_domain
from revolab.domain import knowledge, persistence, provenance, scientific_object
from revolab.domain.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from revolab.domain.identity import (
    add_credential_binding,
    binding_for,
    can_mutate,
    mutation_capable_membership,
    owner_membership,
)
from revolab.domain.identity import (
    list_credential_bindings as identity_list_credential_bindings,
)
from revolab.domain.identity import (
    readable_membership as _readable_membership,
)
from revolab.drivers import DriverRegistry
from revolab.enums import CapabilityKind, ProjectVisibility, RelationType, ResourceKind, Role
from revolab.models import (
    Actor,
    ArtifactReference,
    Decision,
    DecisionTarget,
    Evidence,
    ExternalProviderCredentialBinding,
    GlobalProvenanceEdge,
    Project,
    ProjectMembership,
    ProjectResourceLink,
    ResourceStewardship,
    ScientificObjectRevision,
)
from revolab.secret_store import SecretRef, SecretStore


def readable_membership(session: Session, actor_id: UUID, project_id: UUID) -> object:
    return _readable_membership(session, actor_id, project_id)


def _resource_kind(session: Session, resource_id: UUID) -> ResourceKind:
    return persistence.resource_kind(session, resource_id)


def _require_visible(session: Session, project_id: UUID, resource_id: UUID) -> None:
    persistence.require_visible(session, project_id, resource_id)


def _is_frozen(session: Session, evidence_id: UUID) -> bool:
    return provenance.is_frozen(session, evidence_id)


# ---------------------------------------------------------------------------
# Application policy + credential provider-kind validation
# ---------------------------------------------------------------------------

# Capability kinds whose "requested operation" is a read/projection. Action
# capabilities (compute submission, design export, interactive handoff) require a
# mutation-capable membership. This is the Phase-3 minimal policy, not an RBAC
# engine: the ONLY authorization truth remains Phase-1 ProjectMembership roles.
READ_ONLY_CAPABILITY_KINDS = frozenset(
    {CapabilityKind.SEARCH, CapabilityKind.ARTIFACT_RESOLUTION}
)


def project_policy_permits(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    capability_kind: CapabilityKind,
) -> bool:
    """Application policy: does the Actor's Project membership permit invoking
    this capability kind? Read-only kinds accept any readable membership; action
    kinds require owner/member (mutation-capable). Derived per query, never
    stored; a non-permitting membership returns False, not an exception."""
    try:
        if capability_kind in READ_ONLY_CAPABILITY_KINDS:
            _readable_membership(session, actor_id, project_id)
        else:
            mutation_capable_membership(session, actor_id, project_id)
    except AuthorizationError:
        return False
    return True


def _require_provider_kind(
    registry: DriverRegistry, provider_key: str, kind: str
) -> None:
    """A credential binding names a real Provider and one of its declared
    required credential kinds (provider identity is registry truth, not an
    arbitrary key/value vault)."""
    try:
        handle = registry.get(provider_key)
    except LookupError as exc:  # LookupError -> NotFoundError at the boundary
        raise NotFoundError(f"unknown provider: {provider_key}") from exc
    if kind not in handle.driver.required_credential_kinds:
        raise ValidationError(
            f"credential kind {kind!r} is not required by provider {provider_key!r}"
        )


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
    try:
        visibility_value = ProjectVisibility(visibility)
    except ValueError as exc:
        raise ValidationError(f"unknown project visibility {visibility!r}") from exc
    project = Project(name=name, description=description, visibility=visibility_value.value)
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


def _require_role(role: str) -> Role:
    try:
        return Role(role)
    except ValueError as exc:
        raise ValidationError(f"unknown role {role!r}") from exc


def _requires_actor(session: Session, actor_id: UUID) -> None:
    if session.get(Actor, actor_id) is None:
        raise NotFoundError("actor not found")


def _is_membership_uniqueness_conflict(exc: IntegrityError) -> bool:
    """Narrow detection of the project_memberships composite PK uniqueness.

    Unrelated integrity failures are re-raised untouched (same fail-closed
    posture as `_is_binding_uniqueness_conflict` in the Identity domain)."""
    orig = exc.orig
    diagnostics = getattr(orig, "diag", None)
    if diagnostics is not None:  # PostgreSQL psycopg
        return bool(diagnostics.constraint_name == "project_memberships_pkey")
    message = str(orig)
    return "UNIQUE constraint failed" in message and "project_memberships" in message


def _is_link_uniqueness_conflict(exc: IntegrityError) -> bool:
    """Narrow detection of the `uq_link_project_resource` unique constraint."""
    orig = exc.orig
    diagnostics = getattr(orig, "diag", None)
    if diagnostics is not None:  # PostgreSQL psycopg
        return bool(diagnostics.constraint_name == "uq_link_project_resource")
    message = str(orig)
    return (
        "UNIQUE constraint failed" in message
        and "project_resource_links.project_id" in message
    )


def _other_owner_count(session: Session, project_id: UUID, excluding_actor_id: UUID) -> int:
    """Count owner memberships in an active Project other than `excluding_actor_id`.

    This is the single place the final-required-owner invariant is derived:
    membership/role changes must never leave an active Project with zero owners.

    ALL owner rows of the Project are locked under `FOR UPDATE` (not just the
    counted subset) so that concurrent owner demotions/removals serialize: the
    second transaction blocks until the first commits, then re-counts against the
    committed owner set — closing the check-then-act window that would otherwise
    let two owners demote each other into a zero-owner Project. On SQLite the
    clause is a no-op, but SQLite already serializes writes.
    """
    owner_ids = list(
        session.scalars(
            select(ProjectMembership.actor_id)
            .where(
                ProjectMembership.project_id == project_id,
                ProjectMembership.role == Role.OWNER.value,
            )
            .with_for_update()
        ).all()
    )
    return sum(1 for actor in owner_ids if actor != excluding_actor_id)


def _require_membership(session: Session, project_id: UUID, member_actor_id: UUID) -> ProjectMembership:
    membership = session.scalar(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.actor_id == member_actor_id,
        )
    )
    if membership is None:
        raise NotFoundError("actor is not a member of the project")
    return membership


def add_membership(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    member_actor_id: UUID,
    role: str,
) -> ProjectMembership:
    owner_membership(session, actor_id, project_id)
    role_value = _require_role(role)
    _requires_actor(session, member_actor_id)
    if (
        session.scalar(
            select(ProjectMembership.actor_id).where(
                ProjectMembership.project_id == project_id,
                ProjectMembership.actor_id == member_actor_id,
            )
        )
        is not None
    ):
        raise ConflictError("actor is already a member of the project")
    membership = ProjectMembership(project_id=project_id, actor_id=member_actor_id, role=role_value.value)
    session.add(membership)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _is_membership_uniqueness_conflict(exc):
            raise ConflictError("actor is already a member of the project") from exc
        raise
    session.refresh(membership)
    return membership


def list_memberships(session: Session, actor_id: UUID, project_id: UUID) -> list[ProjectMembership]:
    """Inspect the Project membership roster. Any readable membership may see it:
    membership is the visibility vector, and visibility does not require an
    owner/member role (consistent with the derived read projection)."""
    readable_membership(session, actor_id, project_id)
    return list(
        session.scalars(
            select(ProjectMembership)
            .where(ProjectMembership.project_id == project_id)
            .order_by(ProjectMembership.created_at)
        )
    )


def update_membership(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    member_actor_id: UUID,
    role: str,
) -> ProjectMembership:
    """Change a member's role. Owner-only; the final required owner is protected."""
    owner_membership(session, actor_id, project_id)
    role_value = _require_role(role)
    membership = _require_membership(session, project_id, member_actor_id)
    if (
        membership.role == Role.OWNER.value
        and role_value is not Role.OWNER
        and not _other_owner_count(session, project_id, member_actor_id)
    ):
        raise ConflictError("project must retain at least one owner")
    membership.role = role_value.value
    session.commit()
    session.refresh(membership)
    return membership


def remove_membership(
    session: Session, actor_id: UUID, project_id: UUID, member_actor_id: UUID
) -> None:
    """Remove a member. Owner-only; the final required owner is protected."""
    owner_membership(session, actor_id, project_id)
    membership = _require_membership(session, project_id, member_actor_id)
    if (
        membership.role == Role.OWNER.value
        and not _other_owner_count(session, project_id, member_actor_id)
    ):
        raise ConflictError("project must retain at least one owner")
    session.delete(membership)
    session.commit()


def update_project(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    *,
    name: str | None = None,
    description: str | None = None,
    visibility: str | None = None,
    clear_description: bool = False,
) -> Project:
    """Update Project metadata/visibility. Owner-only.

    Phase-5 visibility is an explicit Project-label (`private |
    shared-with-members`); it never gates access on its own — read authorization
    remains membership-derived (`readable_membership`). `public` is deferred.

    `clear_description` distinguishes an explicit `description: null` (clear the
    field) from an omitted description (leave it unchanged).
    """
    owner_membership(session, actor_id, project_id)
    project = get_project(session, project_id)
    if name is not None:
        project.name = name
    if clear_description:
        project.description = None
    elif description is not None:
        project.description = description
    if visibility is not None:
        try:
            project.visibility = ProjectVisibility(visibility).value
        except ValueError as exc:
            raise ValidationError(f"unknown project visibility {visibility!r}") from exc
    session.commit()
    session.refresh(project)
    return project


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
    """Internal read-lens link primitive (used where the caller previously
    established the resource's context — e.g. import/provenance orchestration and
    tests). Cross-Project sharing from outside the Project must go through
    `share_resource`, which additionally validates source visibility/share
    authority; do not treat possession of a UUID as permission to link here."""
    mutation_capable_membership(session, actor_id, project_id)
    provenance.link_series(session, project_id, resource_id)


def _visible_project_ids(session: Session, actor_id: UUID, resource_id: UUID) -> set[UUID]:
    """The active Projects through which `actor` can READ `resource` (a derived
    projection over membership and link — never a stored ACL)."""
    return {
        project_id
        for project_id in session.scalars(
            select(ProjectMembership.project_id)
            .join(Project, Project.id == ProjectMembership.project_id)
            .join(ProjectResourceLink, ProjectResourceLink.project_id == Project.id)
            .where(
                ProjectMembership.actor_id == actor_id,
                Project.deleted_at.is_(None),
                ProjectResourceLink.resource_id == resource_id,
            )
        )
    }


def share_resource(session: Session, actor_id: UUID, target_project_id: UUID, resource_id: UUID) -> ResourceKind:
    """First-class cross-Project share: make an already-existing global resource
    part of another Project's context (a `ProjectResourceLink` — the read lens).

    Authority (TODO #3):
      * the actor holds a mutation-capable membership in the TARGET Project
        (granting the read lens requires target-Project authority, ADR-0008);
      * the actor can READ the resource through at least one OTHER active Project
        (the source of the share) — possession of a UUID is NOT permission to
        link an arbitrary resource.

    Source-of-share authority is deliberately the read lens itself (any readable
    membership — owner/member/viewer — in an active Project that links the
    resource): sharing only mints another read lens in the target Project and
    never transfers stewardship, mutation authority, or external credentials, so
    a viewer re-sharing a resource they can already read grants no escalation.

    Revision ⇒ series visibility closure (TODO #4): sharing a revision also links
    its owning Series into the TARGET Project (sibling revisions stay private).
    Nothing is copied: the same global `resource_id` becomes visible through the
    target Project's link set only.
    """
    mutation_capable_membership(session, actor_id, target_project_id)
    kind = persistence.resource_kind(session, resource_id)
    if persistence.is_visible(session, target_project_id, resource_id):
        return kind  # idempotent: the read lens already exists
    sources = _visible_project_ids(session, actor_id, resource_id) - {target_project_id}
    if not sources:
        raise AuthorizationError(
            "cannot share a resource the actor cannot read through another project"
        )
    provenance.share_into_project(session, target_project_id, resource_id)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _is_link_uniqueness_conflict(exc):
            # A concurrent share won the same link insert: the read lens already
            # exists, so the operation is idempotently satisfied.
            raise ConflictError("resource is already linked through this project") from exc
        raise
    return kind


def set_preferred_revision(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    series_id: UUID,
    revision_id: UUID | None,
) -> None:
    """Set/clear the Project-local preferred-revision pin on a series link.

    Project-local context mutation (the link is owned by the Project domain), so
    a mutation-capable membership authorizes it. The pin must point at a revision
    of that series that is already visible through this Project — never a global
    `current` pointer, never a sibling revision leaked from another Project.
    """
    mutation_capable_membership(session, actor_id, project_id)
    if persistence.resource_kind(session, series_id) is not ResourceKind.SCIENTIFIC_OBJECT_SERIES:
        raise ValidationError("preferred revision requires a scientific_object_series")
    if not persistence.is_visible(session, project_id, series_id):
        raise AuthorizationError("series is not visible through this project")
    if revision_id is None:
        link = _require_link(session, project_id, series_id)
        link.preferred_revision_id = None
        session.commit()
        return
    if persistence.resource_kind(session, revision_id) is not ResourceKind.SCIENTIFIC_OBJECT_REVISION:
        raise ValidationError("preferred revision must name a scientific_object_revision")
    if persistence.revision_series_id(session, revision_id) != series_id:
        raise ValidationError("preferred revision must belong to the series")
    if not persistence.is_visible(session, project_id, revision_id):
        raise AuthorizationError("preferred revision must already be visible through this project")
    link = _require_link(session, project_id, series_id)
    link.preferred_revision_id = revision_id
    session.commit()


def _require_link(session: Session, project_id: UUID, resource_id: UUID) -> ProjectResourceLink:
    link = session.scalar(
        select(ProjectResourceLink).where(
            ProjectResourceLink.project_id == project_id,
            ProjectResourceLink.resource_id == resource_id,
        )
    )
    if link is None:
        raise NotFoundError("resource link not found in project")
    return link


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
        provenance.assert_reference_compatible(
            existing,
            task_type=task_type,
            input_parameter_digest=input_parameter_digest,
            submitted_at=submitted_at,
        )
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
        provenance.assert_reference_compatible(
            existing, checksum=checksum, size=size, content_type=content_type
        )
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
    """The frozen #5 creator authority (SCIENTIFIC_GRAPH.md): task-submission
    authority + read(input). A shared/visible input is consumable by another
    Project without transferring mutation authority over its source."""
    mutation_capable_membership(session, actor_id, project_id)
    return provenance.add_consumed_input(session, actor_id, project_id, source_id, target_id)


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
    if not persistence.is_visible(session, project_id, series_id):
        raise AuthorizationError("series must be visible in the project before importing into it")
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


# ---------------------------------------------------------------------------
# Compute invocation (Phase 4): neutral InputBinding resolution + typed
# RunReference/ArtifactReference persistence over the existing graph semantics.
# The external side effect (the provider call) is performed by the
# Provider/Capability domain (`revolab.domain.compute`); this module owns the
# REvoLab-side scientific context produced from it.
# ---------------------------------------------------------------------------


def resolve_compute_input(
    session: Session,
    project_id: UUID,
    binding: InputBinding,
    *,
    store: ContentStore,
) -> ResolvedInput:
    """Resolve one neutral InputBinding into provider-neutral material.

    A ScientificObjectRevision contributes its typed JSON payload serialized as
    bytes (verified against its stored checksum); an internal `revolab` artifact
    contributes its ContentStore bytes; an external artifact contributes only its
    immutable identity card — its bytes remain owned by the external provider
    (the driver decides whether to reference or re-resolve them).
    """
    if binding.kind not in LEGAL_COMPUTE_INPUT_KINDS:
        raise ValidationError("compute input kind must be a revision or an artifact reference")
    persistence.require_visible(session, project_id, binding.resource_id)

    if binding.kind is ResourceKind.SCIENTIFIC_OBJECT_REVISION:
        revision = session.get(ScientificObjectRevision, binding.resource_id)
        if revision is None:
            raise NotFoundError("revision not found")
        payload = revision.payload or {}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        if hashlib.sha256(raw).hexdigest() != revision.checksum:
            raise ConflictError("revision payload failed its integrity check")
        return ResolvedInput(
            role=binding.role,
            filename=f"revision-{revision.series_id.hex[:8]}-r{revision.revision_seq}.json",
            content_type="application/json",
            data=raw,
        )

    artifact = session.get(ArtifactReference, binding.resource_id)
    if artifact is None:
        raise NotFoundError("artifact reference not found")
    if artifact.authority == "revolab":
        return ResolvedInput(
            role=binding.role,
            filename=f"artifact-{artifact.native_id[:12]}.bin",
            content_type=artifact.content_type or "application/octet-stream",
            data=store.get(artifact.native_id),
        )
    return ResolvedInput(
        role=binding.role,
        filename=f"{artifact.authority}-{artifact.native_id[:12]}",
        content_type=artifact.content_type,
        external=ExternalArtifactRef(
            authority=artifact.authority,
            native_id=artifact.native_id,
            version_id=artifact.version_id,
            content_type=artifact.content_type,
            size=artifact.size,
            checksum=artifact.checksum,
        ),
    )


def _edge_exists(
    session: Session, relation_type: RelationType, source_id: UUID, target_id: UUID
) -> bool:
    return (
        session.scalar(
            select(GlobalProvenanceEdge.edge_id)
            .where(
                GlobalProvenanceEdge.relation_type == relation_type.value,
                GlobalProvenanceEdge.source_id == source_id,
                GlobalProvenanceEdge.target_id == target_id,
            )
            .limit(1)
        )
        is not None
    )


def record_compute_run(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    handle: RunHandle,
    *,
    inputs: list[UUID],
) -> dict[str, Any]:
    """Persist the immutable RunReference identity card plus the
    `consumed_as_input_by` edges for the external run. This stores only
    reference identity + scientific provenance — never the provider's mutable
    execution state."""
    run = create_run_reference(
        session,
        actor_id,
        project_id,
        handle.authority,
        handle.native_id,
        task_type=handle.task_type,
    )
    edges = []
    for source_id in inputs:
        if _edge_exists(session, RelationType.CONSUMED_AS_INPUT_BY, source_id, run.run_id):
            continue
        edges.append(add_consumed_input(session, actor_id, project_id, source_id, run.run_id))
    return {
        "run_resource_id": run.run_id,
        "authority": run.authority,
        "native_id": run.native_id,
        "task_type": run.task_type,
        "consumed_edges": [edge.edge_id for edge in edges],
    }


def record_compute_artifacts(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    run_resource_id: UUID,
    *,
    artifacts: list[ArtifactHandle],
) -> list[dict[str, Any]]:
    """Persist immutable ArtifactReference identity cards + `produced` edges for
    an external run's results. Byte identity facts (checksum/size/content type)
    are recorded only when the provider reports them."""
    recorded: list[dict[str, Any]] = []
    for handle in artifacts:
        artifact = create_artifact_reference(
            session,
            actor_id,
            project_id,
            handle.authority,
            handle.native_id,
            content_type=handle.content_type,
            size=handle.size,
            checksum=handle.checksum,
            version_id=handle.version_id,
        )
        if not _edge_exists(session, RelationType.PRODUCED, run_resource_id, artifact.artifact_id):
            record_produced(session, actor_id, project_id, run_resource_id, artifact.artifact_id)
        recorded.append(_reference_identity(artifact.artifact_id, artifact))
    return recorded


def compute_submit(
    session: Session,
    registry: DriverRegistry,
    secret_store: SecretStore,
    content_store: ContentStore,
    actor_id: UUID,
    project_id: UUID,
    provider_key: str,
    task_kind: str,
    bindings: list[InputBinding],
    params: dict[str, Any],
) -> dict[str, Any]:
    """One real compute submission: every precondition is validated BEFORE the
    external side effect, then the live provider call, then REvoLab-side
    RunReference + input provenance."""
    permitted = project_policy_permits(session, actor_id, project_id, CapabilityKind.COMPUTE)
    # Preflight the accepted #5 authority BEFORE any external side effect:
    # task-submission authority (mutation-capable membership) + read(input).
    # Stewardship over the input source is NOT required — a shared, visible
    # input must be consumable without transferring mutation authority.
    mutation_capable_membership(session, actor_id, project_id)
    for binding in bindings:
        persistence.require_visible(session, project_id, binding.resource_id)
    resolved_inputs = [
        resolve_compute_input(session, project_id, binding, store=content_store)
        for binding in bindings
    ]
    handle = compute_domain.submit_compute(
        session,
        registry,
        secret_store,
        actor_id,
        provider_key,
        task_kind,
        resolved_inputs,
        params,
        permitted=permitted,
    )
    return record_compute_run(
        session,
        actor_id,
        project_id,
        handle,
        inputs=[binding.resource_id for binding in bindings],
    )


def compute_refresh_artifacts(
    session: Session,
    registry: DriverRegistry,
    secret_store: SecretStore,
    actor_id: UUID,
    project_id: UUID,
    provider_key: str,
    run_resource_id: UUID,
    run_native_id: str,
) -> list[dict[str, Any]]:
    """Live-enumerate an external run's artifacts and persist durable
    ArtifactReferences + `produced` edges. Provider outage raises a typed
    CapabilityError; stored references are never rewritten."""
    permitted = project_policy_permits(session, actor_id, project_id, CapabilityKind.COMPUTE)
    handles = compute_domain.list_artifacts(
        session,
        registry,
        secret_store,
        actor_id,
        provider_key,
        run_native_id,
        permitted=permitted,
    )
    return record_compute_artifacts(
        session,
        actor_id,
        project_id,
        run_resource_id,
        artifacts=handles,
    )


def _reference_identity(resource_id: UUID, row: Any) -> dict[str, Any]:
    return {
        "resource_id": resource_id,
        "authority": getattr(row, "authority", None),
        "native_id": getattr(row, "native_id", None),
        "content_type": getattr(row, "content_type", None),
        "size": getattr(row, "size", None),
        "checksum": getattr(row, "checksum", None),
        "version_id": getattr(row, "version_id", None),
        "revoked_at": getattr(row, "revoked_at", None),
    }


# ---------------------------------------------------------------------------
# Actor-scoped credential management (Secret store + non-secret binding saga)
# ---------------------------------------------------------------------------
#
# Two non-transactional systems are coordinated (TODO.md section 7): the Secret
# store write and the PostgreSQL binding row. Ordering is secret-first with the
# binding COMMIT as the linearization point, plus best-effort compensation. This
# is deliberately NOT a distributed transaction. Availability is binding
# presence (ADR-0012); material presence is re-checked at lease time, so with a
# non-durable Secret store a lost material raises explicitly rather than being
# silently treated as still live. A failed post-commit material cleanup leaves an
# unreachable orphan secret (never the reverse within the store's lifetime).

logger = logging.getLogger(__name__)


def _delete_material(store: SecretStore, old_ref: SecretRef, kind: str) -> None:
    """Best-effort post-commit cleanup. Must not fail an already-committed bind
    mutation; log loudly instead (kind only — never the reference or material)."""
    try:
        store.delete(old_ref)
    except Exception:  # pragma: no cover - defensive compensation path
        logger.error("failed to delete superseded secret material for credential kind %r", kind)


def _require_actor(session: Session, actor_id: UUID) -> None:
    if session.get(Actor, actor_id) is None:
        raise NotFoundError("actor not found")


def provision_credential(
    session: Session,
    store: SecretStore,
    registry: DriverRegistry,
    actor_id: UUID,
    provider_key: str,
    kind: str,
    secret_value: str,
) -> ExternalProviderCredentialBinding:
    """Create an Actor-scoped binding. The Secret store owns the material; Core
    persists only the resulting binding + opaque reference. `provider_key` must
    name a registered Provider and `kind` one of its required credential kinds."""
    _require_actor(session, actor_id)
    _require_provider_kind(registry, provider_key, kind)
    secret_ref = store.put(secret_value)
    try:
        binding = add_credential_binding(
            session, actor_id, provider_key, kind, str(secret_ref)
        )
        session.commit()
    except Exception:
        session.rollback()
        store.delete(secret_ref)
        raise
    session.refresh(binding)
    return binding


def rotate_credential(
    session: Session,
    store: SecretStore,
    registry: DriverRegistry,
    actor_id: UUID,
    provider_key: str,
    kind: str,
    secret_value: str,
) -> ExternalProviderCredentialBinding:
    """Explicit replacement/rotation: a new `secret_ref` supersedes the old one."""
    _require_actor(session, actor_id)
    _require_provider_kind(registry, provider_key, kind)
    binding = binding_for(session, actor_id, provider_key, kind)
    if binding is None:
        raise NotFoundError("credential binding not found")
    old_ref = SecretRef(binding.secret_ref)
    new_ref = store.put(secret_value)
    try:
        binding.secret_ref = str(new_ref)
        session.commit()
    except Exception:
        session.rollback()
        store.delete(new_ref)
        raise
    session.refresh(binding)
    _delete_material(store, old_ref, kind)
    return binding


def revoke_credential(
    session: Session,
    store: SecretStore,
    actor_id: UUID,
    provider_key: str,
    kind: str,
) -> None:
    """Explicit revocation: hard-delete the binding, then best-effort delete the
    secret material (the binding row is the durable truth)."""
    _require_actor(session, actor_id)
    binding = binding_for(session, actor_id, provider_key, kind)
    if binding is None:
        raise NotFoundError("credential binding not found")
    old_ref = SecretRef(binding.secret_ref)
    session.delete(binding)
    session.commit()
    _delete_material(store, old_ref, kind)


def list_credential_bindings(
    session: Session, actor_id: UUID
) -> list[ExternalProviderCredentialBinding]:
    """Actor-scoped read of the caller's own bindings (presence/status only)."""
    return identity_list_credential_bindings(session, actor_id)
