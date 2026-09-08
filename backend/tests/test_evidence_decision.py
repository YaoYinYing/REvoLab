"""Evidence lifecycle + Decision draft/commit promotion + ghost-knowledge
rejection + evidence freeze semantics."""

import pytest
from sqlalchemy import select

from revolab import services
from revolab.domain.errors import AuthorizationError, ConflictError, ValidationError
from revolab.models import DecisionEvidence, DecisionTarget


def _project(session):
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "P")
    return actor, project


def _object(session, actor, project, obj_type="protein", name="X", payload=None):
    return services.create_object(session, actor, project.id, obj_type, name, payload=payload or {})


def _evidence(session, actor, project, revision_id, **kwargs):
    return services.create_evidence(
        session,
        actor,
        project.id,
        kind=kwargs.pop("kind", "computation"),
        role=kwargs.pop("role", "primary_support"),
        interpretation=kwargs.pop("interpretation", "interpretation"),
        polarity=kwargs.pop("polarity", "supports"),
        source_kind="scientific_object_revision",
        source_id=revision_id,
        target_kind="scientific_object_revision",
        target_id=revision_id,
        **kwargs,
    )


def test_evidence_interpretive_fields_mutable_while_unfrozen(session):
    actor, project = _project(session)
    series = _object(session, actor, project)
    revision = services.append_revision(session, actor, project.id, series, {"chain": "A"})
    evidence = _evidence(session, actor, project, revision.revision_id)
    updated = services.update_evidence(session, actor, project.id, evidence.id, polarity="contradicts")
    assert updated.polarity == "contradicts"


def test_evidence_freezes_only_after_committed_decision_cites(session):
    actor, project = _project(session)
    series = _object(session, actor, project)
    revision = services.append_revision(session, actor, project.id, series, {"chain": "A"})
    evidence = _evidence(session, actor, project, revision.revision_id)

    decision = services.create_decision(
        session, actor, project.id, title="D", statement="S",
        cites=[{"evidence_id": evidence.id, "cited_as": "supports"}],
    )
    # A draft Decision does not freeze the evidence.
    services.update_evidence(session, actor, project.id, evidence.id, polarity="neutral")
    services.commit_decision(session, actor, project.id, decision.id)
    # Now it is frozen; correction is a new Evidence, not an in-place edit.
    with pytest.raises(ConflictError):
        services.update_evidence(session, actor, project.id, evidence.id, polarity="supports")


def test_evidence_freezes_when_another_evidence_targets_it(session):
    actor, project = _project(session)
    series = _object(session, actor, project)
    revision = services.append_revision(session, actor, project.id, series, {"chain": "A"})
    first = _evidence(session, actor, project, revision.revision_id)
    services.create_evidence(
        session, actor, project.id, kind="observation",
        target_kind="evidence", target_id=first.id,
    )
    with pytest.raises(ConflictError):
        services.update_evidence(session, actor, project.id, first.id, polarity="supports")


def test_draft_decision_mutable_and_not_project_truth(session):
    actor, project = _project(session)
    series = _object(session, actor, project)
    revision = services.append_revision(session, actor, project.id, series, {"chain": "A"})
    evidence = _evidence(session, actor, project, revision.revision_id)

    decision = services.create_decision(session, actor, project.id, title="D", statement="before")
    updated = services.update_decision(
        session, actor, project.id, decision.id, statement="after",
        cites=[{"evidence_id": evidence.id, "cited_as": "supports"}],
        selects=[{"target_id": series, "target_kind": "scientific_object_series"}],
    )
    assert updated.status == "draft"
    assert updated.statement == "after"
    # Nothing materialized as knowledge edges while draft.
    assert session.scalars(select(DecisionEvidence)).all() == []
    assert session.scalars(select(DecisionTarget)).all() == []


def test_commit_atomically_materializes_edges(session):
    actor, project = _project(session)
    series = _object(session, actor, project)
    revision = services.append_revision(session, actor, project.id, series, {"chain": "A"})
    evidence = _evidence(session, actor, project, revision.revision_id)
    decision = services.create_decision(
        session, actor, project.id, title="D", statement="S",
        cites=[{"evidence_id": evidence.id, "cited_as": "supports"}],
        selects=[{"target_id": series, "target_kind": "scientific_object_series"}],
    )
    committed = services.commit_decision(session, actor, project.id, decision.id)
    assert committed.status == "committed"
    assert committed.committed_at is not None
    cites = session.scalars(select(DecisionEvidence)).all()
    selects = session.scalars(select(DecisionTarget)).all()
    assert len(cites) == 1 and cites[0].cited_as == "supports"
    assert len(selects) == 1 and selects[0].target_id == series


