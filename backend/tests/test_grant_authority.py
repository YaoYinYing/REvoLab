"""MutationGrant is real, not ceremonial: leaf domain mutations must consume and
re-validate the pre-issued grant, and direct calls cannot bypass the authority
contract."""

import pytest

from revolab import services
from revolab.domain import persistence, provenance, scientific_object
from revolab.domain.errors import AuthorizationError, ValidationError
from revolab.domain.grants import MutationGrant
from revolab.enums import RelationType, ResourceKind
from revolab.models import ResourceStewardship


def _setup(session):
    actor = services.create_actor(session)
    project = services.create_project(session, actor, "P")
    series = services.create_object(session, actor, project.id, "protein", "X", payload={})
    return actor, project, series


def _grant(resource_id, project_id, actor_id, purpose):
    return MutationGrant(
        resource_id=resource_id,
        steward_project_id=project_id,
        actor_id=actor_id,
        role="owner",
        purpose=purpose,
    )


def test_leaf_mutations_require_a_covering_grant(session):
    actor, project, series = _setup(session)
    other = services.create_object(session, actor, project.id, "ligand", "L", payload={"smiles": "CCO"})
    wrong = _grant(other, project.id, actor, "append revision")
    with pytest.raises(AuthorizationError):
        scientific_object.append_revision(session, wrong, series, {"organism": None})
    with pytest.raises(AuthorizationError):
        scientific_object.update_series(session, wrong, series, name="nope", description=None)
    with pytest.raises(AuthorizationError):
        scientific_object.mark_archived(session, wrong, series)


def test_leaf_mutations_reject_a_stale_grant(session):
    actor, project, series = _setup(session)
    grant = _grant(series, project.id, actor, "append revision")
    stewardship = session.get(ResourceStewardship, series)
    stewardship.steward_project_id = None
    session.commit()
    with pytest.raises(AuthorizationError):
        scientific_object.append_revision(session, grant, series, {"organism": None})


def test_conceptual_edge_leaf_requires_source_grant(session):
    actor, project, source = _setup(session)
    target = services.create_object(session, actor, project.id, "protein", "Y", payload={})
    other = services.create_object(session, actor, project.id, "ligand", "L", payload={"smiles": "CCO"})
    wrong = _grant(other, project.id, actor, "variant_of")
    with pytest.raises(AuthorizationError):
        provenance.add_conceptual_edge(session, wrong, RelationType.VARIANT_OF, source, target)


def test_consumed_input_leaf_validates_source_series_grant(session):
    actor, project, series = _setup(session)
    revision = services.append_revision(session, actor, project.id, series, {"organism": None})
    run = services.create_run_reference(session, actor, project.id, "revocompute", "run-1")
    wrong = _grant(run.run_id, project.id, actor, "consumed_as_input_by")
    with pytest.raises(AuthorizationError):
        provenance.add_consumed_input(session, wrong, project.id, revision.revision_id, run.run_id)


def test_insert_edge_sink_cross_checks_registry_kind(session):
    actor, project, series = _setup(session)
    revision = services.append_revision(session, actor, project.id, series, {"organism": None})
    with pytest.raises(ValidationError):
        persistence.insert_edge(
            session,
            actor,
            RelationType.DERIVED_FROM,
            revision.revision_id,
            ResourceKind.SCIENTIFIC_OBJECT_SERIES,  # asserted kind disagrees with registry
            revision.revision_id,
            ResourceKind.SCIENTIFIC_OBJECT_SERIES,
        )
