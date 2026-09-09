"""Phase 5 — Project collaboration & sharing.

Executable evidence for: membership/role operations with the final-owner
invariant, explicit Project visibility, cross-Project sharing with source
visibility authority, the revision => series closure, shared resources are not
shared interpretations, visibility is not stewardship, the project-context
write-time invariant, the preferred-revision pin, and tombstone semantics with
shared resources.
"""

from uuid import uuid4

import pytest
from sqlalchemy import select

from revolab import queries, services
from revolab.domain.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from revolab.enums import ResourceKind, Role
from revolab.models import (
    Decision,
    Evidence,
    ProjectResourceLink,
    ResourceStewardship,
    ScientificObjectRevision,
    ScientificObjectSeries,
)


def _actor(session):
    return services.create_actor(session)


# ---------------------------------------------------------------------------
# 1. Membership / collaboration commands
# ---------------------------------------------------------------------------


def _team(session):
    owner = _actor(session)
    member = _actor(session)
    viewer = _actor(session)
    project = services.create_project(session, owner, "P")
    services.add_membership(session, owner, project.id, member, Role.MEMBER.value)
    services.add_membership(session, owner, project.id, viewer, Role.VIEWER.value)
    return owner, member, viewer, project


def test_membership_roster_is_inspectable_by_any_member(session):
    owner, member, viewer, project = _team(session)
    roster = services.list_memberships(session, owner, project.id)
    ids = {m.actor_id for m in roster}
    assert ids == {owner, member, viewer}
    # A viewer/member may inspect too: visibility is membership-mediated.
    assert {m.actor_id for m in services.list_memberships(session, viewer, project.id)} == ids


def test_membership_role_change_requires_owner(session):
    owner, member, viewer, project = _team(session)
    with pytest.raises(AuthorizationError):
        services.update_membership(session, member, project.id, viewer, Role.MEMBER.value)
    updated = services.update_membership(session, owner, project.id, viewer, Role.MEMBER.value)
    assert updated.role == Role.MEMBER.value


def test_membership_remove_requires_owner(session):
    owner, member, viewer, project = _team(session)
    with pytest.raises(AuthorizationError):
        services.remove_membership(session, member, project.id, viewer)
    services.remove_membership(session, owner, project.id, viewer)
    assert viewer not in {m.actor_id for m in services.list_memberships(session, owner, project.id)}


def test_duplicate_membership_is_a_conflict(session):
    owner, member, _viewer, project = _team(session)
    with pytest.raises(ConflictError):
        services.add_membership(session, owner, project.id, member, Role.VIEWER.value)


def test_adding_unknown_actor_is_not_found(session):
    owner, _member, _viewer, project = _team(session)
    with pytest.raises(NotFoundError):
        services.add_membership(session, owner, project.id, uuid4(), Role.MEMBER.value)


def test_last_owner_cannot_be_demoted_or_removed(session):
    owner, member, _viewer, project = _team(session)
    with pytest.raises(ConflictError):
        services.update_membership(session, owner, project.id, owner, Role.MEMBER.value)
    with pytest.raises(ConflictError):
        services.remove_membership(session, owner, project.id, owner)
    # Promote a second owner, then the first owner may step down.
    services.update_membership(session, owner, project.id, member, Role.OWNER.value)
    services.update_membership(session, owner, project.id, owner, Role.MEMBER.value)
    roles = {m.actor_id: m.role for m in services.list_memberships(session, member, project.id)}
    assert roles.get(owner) == Role.MEMBER.value
    assert roles.get(member) == Role.OWNER.value


def test_tombstoned_project_rejects_membership_ops(session):
    owner, member, _viewer, project = _team(session)
    services.delete_project(session, owner, project.id)
    new_actor = _actor(session)
    with pytest.raises(AuthorizationError):
        services.add_membership(session, owner, project.id, new_actor, Role.MEMBER.value)
    with pytest.raises(AuthorizationError):
        services.list_memberships(session, member, project.id)


# ---------------------------------------------------------------------------
# 2. Project visibility (distinct from membership)
# ---------------------------------------------------------------------------


def test_project_visibility_is_explicit_and_owner_updateable(session):
    owner = _actor(session)
    project = services.create_project(session, owner, "P", visibility="shared_with_members")
    assert project.visibility == "shared_with_members"
    updated = services.update_project(session, owner, project.id, visibility="private")
    assert updated.visibility == "private"
    with pytest.raises(ValidationError):
        services.update_project(session, owner, project.id, visibility="public")


