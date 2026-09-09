"""Phase-6 Agent Context & Tools: bounded context, ToolCatalog authority
projection, artifact inspection, and the proposal -> draft -> commit slice.

Safety regressions prove the Agent is a consumer, never an owner:
- no raw write path, no cross-Project context leak, no sibling-revision leak;
- no credential tool/credential material anywhere in the Agent surface;
- chatting never persists into the scientific graph;
- the Agent surface can only ever create a Decision DRAFT.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import ClassVar
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from revolab import services
from revolab.agent import (
    build_context,
    build_tool_catalog,
    inspect_artifact,
    propose_selection,
    record_proposal,
)
from revolab.agent.session import AgentSession
from revolab.content_store import ContentStore
from revolab.domain.errors import AuthorizationError
from revolab.drivers import DriverContext, DriverRegistry
from revolab.enums import (
    AgentToolAutonomy,
    CapabilityKind,
    DecisionStatus,
    ProviderRuntimeHealth,
    Role,
)
from revolab.models import (
    Decision,
    DecisionEvidence,
    DecisionTarget,
    Evidence,
    GlobalProvenanceEdge,
    ScientificObjectSeries,
)
from revolab.schemas import ContextSelectionCreate
from revolab.secret_store import InMemorySecretStore
from revolab.testing.fake_compute import FakeComputeDriver


def _actor(session):
    return services.create_actor(session)


def _project(session, actor, name="Agent P"):
    return services.create_project(session, actor, name)


def _object(session, actor, project, name="X", obj_type="protein", payload=None):
    return services.create_object(session, actor, project.id, obj_type, name, payload=payload or {})


def _revision(session, actor, project, series_id, payload):
    return services.append_revision(session, actor, project.id, series_id, payload)


def _evidence(session, actor, project, revision_id, target=None):
    target_id = target if target is not None else revision_id
    return services.create_evidence(
        session,
        actor,
        project.id,
        kind="computation",
        role="primary_support",
        interpretation="supports variant",
        polarity="supports",
        source_kind="scientific_object_revision",
        source_id=revision_id,
        target_kind="scientific_object_revision",
        target_id=target_id,
    )


def _registry(*drivers):
    registry = DriverRegistry()
    for driver in drivers:
        registry.register(driver)
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return registry


class _Capability:
    provider_key = "credprov"
    kind = CapabilityKind.COMPUTE


class _CredentialDriver:
    name = "credprov"
    display_name = "Credentialed Provider"
    description = "synthetic driver requiring a credential"
    required_credential_kinds = ("api_key",)
    authorities = ("credprov",)
    capabilities: ClassVar[dict[CapabilityKind, object]] = {CapabilityKind.COMPUTE: _Capability()}

    def start(self, context: DriverContext) -> None:
        pass

    def stop(self) -> None:
        pass

    def probe_health(self) -> ProviderRuntimeHealth:
        return ProviderRuntimeHealth.READY


# ---------------------------------------------------------------------------
# ContextBuilder authorization / visibility
# ---------------------------------------------------------------------------


def test_non_member_context_fails_closed(session):
    owner = _actor(session)
    outsider = _actor(session)
    project = _project(session, owner)
    registry = _registry()

    with pytest.raises(AuthorizationError):
        build_context(session, outsider, project.id, registry)


def test_viewer_reads_skeleton_and_cannot_record_draft(session):
    owner = _actor(session)
    viewer = services.create_actor(session)
    project = _project(session, owner)
    services.add_membership(session, owner, project.id, viewer, Role.VIEWER.value)
    series = _object(session, owner, project, "V1")
    registry = _registry()

    context = build_context(session, viewer, project.id, registry)
    assert context.membership_role is Role.VIEWER
    assert {ref.series_id for ref in context.series} == {series}

    with pytest.raises(AuthorizationError):
        services.create_decision(session, viewer, project.id, title="D", statement="S")

    catalog = build_tool_catalog(session, viewer, project.id, registry)
    decisions = {tool.id: tool for tool in catalog.tools}
    assert decisions["decision.record_draft"].available is False
    assert decisions["decision.commit"].available is False
    assert decisions["context.build"].available is True


def test_shared_revision_sibling_privacy_in_context(session):
    actor_a = _actor(session)
    actor_b = _actor(session)
    project_a = _project(session, actor_a, "A")
    project_b = _project(session, actor_b, "B")
    series = _object(session, actor_a, project_a, "Shared")
    revision_1 = _revision(session, actor_a, project_a, series, {"chain": "r1"})
    revision_2 = _revision(session, actor_a, project_a, series, {"chain": "r2"})

    services.add_membership(session, actor_b, project_b.id, actor_a, Role.MEMBER.value)
    services.share_resource(session, actor_a, project_b.id, revision_1.revision_id)

    context = build_context(
        session,
        actor_b,
        project_b.id,
        _registry(),
        ContextSelectionCreate(series_ids=[series]),
    )
    # Only the explicitly shared revision (and its series skeleton) is visible;
    # the initial revision and the sibling revision stay private.
    assert [ref.revision_seq for ref in context.revisions] == [revision_1.revision_seq]

    # Sibling revision is a different durability belief: not visible in B.
    with pytest.raises(AuthorizationError):
        build_context(
            session,
            actor_b,
            project_b.id,
            _registry(),
            ContextSelectionCreate(revision_ids=[revision_2.revision_id]),
        )


def test_tombstoned_project_context_fails_closed(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry()
    services.delete_project(session, actor, project.id)
    with pytest.raises(AuthorizationError):
        build_context(session, actor, project.id, registry)


def test_foreign_selection_rejected_without_existence_oracle(session):
    actor_a = _actor(session)
    actor_b = _actor(session)
    project_a = _project(session, actor_a, "A")
    project_b = _project(session, actor_b, "B")
    series_a = _object(session, actor_a, project_a, "A-only")
    registry = _registry()

    # Another Project's resource and an unknown UUID both fail closed as
    # AuthorizationError — never a 404 existence oracle.
    for bad_id in (series_a, uuid4()):
        with pytest.raises(AuthorizationError):
            build_context(session, actor_b, project_b.id, registry, ContextSelectionCreate(series_ids=[bad_id]))


def test_explicit_context_is_typed_and_bounded(session):
    actor = _actor(session)
    project = _project(session, actor)
    series = _object(session, actor, project, "T5alphaH", payload={"organism": "T"})
    revision = _revision(session, actor, project, series, {"chain": "A"})
    evidence = _evidence(session, actor, project, revision.revision_id)
    decision = services.create_decision(
        session,
        actor,
        project.id,
        title="Select",
        statement="Selected",
        cites=[{"evidence_id": evidence.id, "cited_as": "supports"}],
        selects=[{"target_id": revision.revision_id, "target_kind": "scientific_object_revision"}],
    )

    context = build_context(
        session,
        actor,
        project.id,
        _registry(),
        ContextSelectionCreate(series_ids=[series], graph_depth=0),
    )
    assert context.project_name == "Agent P"
    assert [ref.series_id for ref in context.series] == [series]
    # `_object` creates the initial revision; `_revision` appends a second. Both
    # are visible refs (identity + seq only, no payload).
    assert revision.revision_id in {ref.revision_id for ref in context.revisions}
    assert {ref.evidence_id for ref in context.evidence} == {evidence.id}
    assert {ref.decision_id for ref in context.decisions} == {decision.id}
    # Revisions are refs, not payload leaf content.
    assert not hasattr(context.revisions[0], "payload")
    # graph_depth=0 keeps relations out entirely.
    assert context.relations == []


def test_context_budget_truncates_implicit_series(session):
    actor = _actor(session)
    project = _project(session, actor)
    for index in range(3):
        _object(session, actor, project, f"O{index}")

    context = build_context(
        session,
        actor,
        project.id,
        _registry(),
        ContextSelectionCreate(max_series=2),
    )
    assert context.budget.series_count == 2
    assert context.budget.truncated is True


def test_context_budget_enforces_global_revision_cap(session):
    actor = _actor(session)
    project = _project(session, actor)
    series = _object(session, actor, project, "T5")
    for seq in range(3):
        _revision(session, actor, project, series, {"chain": f"A{seq + 2}"})

    context = build_context(
        session,
        actor,
        project.id,
        _registry(),
        ContextSelectionCreate(series_ids=[series], max_revisions=2),
    )
    assert context.budget.revision_count == 2
    assert context.budget.truncated is True


# ---------------------------------------------------------------------------
# Artifact inspection
# ---------------------------------------------------------------------------


def test_inspect_internal_artifact_returns_bounded_preview(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    content_store = ContentStore(tmp_path)
    artifact = services.create_internal_artifact(
        session, actor, project.id, content_store, b"ACGT" * 100, content_type="text/plain"
    )
    result = inspect_artifact(
        session,
        _registry(),
        InMemorySecretStore(),
        content_store,
        actor,
        project.id,
        artifact.artifact_id,
        preview_limit=8,
    )
    assert result.preview == "ACGTACGT"
    assert result.truncated is True
    assert result.binary is False
    assert result.authority == "revolab"
    # No secret material may ever surface in an inspection result.
    assert "secret" not in result.preview.lower()
    assert "api_key" not in result.preview.lower()


def test_inspect_artifact_requires_project_visibility(session, tmp_path):
    owner = _actor(session)
    outsider = _actor(session)
    project = _project(session, owner)
    content_store = ContentStore(tmp_path)
    artifact = services.create_internal_artifact(
        session, owner, project.id, content_store, b"bytes", content_type="text/plain"
    )
    with pytest.raises(AuthorizationError):
        inspect_artifact(
            session,
            _registry(),
            InMemorySecretStore(),
            content_store,
            outsider,
            project.id,
            artifact.artifact_id,
        )


# ---------------------------------------------------------------------------
# ToolCatalog projection
# ---------------------------------------------------------------------------


def _tool_ids(catalog) -> set[str]:
    return {tool.id for tool in catalog.tools}


def test_domain_tools_authority_matrix(session):
    owner = _actor(session)
    project = _project(session, owner)
    catalog = build_tool_catalog(session, owner, project.id, _registry())
    by_id = {tool.id: tool for tool in catalog.tools}

    assert by_id["context.build"].autonomy is AgentToolAutonomy.AUTOMATIC
    assert by_id["artifact.inspect"].autonomy is AgentToolAutonomy.AUTOMATIC
    assert by_id["evidence.create"].autonomy is AgentToolAutonomy.POLICY
    assert by_id["decision.record_draft"].autonomy is AgentToolAutonomy.POLICY
    assert by_id["decision.commit"].autonomy is AgentToolAutonomy.EXPLICIT_ACTION

    # Credentials, secret store, raw-write and sharing changes are NEVER tools.
    for tool_id in _tool_ids(catalog):
        assert "credential" not in tool_id
        assert "secret" not in tool_id
        assert "share" not in tool_id
        assert "membership" not in tool_id
        assert "sql" not in tool_id
        assert "http" not in tool_id
    assert "decision.commit" in _tool_ids(catalog)


def test_provider_tools_only_available_capabilities(session):
    actor = _actor(session)
    project = _project(session, actor)

    # A zero-credential, READY provider is projected as executable tools.
    available = build_tool_catalog(session, actor, project.id, _registry(FakeComputeDriver()))
    ids = _tool_ids(available)
    assert "fakecompute.compute.submit" in ids
    assert "fakecompute.compute.list_task_kinds" in ids
    assert "fakecompute.artifact.resolve" in ids

    # A credential-missing provider disappears from the executable catalog.
    missing = build_tool_catalog(session, actor, project.id, _registry(_CredentialDriver()))
    assert not any(tool.provider_key == "credprov" for tool in missing.tools)


# ---------------------------------------------------------------------------
# Proposal -> draft -> explicit authorized commit
# ---------------------------------------------------------------------------


def test_agent_proposal_is_draft_then_explicit_commit(session):
    actor = _actor(session)
    project = _project(session, actor)
    series = _object(session, actor, project, "T5alphaH")
    revision = _revision(session, actor, project, series, {"chain": "A"})
    _evidence(session, actor, project, revision.revision_id)

    registry = _registry()
    context = build_context(
        session,
        actor,
        project.id,
        registry,
        ContextSelectionCreate(series_ids=[series], graph_depth=0),
    )
    proposal = propose_selection(context)
    draft = record_proposal(session, actor, project.id, proposal)

    assert draft.status == DecisionStatus.DRAFT.value
    assert draft.committed_at is None
    assert session.scalars(select(DecisionEvidence)).all() == []
    assert session.scalars(select(DecisionTarget)).all() == []

    # A draft is visible as a draft, not as committed Knowledge truth.
    assert services.create_decision is not None
    documents = session.scalars(select(Decision)).all()
    assert [d.status for d in documents] == [DecisionStatus.DRAFT.value]

    committed = services.commit_decision(session, actor, project.id, draft.id)
    assert committed.status == DecisionStatus.COMMITTED.value
    assert committed.committed_at is not None
    assert len(session.scalars(select(DecisionEvidence)).all()) == 1
    assert len(session.scalars(select(DecisionTarget)).all()) == 1


def test_agent_session_messages_never_enter_scientific_graph(session):
    actor = _actor(session)
    project = _project(session, actor)
    series = _object(session, actor, project, "V")
    _revision(session, actor, project, series, {"chain": "A"})

    session_obj = AgentSession(actor_id=actor, project_id=project.id, messages=["hello", "propose X"])
    assert [message for message in session_obj.messages] == ["hello", "propose X"]

    proposal = propose_selection(
        build_context(session, actor, project.id, _registry(), ContextSelectionCreate(series_ids=[series]))
    )
    record_proposal(session, session_obj.actor_id, session_obj.project_id, proposal)

    # Chat never materializes as objects/evidence/provenance/decision truth: the
    # only durable row is the intentional Decision draft, and its text is never
    # the chat message.
    assert session.scalar(select(func.count()).select_from(ScientificObjectSeries)) == 1
    assert session.scalar(select(func.count()).select_from(Evidence)) == 0
    assert session.scalar(select(func.count()).select_from(GlobalProvenanceEdge)) == 0
    decisions = session.scalars(select(Decision)).all()
    assert len(decisions) == 1
    assert decisions[0].status == DecisionStatus.DRAFT.value
    assert "hello" not in decisions[0].statement


# ---------------------------------------------------------------------------
# HTTP slice
# ---------------------------------------------------------------------------


def test_agent_api_vertical_slice(client, tmp_path):
    actor_id = client.post("/api/actors").json()["actor_id"]
    headers = {"X-Actor-Id": actor_id}
    project = client.post("/api/projects", json={"name": "Agent API"}, headers=headers).json()
    pid = project["id"]

    created = client.post(
        f"/api/projects/{pid}/objects",
        json={"object_type": "protein", "name": "T5alphaH", "payload": {"organism": "T"}},
        headers=headers,
    )
    assert created.status_code == 201
    series_id = created.json()["series"]["series_id"]
    revision_id = created.json()["visible_revisions"][0]["revision_id"]

    evidence = client.post(
        f"/api/projects/{pid}/evidence",
        json={
            "kind": "computation",
            "polarity": "supports",
            "source_kind": "scientific_object_revision",
            "source_id": revision_id,
            "target_kind": "scientific_object_revision",
            "target_id": revision_id,
        },
        headers=headers,
    )
    assert evidence.status_code == 201
    evidence_id = evidence.json()["id"]

    context = client.post(
        f"/api/projects/{pid}/context",
        json={"series_ids": [series_id]},
        headers=headers,
    )
    assert context.status_code == 200
    assert context.json()["budget"]["series_count"] == 1

    tools = client.get(f"/api/projects/{pid}/agent/tools", headers=headers)
    assert tools.status_code == 200
    tool_ids = {tool["id"] for tool in tools.json()["tools"]}
    assert "decision.commit" in tool_ids

    proposal = client.post(
        f"/api/projects/{pid}/agent/proposals",
        json={
            "title": "Select variant",
            "statement": "This variant is the current experimental candidate.",
            "cites": [{"evidence_id": evidence_id, "cited_as": "supports"}],
            "selects": [{"target_id": series_id, "target_kind": "scientific_object_series"}],
        },
        headers=headers,
    )
    assert proposal.status_code == 201
    assert proposal.json()["status"] == "draft"
    decision_id = proposal.json()["id"]

    committed = client.post(
        f"/api/projects/{pid}/decisions/{decision_id}/commit", headers=headers
    )
    assert committed.status_code == 200
    assert committed.json()["status"] == "committed"
