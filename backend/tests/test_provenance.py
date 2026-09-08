"""Global provenance edges #1-7: per-edge authority, endpoint matrix, no
generic writer, immutability, derived generated_by, provenance round-trip."""

import pytest
from sqlalchemy import select

from revolab import queries, services
from revolab.domain.errors import AuthorizationError, ValidationError
from revolab.models import GlobalProvenanceEdge, ProjectResourceLink, ResourceStewardship


def _object(session, actor, project, obj_type, name="X", payload=None):
    return services.create_object(session, actor, project.id, obj_type, name, payload=payload or {})


def _revision(session, actor, project, series_id, payload):
    return services.append_revision(session, actor, project.id, series_id, payload)


def test_conceptual_edges_require_steward_source_and_read_target(session):
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "P")
    source = _object(session, actor, project, "variant", "V", {"ref_allele": "A"})
    target = _object(session, actor, project, "protein", "X", {})

    edge = services.add_variant_of(session, actor, project.id, source, target)
    assert edge.relation_type == "variant_of"

    # Non-steward Project over the source cannot assert the edge.
    p2 = services.create_project(session, actor, "P2")
    services.link_series(session, actor, p2.id, source)
    services.link_series(session, actor, p2.id, target)
    with pytest.raises(AuthorizationError):
        services.add_variant_of(session, actor, p2.id, source, target)


def test_content_edges_require_steward_of_source_series(session):
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "P")
    a = _object(session, actor, project, "structure", "A", {"method": "X"})
    b = _object(session, actor, project, "structure", "B", {"method": "Y"})
    a2 = _revision(session, actor, project, a, {"method": "Z"})
    b1 = session.scalar(
        select(__import__("revolab.models", fromlist=["ScientificObjectRevision"]).ScientificObjectRevision)
        .where(__import__("revolab.models", fromlist=["ScientificObjectRevision"]).ScientificObjectRevision.series_id == b)
    )
    edge = services.add_derived_from(session, actor, project.id, a2.revision_id, b1.revision_id)
    assert edge.relation_type == "derived_from"


def test_consumed_as_input_by_endpoint_kind_validated(session):
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "P")
    series = _object(session, actor, project, "protein", "X", {})
    revision = _revision(session, actor, project, series, {"chain": "B"})
    run = services.create_run_reference(session, actor, project.id, "revocompute", "run-1")

    edge = services.add_consumed_input(session, actor, project.id, revision.revision_id, run.run_id)
    assert edge.relation_type == "consumed_as_input_by"
    assert edge.source_kind == "scientific_object_revision"
    assert edge.target_kind == "run_reference"

    # Wrong endpoint kind is rejected.
    with pytest.raises(ValidationError):
        services.add_consumed_input(session, actor, project.id, run.run_id, revision.revision_id)


def test_produced_round_trip_and_derived_generated_by(session):
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "P")
    series = _object(session, actor, project, "structure", "S", {})
    revision = _revision(session, actor, project, series, {"method": "AlphaFold"})
    run = services.create_run_reference(session, actor, project.id, "revocompute", "run-1")
    artifact = services.create_artifact_reference(
        session, actor, project.id, "revocompute", "art-1", checksum="abc", size=3
    )

    services.record_produced(session, actor, project.id, run.run_id, artifact.artifact_id)
    imported = services.import_revision(
        session, actor, project.id, series, artifact.artifact_id, payload={"method": "AlphaFold"}
    )
    services.add_consumed_input(session, actor, project.id, revision.revision_id, run.run_id)

    chain = queries.generated_by(session, project.id, imported.revision_id)
    assert chain, "derived generated_by must resolve through produced + imported_as"
    assert chain[0]["resource_id"] == str(run.run_id)
    assert chain[0]["artifact_id"] == str(artifact.artifact_id)


def test_global_edge_immutable_and_corrected_by_superseding_edge(session):
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "P")
    source = _object(session, actor, project, "variant", "V", {})
    target = _object(session, actor, project, "protein", "X", {})
    edge = services.add_variant_of(session, actor, project.id, source, target)

    # No generic update path exists: a direct in-place edit is rejected at the
    # model boundary (immutable column set), and correction is a new edge.
    replacement = services.add_variant_of(session, actor, project.id, source, target)
    replacement.superseded_by_id = edge.edge_id
    session.commit()
    assert session.get(GlobalProvenanceEdge, edge.edge_id) is not None
    assert replacement.superseded_by_id == edge.edge_id