def test_visibility_update_requires_owner(session):
    owner, member, _viewer, project = _team(session)
    with pytest.raises(AuthorizationError):
        services.update_project(session, member, project.id, visibility="shared_with_members")
    # The owner can.
    services.update_project(session, owner, project.id, visibility="shared_with_members")


def test_visibility_does_not_grant_non_member_access(session):
    owner = _actor(session)
    project = services.create_project(session, owner, "P", visibility="shared_with_members")
    outsider = _actor(session)
    with pytest.raises(AuthorizationError):
        services.readable_membership(session, outsider, project.id)
    with pytest.raises(AuthorizationError):
        services.list_memberships(session, outsider, project.id)


# ---------------------------------------------------------------------------
# 3. Cross-Project sharing
# ---------------------------------------------------------------------------


def _two_actor_share(session):
    """A owns `series` (r1 and r2 revs); a_owner shares only r1 into B."""
    a_owner = _actor(session)
    b_owner = _actor(session)
    a = services.create_project(session, a_owner, "A")
    b = services.create_project(session, b_owner, "B")
    services.add_membership(session, b_owner, b.id, a_owner, Role.MEMBER.value)
    series = services.create_object(session, a_owner, a.id, "protein", "X", payload={"chain": "A"})
    r1 = session.scalar(
        select(ScientificObjectRevision).where(
            ScientificObjectRevision.series_id == series,
            ScientificObjectRevision.revision_seq == 1,
        )
    )
    r2 = services.append_revision(session, a_owner, a.id, series, {"chain": "B"})
    services.share_resource(session, a_owner, b.id, r1.revision_id)
    return a_owner, b_owner, a, b, series, r1, r2


def test_share_requires_target_mutation_membership(session):
    a_owner = _actor(session)
    b_owner = _actor(session)
    a = services.create_project(session, a_owner, "A")
    b = services.create_project(session, b_owner, "B")
    series = services.create_object(session, a_owner, a.id, "protein", "X", payload={})
    # No membership in B -> cannot grant B's read lens.
    with pytest.raises(AuthorizationError):
        services.share_resource(session, a_owner, b.id, series)
    # Viewer membership is read-only -> cannot grant the lens either.
    services.add_membership(session, b_owner, b.id, a_owner, Role.VIEWER.value)
    with pytest.raises(AuthorizationError):
        services.share_resource(session, a_owner, b.id, series)


def test_uuid_possession_is_not_share_authority(session):
    a_owner = _actor(session)
    c_owner = _actor(session)
    a = services.create_project(session, a_owner, "A")
    c = services.create_project(session, c_owner, "C")
    series = services.create_object(session, a_owner, a.id, "protein", "X", payload={})
    # c_owner has the UUID but no read path to it -> cannot share.
    with pytest.raises(AuthorizationError):
        services.share_resource(session, c_owner, c.id, series)


def test_share_unknown_resource_is_not_found(session):
    owner = _actor(session)
    project = services.create_project(session, owner, "P")
    # Defense-in-depth: an actor with no read path to the resource is denied as
    # unauthorized rather than told whether the UUID exists (no existence oracle).
    with pytest.raises(AuthorizationError):
        services.share_resource(session, owner, project.id, uuid4())


def test_revision_share_makes_series_visible_but_no_siblings(session):
    _a_owner, _b_owner, _a, b, series, r1, _r2 = _two_actor_share(session)
    links = set(
        session.scalars(
            select(ProjectResourceLink.resource_id).where(ProjectResourceLink.project_id == b.id)
        ).all()
    )
    assert series in links
    assert r1.revision_id in links
    detail = queries.object_detail(session, b.id, series)
    assert [rev["revision_seq"] for rev in detail["visible_revisions"]] == [1]
    assert detail["latest_revision_seq"] == 1
    assert detail["read_only"] is True


def test_future_sibling_revision_stays_private_after_share(session):
    a_owner, _b_owner, a, b, series, _r1, _r2 = _two_actor_share(session)
    services.append_revision(session, a_owner, a.id, series, {"chain": "C"})
    detail = queries.object_detail(session, b.id, series)
    assert [rev["revision_seq"] for rev in detail["visible_revisions"]] == [1]


def test_share_is_idempotent(session):
    a_owner, _b_owner, _a, b, series, r1, _r2 = _two_actor_share(session)
    kind = services.share_resource(session, a_owner, b.id, r1.revision_id)
    assert kind is ResourceKind.SCIENTIFIC_OBJECT_REVISION
    links = set(
        session.scalars(
            select(ProjectResourceLink.resource_id).where(ProjectResourceLink.project_id == b.id)
        ).all()
    )
    assert links == {series, r1.revision_id}