def test_committed_decision_immutable(session):
    actor, project = _project(session)
    decision = services.create_decision(session, actor, project.id, title="D", statement="S")
    services.commit_decision(session, actor, project.id, decision.id)
    with pytest.raises(ConflictError):
        services.update_decision(session, actor, project.id, decision.id, statement="changed")
    with pytest.raises(ConflictError):
        services.commit_decision(session, actor, project.id, decision.id)


def test_supersede_links_a_new_decision(session):
    actor, project = _project(session)
    first = services.create_decision(session, actor, project.id, title="A", statement="first")
    services.commit_decision(session, actor, project.id, first.id)
    second = services.create_decision(session, actor, project.id, title="B", statement="second")
    # A draft cannot supersede: the #9 edge materializes only at commit (ADR-0011).
    with pytest.raises(ConflictError):
        services.supersede_decision(session, actor, project.id, second.id, first.id)
    services.commit_decision(session, actor, project.id, second.id)
    services.supersede_decision(session, actor, project.id, second.id, first.id)
    # Superseded is derived via the ProjectKnowledgeEdge, not a stored status.
    from revolab.queries import decision_summary

    first_summary = decision_summary(session, first)
    assert first_summary["superseded"] is True
    assert first_summary["superseded_by"] == str(second.id)


def test_evidence_revision_target_must_actually_be_a_revision(session):
    actor, project = _project(session)
    series = _object(session, actor, project, "protein", "X", {})
    # The series is visible but is the wrong kind for a revision target.
    with pytest.raises(ValidationError):
        services.create_evidence(
            session,
            actor,
            project.id,
            kind="computation",
            source_kind=None,
            source_id=None,
            target_kind="scientific_object_revision",
            target_id=series,
        )


def test_supersession_rejects_direct_and_transitive_cycles(session):
    actor, project = _project(session)
    a = services.create_decision(session, actor, project.id, title="A", statement="a")
    b = services.create_decision(session, actor, project.id, title="B", statement="b")
    c = services.create_decision(session, actor, project.id, title="C", statement="c")
    for decision in (a, b, c):
        services.commit_decision(session, actor, project.id, decision.id)

    # A -> B (direct cycle: B -> A must then fail)
    services.supersede_decision(session, actor, project.id, a.id, b.id)
    with pytest.raises(ConflictError):
        services.supersede_decision(session, actor, project.id, b.id, a.id)

    # Build A -> B -> C transitive chain, then C -> A must fail.
    services.supersede_decision(session, actor, project.id, b.id, c.id)
    with pytest.raises(ConflictError):
        services.supersede_decision(session, actor, project.id, c.id, a.id)


def test_supersession_respects_in_out_uniqueness(session):
    actor, project = _project(session)
    a = services.create_decision(session, actor, project.id, title="A", statement="a")
    b = services.create_decision(session, actor, project.id, title="B", statement="b")
    c = services.create_decision(session, actor, project.id, title="C", statement="c")
    for decision in (a, b, c):
        services.commit_decision(session, actor, project.id, decision.id)
    services.supersede_decision(session, actor, project.id, a.id, b.id)
    # A already supersedes B (out-going); A cannot supersede C too.
    with pytest.raises(ConflictError):
        services.supersede_decision(session, actor, project.id, a.id, c.id)
    # B is already superseded by A; C cannot also supersede B.
    with pytest.raises(ConflictError):
        services.supersede_decision(session, actor, project.id, c.id, b.id)


def test_ghost_knowledge_rejected_on_evidence_write(session):
    actor, project = _project(session)
    series = _object(session, actor, project, "structure", "S", {"method": "X"})
    # The revision is visible (created in-project)...
    revision = services.append_revision(session, actor, project.id, series, {"method": "Y"})
    other_project = services.create_project(session, actor, "P2")
    other = _object(session, actor, other_project, "structure", "O", {})
    other_revision = services.append_revision(session, actor, other_project.id, other, {})
    # ...but the target revision is NOT visible in `project`, so writing an
    # Evidence in `project` about it is ghost knowledge and must be rejected.
    with pytest.raises(AuthorizationError):
        services.create_evidence(
            session, actor, project.id, kind="computation",
            source_kind="scientific_object_revision", source_id=revision.revision_id,
            target_kind="scientific_object_revision", target_id=other_revision.revision_id,
        )


def test_ghost_knowledge_rejected_on_decision_select(session):
    actor, project = _project(session)
    other_project = services.create_project(session, actor, "P2")
    other = _object(session, actor, other_project, "protein", "O", {})
    with pytest.raises(AuthorizationError):
        services.create_decision(
            session, actor, project.id, title="D", statement="S",
            selects=[{"target_id": other, "target_kind": "scientific_object_series"}],
        )