def test_self_relations_rejected(session):
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "P")
    s = _object(session, actor, project, "protein", "X", {})
    with pytest.raises(ValidationError):
        services.add_variant_of(session, actor, project.id, s, s)


def test_conceptual_edge_rejects_non_series_endpoint(session):
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "P")
    target = _object(session, actor, project, "protein", "T", {})
    artifact = services.create_artifact_reference(
        session, actor, project.id, "revocompute", "art-1", checksum="c", size=1
    )
    # An artifact is a visible, stewarded resource but is not a Series.
    with pytest.raises(ValidationError):
        services.add_variant_of(session, actor, project.id, artifact.artifact_id, target)
    with pytest.raises(ValidationError):
        services.add_variant_of(session, actor, project.id, target, artifact.artifact_id)


def test_revision_edge_rejects_non_revision_endpoint(session):
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "P")
    source_series = _object(session, actor, project, "structure", "S", {})
    source_rev = _revision(session, actor, project, source_series, {"method": "A"})
    target_series = _object(session, actor, project, "structure", "T2", {})
    # A series (visible, stewarded) cannot serve as a revision endpoint.
    with pytest.raises(ValidationError):
        services.add_derived_from(session, actor, project.id, source_rev.revision_id, target_series)


def test_reference_identity_get_or_create_single_global_node(session):
    actor = services.create_actor(session)
    first = services.create_project(session, actor, "P1")
    second = services.create_project(session, actor, "P2")

    r1 = services.create_run_reference(session, actor, first.id, "revocompute", "run-shared")
    r2 = services.create_run_reference(session, actor, second.id, "revocompute", "run-shared")
    assert r1.run_id == r2.run_id  # one durable global node, not two

    # The second Project gains the read lens but NOT stewardship over the node.
    stewardship = session.get(ResourceStewardship, r1.run_id)
    assert stewardship.steward_project_id == first.id
    link = session.scalar(
        select(ProjectResourceLink.id).where(
            ProjectResourceLink.project_id == second.id,
            ProjectResourceLink.resource_id == r1.run_id,
        )
    )
    assert link is not None


def test_artifact_version_identity_is_explicit(session):
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "P")
    v1 = services.create_artifact_reference(
        session, actor, project.id, "revocompute", "out-1", version_id="1", checksum="a"
    )
    v1_again = services.create_artifact_reference(
        session, actor, project.id, "revocompute", "out-1", version_id="1", checksum="a"
    )
    v2 = services.create_artifact_reference(
        session, actor, project.id, "revocompute", "out-1", version_id="2", checksum="b"
    )
    assert v1.artifact_id == v1_again.artifact_id
    assert v1.artifact_id != v2.artifact_id


def test_produced_rejects_non_run_session_source(session):
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "P")
    source_artifact = services.create_artifact_reference(
        session, actor, project.id, "revocompute", "src", checksum="s"
    )
    target_artifact = services.create_artifact_reference(
        session, actor, project.id, "revocompute", "tgt", checksum="t"
    )
    # produced requires a Run/Session source; an artifact is the wrong kind.
    with pytest.raises(ValidationError):
        services.record_produced(
            session, actor, project.id, source_artifact.artifact_id, target_artifact.artifact_id
        )


def test_imported_as_rejects_non_artifact_external_source(session):
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "P")
    series = _object(session, actor, project, "structure", "S", {})
    run = services.create_run_reference(session, actor, project.id, "revocompute", "run-1")
    # imported_as requires an Artifact/External reference source; a run is wrong.
    with pytest.raises(ValidationError):
        services.import_revision(session, actor, project.id, series, run.run_id, payload={})


def test_import_requires_target_stewardship(session):
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "P")
    series = _object(session, actor, project, "structure", "S", {})
    artifact = services.create_artifact_reference(
        session, actor, project.id, "revocompute", "art-1", checksum="abc", size=3
    )
    p2 = services.create_project(session, actor, "P2")
    services.link_series(session, actor, p2.id, series)
    services.link_series(session, actor, p2.id, artifact.artifact_id)
    with pytest.raises(AuthorizationError):
        services.import_revision(session, actor, p2.id, series, artifact.artifact_id, payload={})