# ---------------------------------------------------------------------------
# 7. Visibility is not stewardship
# ---------------------------------------------------------------------------


def test_non_steward_project_cannot_mutate_shared_resource(session):
    a_owner, b_owner, a, b, series, _r1, _r2 = _two_actor_share(session)
    with pytest.raises(AuthorizationError):
        services.update_series(session, b_owner, b.id, series, name="Renamed-by-B")
    with pytest.raises(AuthorizationError):
        services.append_revision(session, b_owner, b.id, series, {"chain": "B"})
    with pytest.raises(AuthorizationError):
        services.archive_series(session, b_owner, b.id, series)
    with pytest.raises(AuthorizationError):
        services.attach_external_identity(session, b_owner, b.id, series, "uniprot", "P00001")
    # A (the steward) still mutates after sharing.
    renamed = services.update_series(session, a_owner, a.id, series, name="Renamed-by-A")
    assert renamed.name == "Renamed-by-A"


def test_shared_resource_is_read_only_view(session):
    _a_owner, _b_owner, _a, b, series, _r1, _r2 = _two_actor_share(session)
    assert queries.read_only(session, b.id, series) is True


def test_read_only_resolves_revision_through_owning_series_stewardship(session):
    _a_owner, _b_owner, a, b, series, r1, _r2 = _two_actor_share(session)
    # A stewards the series, so its revision is also writable (not read-only)
    # through A — stewardship is per-series, and the lens resolves a revision
    # through its owning series.
    assert queries.read_only(session, a.id, series) is False
    assert queries.read_only(session, a.id, r1.revision_id) is False
    # B holds only the read lens for both the series and the shared revision.
    assert queries.read_only(session, b.id, series) is True
    assert queries.read_only(session, b.id, r1.revision_id) is True


# ---------------------------------------------------------------------------
# 6. Shared resource is not a shared interpretation
# ---------------------------------------------------------------------------


def test_shared_resource_does_not_leak_interpretations(session):
    _a_owner, b_owner, a, b, series, r1, _r2 = _two_actor_share(session)
    b_evidence = services.create_evidence(
        session, b_owner, b.id, kind="computation", polarity="contradicts",
        source_kind="scientific_object_revision", source_id=r1.revision_id,
        target_kind="scientific_object_revision", target_id=r1.revision_id,
        interpretation="B disagrees",
    )
    b_decision = services.create_decision(
        session, b_owner, b.id, title="B conclusion", statement="B's truth",
        cites=[{"evidence_id": b_evidence.id, "cited_as": "supports"}],
        selects=[{"target_id": series, "target_kind": "scientific_object_series"}],
    )
    services.commit_decision(session, b_owner, b.id, b_decision.id)

    detail_b = queries.object_detail(session, b.id, series)
    assert [e["id"] for e in detail_b["evidence"]] == [str(b_evidence.id)]
    assert [d["id"] for d in detail_b["decisions"]] == [str(b_decision.id)]

    # A does NOT inherit B's Evidence/Decision.
    detail_a = queries.object_detail(session, a.id, series)
    assert detail_a["evidence"] == []
    assert detail_a["decisions"] == []


# ---------------------------------------------------------------------------
# 5. Project-context write-time invariant (no ghost knowledge)
# ---------------------------------------------------------------------------


def test_project_cannot_cite_invisible_global_endpoint(session):
    _a_owner, b_owner, _a, b, series, _r1, r2 = _two_actor_share(session)
    with pytest.raises(AuthorizationError):
        services.create_evidence(
            session, b_owner, b.id, kind="computation", polarity="supports",
            source_kind="scientific_object_revision", source_id=r2.revision_id,
            target_kind="scientific_object_revision", target_id=r2.revision_id,
        )
    with pytest.raises(AuthorizationError):
        services.create_decision(
            session, b_owner, b.id, title="D", statement="S",
            selects=[{"target_id": r2.revision_id, "target_kind": "scientific_object_revision"}],
        )
    decision = services.create_decision(
        session, b_owner, b.id, title="D", statement="S",
        selects=[{"target_id": series, "target_kind": "scientific_object_series"}],
    )
    services.commit_decision(session, b_owner, b.id, decision.id)


# ---------------------------------------------------------------------------
# 4. Preferred revision pin (optional project-local derivation)
# ---------------------------------------------------------------------------


