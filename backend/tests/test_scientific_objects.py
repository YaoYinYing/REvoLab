"""ScientificObject Series/Revision identity, immutability, typed payload,
atomic creation, revision visibility closure."""

import pytest
from sqlalchemy import select

from revolab import services
from revolab.domain.errors import AuthorizationError, ConflictError, ValidationError
from revolab.models import (
    GlobalResourceRegistry,
    ProjectResourceLink,
    ScientificObjectRevision,
    ScientificObjectSeries,
)


def _project(session):
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "P")
    return actor, project


def test_series_and_revision_have_distinct_uuid_identities(session):
    actor, project = _project(session)
    series_id = services.create_object(session, actor, project.id, "protein", "X", payload={})
    series = session.get(ScientificObjectSeries, series_id)
    revisions = session.scalars(
        select(ScientificObjectRevision).where(ScientificObjectRevision.series_id == series_id)
    ).all()
    assert series.series_id == series_id
    assert len(revisions) == 1
    assert revisions[0].revision_id != series_id
    # Both registered with correct kinds in the registry spine.
    assert session.get(GlobalResourceRegistry, series_id).resource_kind == "scientific_object_series"
    assert (
        session.get(GlobalResourceRegistry, revisions[0].revision_id).resource_kind
        == "scientific_object_revision"
    )


def test_revision_seq_monotonic_and_immutable(session):
    actor, project = _project(session)
    series_id = services.create_object(session, actor, project.id, "protein", "X", payload={"chain": "A"})
    r2 = services.append_revision(session, actor, project.id, series_id, {"chain": "B"})
    r3 = services.append_revision(session, actor, project.id, series_id, {"chain": "B"})
    assert r2.revision_seq == 2
    assert r3.revision_seq == 3
    assert r2.revision_id != r3.revision_id


def test_latest_revision_is_derived_not_stored(session):
    actor, project = _project(session)
    series_id = services.create_object(session, actor, project.id, "protein", "X", payload={"chain": "A"})
    services.append_revision(session, actor, project.id, series_id, {"chain": "B"})
    series = session.get(ScientificObjectSeries, series_id)
    assert not hasattr(series, "current_revision_id")
    latest = session.scalar(
        select(ScientificObjectRevision)
        .where(ScientificObjectRevision.series_id == series_id)
        .order_by(ScientificObjectRevision.revision_seq.desc())
        .limit(1)
    )
    assert latest.revision_seq == 2


def test_typed_payload_validated_against_per_type_schema(session):
    actor, project = _project(session)
    with pytest.raises(ValidationError):
        services.create_object(
            session, actor, project.id, "protein", "X",
            payload={"not_a_field": True},
        )
    with pytest.raises(ValidationError):
        services.create_object(session, actor, project.id, "ligand", "L", payload={})  # smiles required

    series_id = services.create_object(
        session, actor, project.id, "ligand", "L", payload={"smiles": "CC(=O)O"}
    )
    revision = session.scalar(
        select(ScientificObjectRevision).where(ScientificObjectRevision.series_id == series_id)
    )
    assert revision.payload == {"smiles": "CC(=O)O", "source": None}


def test_revision_visibility_requires_series_closure(session):
    actor, project = _project(session)
    series_id = services.create_object(session, actor, project.id, "protein", "X", payload={})
    revision = session.scalar(
        select(ScientificObjectRevision).where(ScientificObjectRevision.series_id == series_id)
    )
    # Revision link cannot be created in a fresh project without the series link.
    p2 = services.create_project(session, actor, "P2")
    with pytest.raises(ValidationError):
        services.link_series(session, actor, p2.id, revision.revision_id)


def test_append_revision_requires_stewardship(session):
    actor, project = _project(session)
    series_id = services.create_object(session, actor, project.id, "protein", "X", payload={})
    p2 = services.create_project(session, actor, "P2")
    services.link_series(session, actor, p2.id, series_id)
    with pytest.raises(AuthorizationError):
        services.append_revision(session, actor, p2.id, series_id, {"chain": "B"})


def test_series_rename_requires_stewardship(session):
    actor, project = _project(session)
    series_id = services.create_object(session, actor, project.id, "protein", "X", payload={})
    series = services.update_series(session, actor, project.id, series_id, name="Renamed")
    assert series.name == "Renamed"
    p2 = services.create_project(session, actor, "P2")
    services.link_series(session, actor, p2.id, series_id)
    with pytest.raises(AuthorizationError):
        services.update_series(session, actor, p2.id, series_id, name="Should not happen")


def test_archive_series_blocked_when_referenced(session):
    actor, project = _project(session)
    s1 = services.create_object(session, actor, project.id, "protein", "X", payload={})
    s2 = services.create_object(session, actor, project.id, "variant", "V", payload={})
    s3 = services.create_object(session, actor, project.id, "ligand", "L", payload={"smiles": "CCO"})
    services.add_variant_of(session, actor, project.id, s2, s1)
    # Both endpoints of a provenance edge are referenced and cannot be archived.
    with pytest.raises(ConflictError):
        services.archive_series(session, actor, project.id, s2)
    with pytest.raises(ConflictError):
        services.archive_series(session, actor, project.id, s1)
    # An unreferenced series archives cleanly.
    services.archive_series(session, actor, project.id, s3)
    assert session.get(ScientificObjectSeries, s3).archived_at is not None


def test_archive_blocked_when_referenced_by_evidence_or_decision(session):
    actor, project = _project(session)
    s1 = services.create_object(session, actor, project.id, "protein", "X", payload={})
    revision = services.append_revision(session, actor, project.id, s1, {"chain": "A"})
    # Cite it with Evidence, then archive must be blocked.
    services.create_evidence(
        session, actor, project.id, kind="computation", polarity="supports",
        source_kind="scientific_object_revision", source_id=revision.revision_id,
        target_kind="scientific_object_revision", target_id=revision.revision_id,
    )
    with pytest.raises(ConflictError):
        services.archive_series(session, actor, project.id, s1)


def test_archive_blocked_when_selected_by_committed_decision(session):
    actor, project = _project(session)
    s1 = services.create_object(session, actor, project.id, "protein", "X", payload={})
    decision = services.create_decision(
        session, actor, project.id, title="D", statement="S",
        selects=[{"target_id": s1, "target_kind": "scientific_object_series"}],
    )
    services.commit_decision(session, actor, project.id, decision.id)
    with pytest.raises(ConflictError):
        services.archive_series(session, actor, project.id, s1)


def test_linking_series_does_not_expose_sibling_revisions(session):
    actor, p1 = _project(session)
    series_id = services.create_object(session, actor, p1.id, "protein", "X", payload={"chain": "A"})
    services.append_revision(session, actor, p1.id, series_id, {"chain": "B"})

    p2 = services.create_project(session, actor, "P2")
    services.link_series(session, actor, p2.id, series_id)
    visible_links = session.scalars(
        select(ProjectResourceLink.resource_id).where(ProjectResourceLink.project_id == p2.id)
    ).all()
    # Only the series is linked in P2; no revision rows were auto-exposed.
    assert set(visible_links) == {series_id}
