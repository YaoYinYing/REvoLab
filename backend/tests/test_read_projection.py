"""Project read projection: global identity is never global readability.

A shared global resource does not leak another Project's Evidence, Decision, or
knowledge-edge topology through object-detail or the bounded graph query."""

import pytest

from revolab import queries, services
from revolab.domain.errors import NotFoundError


def _shared(session):
    actor = services.create_actor(session)
    a = services.create_project(session, actor, "A")
    b = services.create_project(session, actor, "B")
    series = services.create_object(session, actor, a.id, "protein", "X", payload={})
    revision = services.append_revision(session, actor, a.id, series, {"chain": "A"})
    # B gains the read lens over the series only (no revisions exposed).
    services.link_series(session, actor, b.id, series)
    evidence = services.create_evidence(
        session, actor, a.id, kind="computation", polarity="supports",
        source_kind="scientific_object_revision", source_id=revision.revision_id,
        target_kind="scientific_object_revision", target_id=revision.revision_id,
    )
    decision = services.create_decision(
        session, actor, a.id, title="A-only", statement="A conclusion",
        cites=[{"evidence_id": evidence.id, "cited_as": "supports"}],
        selects=[{"target_id": series, "target_kind": "scientific_object_series"}],
    )
    services.commit_decision(session, actor, a.id, decision.id)
    return actor, a, b, series, evidence, decision


def test_shared_series_does_not_leak_other_project_knowledge(session):
    _actor, a, b, series, evidence, decision = _shared(session)

    detail_a = queries.object_detail(session, a.id, series)
    assert [d["id"] for d in detail_a["decisions"]] == [str(decision.id)]
    assert [e["id"] for e in detail_a["evidence"]] == [str(evidence.id)]

    # Project B sees the shared series but none of A's knowledge.
    detail_b = queries.object_detail(session, b.id, series)
    assert detail_b["decisions"] == []
    assert detail_b["evidence"] == []


def test_bounded_graph_scopes_decision_edges_to_owning_project(session):
    _actor, a, b, series, _evidence, _decision = _shared(session)

    graph_a = queries.bounded_graph(session, a.id, series, depth=3)
    assert any(e["kind"] == "decision" for e in graph_a["edges"])

    graph_b = queries.bounded_graph(session, b.id, series, depth=3)
    assert not any(e["kind"] == "decision" for e in graph_b["edges"])


def test_graph_start_must_be_visible(session):
    _actor, _a, b, _series, _evidence, _decision = _shared(session)
    unknown = services.create_object(session, _actor, _a.id, "ligand", "U", payload={"smiles": "CCO"})
    # `unknown` is visible in A but not in B.
    with pytest.raises(NotFoundError):
        queries.bounded_graph(session, b.id, unknown, depth=2)