def test_preferred_revision_pin_is_project_local(session):
    a_owner = _actor(session)
    a = services.create_project(session, a_owner, "A")
    series = services.create_object(session, a_owner, a.id, "protein", "X", payload={})
    r2 = services.append_revision(session, a_owner, a.id, series, {"chain": "B"})
    services.set_preferred_revision(session, a_owner, a.id, series, r2.revision_id)
    assert queries.object_detail(session, a.id, series)["preferred_revision_id"] == str(
        r2.revision_id
    )
    services.set_preferred_revision(session, a_owner, a.id, series, None)
    assert queries.object_detail(session, a.id, series)["preferred_revision_id"] is None


def test_preferred_revision_pin_must_be_visible(session):
    _a_owner, b_owner, _a, b, series, _r1, r2 = _two_actor_share(session)
    with pytest.raises(AuthorizationError):
        services.set_preferred_revision(session, b_owner, b.id, series, r2.revision_id)


def test_preferred_revision_must_belong_to_series(session):
    a_owner = _actor(session)
    a = services.create_project(session, a_owner, "A")
    s1 = services.create_object(session, a_owner, a.id, "protein", "X", payload={})
    s2 = services.create_object(session, a_owner, a.id, "protein", "Y", payload={})
    other_revision = session.scalar(
        select(ScientificObjectRevision).where(ScientificObjectRevision.series_id == s2)
    )
    with pytest.raises(ValidationError):
        services.set_preferred_revision(session, a_owner, a.id, s1, other_revision.revision_id)


def test_set_preferred_revision_requires_mutation_membership(session):
    a_owner, b_owner, _a, b, series, r1, _r2 = _two_actor_share(session)
    services.update_membership(session, b_owner, b.id, a_owner, Role.VIEWER.value)
    with pytest.raises(AuthorizationError):
        services.set_preferred_revision(session, a_owner, b.id, series, r1.revision_id)


# ---------------------------------------------------------------------------
# 8. Project deletion with shared resources
# ---------------------------------------------------------------------------


def test_deleting_one_project_preserves_shared_resource_and_the_other_context(session):
    a_owner = _actor(session)
    b_owner = _actor(session)
    a = services.create_project(session, a_owner, "A")
    b = services.create_project(session, b_owner, "B")
    services.add_membership(session, b_owner, b.id, a_owner, Role.MEMBER.value)
    series = services.create_object(session, a_owner, a.id, "protein", "X", payload={})
    r1 = session.scalar(
        select(ScientificObjectRevision).where(
            ScientificObjectRevision.series_id == series,
            ScientificObjectRevision.revision_seq == 1,
        )
    )
    a_evidence = services.create_evidence(
        session, a_owner, a.id, kind="computation", polarity="supports",
        source_kind="scientific_object_revision", source_id=r1.revision_id,
        target_kind="scientific_object_revision", target_id=r1.revision_id,
    )
    a_decision = services.create_decision(
        session, a_owner, a.id, title="A decision", statement="A truth",
        cites=[{"evidence_id": a_evidence.id, "cited_as": "supports"}],
        selects=[{"target_id": series, "target_kind": "scientific_object_series"}],
    )
    services.commit_decision(session, a_owner, a.id, a_decision.id)

    services.share_resource(session, a_owner, b.id, r1.revision_id)
    b_evidence = services.create_evidence(
        session, b_owner, b.id, kind="observation", polarity="neutral",
        target_kind="scientific_object_revision", target_id=r1.revision_id,
    )
    b_decision = services.create_decision(
        session, b_owner, b.id, title="B decision", statement="B truth",
        selects=[{"target_id": series, "target_kind": "scientific_object_series"}],
    )
    services.commit_decision(session, b_owner, b.id, b_decision.id)

    services.delete_project(session, a_owner, a.id)

    assert session.get(ScientificObjectSeries, series) is not None
    assert session.get(ScientificObjectRevision, r1.revision_id) is not None
    assert (
        session.scalar(
            select(ProjectResourceLink.id).where(ProjectResourceLink.project_id == a.id)
        )
        is None
    )
    assert session.get(ResourceStewardship, series).steward_project_id is None
    assert session.get(Evidence, a_evidence.id).archived_at is not None
    assert session.get(Decision, a_decision.id).archived_at is not None

    assert series in set(
        session.scalars(
            select(ProjectResourceLink.resource_id).where(ProjectResourceLink.project_id == b.id)
        ).all()
    )
    detail_b = queries.object_detail(session, b.id, series)
    assert [e["id"] for e in detail_b["evidence"]] == [str(b_evidence.id)]
    assert [d["id"] for d in detail_b["decisions"]] == [str(b_decision.id)]
    assert session.get(Evidence, b_evidence.id).archived_at is None
    assert session.get(Decision, b_decision.id).archived_at is None


# ---------------------------------------------------------------------------
# P1 (PR review): existing-reference reuse must not bypass sharing authority
# ---------------------------------------------------------------------------


def _reference_fixture(session):
    actor_a = services.create_actor(session)
    actor_c = services.create_actor(session)
    a = services.create_project(session, actor_a, "A")
    c = services.create_project(session, actor_c, "C")
    run = services.create_run_reference(session, actor_a, a.id, "revocompute", "run-private")
    sess_ref = services.create_session_reference(session, actor_a, a.id, "revocompute", "sess-private")
    artifact = services.create_artifact_reference(
        session, actor_a, a.id, "revocompute", "art-private",
        content_type="text/plain", size=3, checksum="abc", version_id="v1",
    )
    literature = services.create_literature_reference(session, actor_a, a.id, "doi", "10.1/private")
    return actor_a, actor_c, a, c, run, sess_ref, artifact, literature


def test_existing_reference_identity_is_not_share_authority(session):
    _actor_a, actor_c, _a, c, run, sess_ref, artifact, literature = _reference_fixture(session)
    # actor_c knows every durable external identity but has no read path to any
    # of them: reuse must be denied for every reference kind.
    with pytest.raises(AuthorizationError):
        services.create_run_reference(session, actor_c, c.id, "revocompute", "run-private")
    with pytest.raises(AuthorizationError):
        services.create_session_reference(session, actor_c, c.id, "revocompute", "sess-private")
    with pytest.raises(AuthorizationError):
        services.create_artifact_reference(
            session, actor_c, c.id, "revocompute", "art-private",
            content_type="text/plain", size=3, checksum="abc", version_id="v1",
        )
    with pytest.raises(AuthorizationError):
        services.create_literature_reference(session, actor_c, c.id, "doi", "10.1/private")
    # Nothing leaked into the target Project's link set.
    assert run.run_id not in {
        link.resource_id
        for link in session.scalars(
            select(ProjectResourceLink).where(ProjectResourceLink.project_id == c.id)
        )
    }
    assert sess_ref.session_id not in {
        link.resource_id
        for link in session.scalars(
            select(ProjectResourceLink).where(ProjectResourceLink.project_id == c.id)
        )
    }
    assert artifact.artifact_id not in {
        link.resource_id
        for link in session.scalars(
            select(ProjectResourceLink).where(ProjectResourceLink.project_id == c.id)
        )
    }
    assert literature.literature_id not in {
        link.resource_id
        for link in session.scalars(
            select(ProjectResourceLink).where(ProjectResourceLink.project_id == c.id)
        )
    }


def test_existing_reference_reuse_succeeds_when_source_visible(session):
    actor_a, actor_c, a, c, run, _sess, _artifact, _literature = _reference_fixture(session)
    # Idempotent already-visible reuse in the source Project.
    again = services.create_run_reference(session, actor_a, a.id, "revocompute", "run-private")
    assert again.run_id == run.run_id
    # Source-read reuse into a second Project where the actor is a member.
    services.add_membership(session, actor_c, c.id, actor_a, Role.MEMBER.value)
    reused = services.create_run_reference(session, actor_a, c.id, "revocompute", "run-private")
    assert reused.run_id == run.run_id
    assert run.run_id in {
        link.resource_id
        for link in session.scalars(
            select(ProjectResourceLink).where(ProjectResourceLink.project_id == c.id)
        )
    }


# ---------------------------------------------------------------------------
# P2 (PR review): concurrent duplicate shares must resolve as idempotent
# ---------------------------------------------------------------------------


def test_share_satisfied_helper_recognizes_link_state(session):
    from revolab.services import _share_is_satisfied

    a_owner, _b_owner, a, b, series, r1, _r2 = _two_actor_share(session)
    # r1 was shared into B: both the revision and its owning series are visible.
    assert _share_is_satisfied(session, b.id, series) is True
    assert _share_is_satisfied(session, b.id, r1.revision_id) is True
    # An A-private object is not visible through B.
    other = services.create_object(session, a_owner, a.id, "ligand", "L", payload={"smiles": "CCO"})
    assert _share_is_satisfied(session, b.id, other) is False
