"""PostgreSQL acceptance for the migrated schema (Phase 3-5 vertical slices:
credential/availability, REvoCompute, and collaboration/sharing).

Runs only when `REVOLAB_TEST_DATABASE_URL` points at a migrated PostgreSQL
database (CI runs `alembic upgrade head && alembic check` first, then this file).
The default SQLite in-memory fixtures in `conftest.py` are deliberately not used
here: PostgreSQL semantics are architecture truth (TODO.md section 10).
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from types import MappingProxyType
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint, Engine, create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from revolab import actions, services
from revolab.agent import AgentTurnRunner, build_context
from revolab.domain.provider import (
    build_credential_lease,
    capability_availability,
    credentials_present,
)
from revolab.drivers import Capability, DriverContext, DriverRegistry
from revolab.enums import (
    ActionRequestStatus,
    CapabilityAvailability,
    CapabilityKind,
    DecisionStatus,
    ProviderRuntimeHealth,
    Role,
)
from revolab.schemas import ContextSelectionCreate
from revolab.secret_store import InMemorySecretStore

DATABASE_URL = os.environ.get("REVOLAB_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not DATABASE_URL.startswith("postgresql"),
    reason="requires REVOLAB_TEST_DATABASE_URL pointing at a migrated PostgreSQL database",
)

SENTINEL = "REVOLAB_SENTINEL_0f9e2a7c4b6d8e1f"


class _Capability:
    def __init__(self, provider_key: str) -> None:
        self.provider_key = provider_key
        self.kind = CapabilityKind.COMPUTE


class _Driver:
    name = "fakeprov"
    display_name = "Fake Provider"
    description = "synthetic driver for PostgreSQL acceptance"
    required_credential_kinds = ("api_key",)
    authorities = ("fakeprov",)
    capabilities: Mapping[CapabilityKind, Capability] = {CapabilityKind.COMPUTE: _Capability("fakeprov")}

    def start(self, context: DriverContext) -> None:
        pass

    def stop(self) -> None:
        pass

    def probe_health(self) -> ProviderRuntimeHealth:
        return ProviderRuntimeHealth.READY


def _registry() -> DriverRegistry:
    registry = DriverRegistry()
    registry.register(_Driver())
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return registry


@pytest.fixture
def pg_engine() -> Iterator[Engine]:
    engine = create_engine(DATABASE_URL)  # type: ignore[arg-type]
    yield engine
    engine.dispose()


@pytest.fixture
def pg_session(pg_engine: Engine) -> Iterator[Session]:
    with Session(pg_engine) as session:
        yield session


def test_migrated_schema_has_phase3_binding_table(pg_session: Session) -> None:
    inspector = inspect(pg_session.get_bind())
    tables = set(inspector.get_table_names())
    assert "external_provider_credential_bindings" in tables

    columns = {column["name"] for column in inspector.get_columns(
        "external_provider_credential_bindings"
    )}
    assert {"id", "actor_id", "provider_key", "kind", "secret_ref", "created_at", "updated_at"} <= columns
    # No secret-material column may exist on the durable binding row.
    assert "secret" not in columns
    assert "token" not in columns
    assert "password" not in columns


def test_credential_availability_vertical_slice_on_postgres(pg_session: Session) -> None:
    store = InMemorySecretStore()
    registry = _registry()
    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, "PG Acceptance")

    availability = capability_availability(
        health=ProviderRuntimeHealth.READY,
        credentials_present=credentials_present(pg_session, actor, "fakeprov", ("api_key",)),
        permitted=services.project_policy_permits(pg_session, actor, project.id, CapabilityKind.COMPUTE),
    )
    assert availability is CapabilityAvailability.CREDENTIAL_MISSING

    binding = services.provision_credential(
        pg_session, store, registry, actor, "fakeprov", "api_key", SENTINEL
    )
    assert binding.secret_ref != SENTINEL
    assert SENTINEL not in binding.secret_ref

    lease = build_credential_lease(pg_session, store, actor, "fakeprov", ("api_key",))
    assert lease.get("api_key") == SENTINEL

    availability = capability_availability(
        health=ProviderRuntimeHealth.READY,
        credentials_present=credentials_present(pg_session, actor, "fakeprov", ("api_key",)),
        permitted=services.project_policy_permits(pg_session, actor, project.id, CapabilityKind.COMPUTE),
    )
    assert availability is CapabilityAvailability.AVAILABLE

    services.revoke_credential(pg_session, store, actor, "fakeprov", "api_key")
    availability = capability_availability(
        health=ProviderRuntimeHealth.READY,
        credentials_present=credentials_present(pg_session, actor, "fakeprov", ("api_key",)),
        permitted=services.project_policy_permits(pg_session, actor, project.id, CapabilityKind.COMPUTE),
    )
    assert availability is CapabilityAvailability.CREDENTIAL_MISSING


def test_phase4_compute_vertical_slice_on_postgres(pg_session: Session, tmp_path) -> None:
    """Phase-4 compute submit -> RunReference -> input provenance -> produced
    artifact, over the migrated PostgreSQL schema, with the provider-neutral
    in-process fake capability."""
    from revolab.capabilities import InputBinding
    from revolab.content_store import ContentStore
    from revolab.enums import RelationType, ResourceKind
    from revolab.models import GlobalProvenanceEdge, ScientificObjectRevision
    from revolab.testing.fake_compute import FakeComputeDriver

    registry = DriverRegistry()
    driver = FakeComputeDriver()
    registry.register(driver)
    driver_context = DriverContext(environment="test", settings=MappingProxyType({}))
    registry.start_all(driver_context)

    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, "PG Compute Acceptance")
    series_id = services.create_object(pg_session, actor, project.id, "sequence", "seq", payload={"sequence": "MEEP"})
    revision_id = pg_session.scalar(
        select(ScientificObjectRevision.revision_id)
        .where(ScientificObjectRevision.series_id == series_id)
        .order_by(ScientificObjectRevision.revision_seq)
        .limit(1)
    )

    submitted = services.compute_submit(
        pg_session,
        registry,
        InMemorySecretStore(),
        ContentStore(tmp_path),
        actor,
        project.id,
        "fakecompute",
        "echo",
        [InputBinding(kind=ResourceKind.SCIENTIFIC_OBJECT_REVISION, resource_id=revision_id)],
        {},
    )

    consumed = pg_session.scalar(
        select(GlobalProvenanceEdge).where(
            GlobalProvenanceEdge.relation_type == RelationType.CONSUMED_AS_INPUT_BY.value,
            GlobalProvenanceEdge.target_id == submitted["run_resource_id"],
        )
    )
    assert consumed is not None

    artifacts = services.compute_refresh_artifacts(
        pg_session,
        registry,
        InMemorySecretStore(),
        actor,
        project.id,
        "fakecompute",
        submitted["run_resource_id"],
        submitted["native_id"],
    )
    assert artifacts
    produced = pg_session.scalar(
        select(GlobalProvenanceEdge).where(
            GlobalProvenanceEdge.relation_type == RelationType.PRODUCED.value,
            GlobalProvenanceEdge.source_id == submitted["run_resource_id"],
        )
    )
    assert produced is not None


def test_phase5_collaboration_vertical_slice_on_postgres(pg_session: Session) -> None:
    """Phase-5 end-to-end collaboration scenario over PostgreSQL (TODO.md #12):
    one global resource, two Projects, two Actors, isolated interpretations, and
    Project deletion that preserves the shared resource and the other context."""
    from revolab import queries
    from revolab.domain.errors import AuthorizationError
    from revolab.enums import Role
    from revolab.models import (
        Decision,
        Evidence,
        ProjectResourceLink,
        ResourceStewardship,
        ScientificObjectRevision,
        ScientificObjectSeries,
    )

    actor_a = services.create_actor(pg_session)
    actor_b = services.create_actor(pg_session)
    project_a = services.create_project(pg_session, actor_a, "PG-A")
    project_b = services.create_project(pg_session, actor_b, "PG-B")
    services.add_membership(pg_session, actor_b, project_b.id, actor_a, Role.MEMBER.value)

    series = services.create_object(
        pg_session, actor_a, project_a.id, "protein", "P5X", payload={"organism": "collab"}
    )
    revision = pg_session.scalar(
        select(ScientificObjectRevision).where(
            ScientificObjectRevision.series_id == series,
            ScientificObjectRevision.revision_seq == 1,
        )
    )

    # Authorized cross-Project share of exactly one revision.
    services.share_resource(pg_session, actor_a, project_b.id, revision.revision_id)

    detail_b = queries.object_detail(pg_session, project_b.id, series)
    assert [rev["revision_seq"] for rev in detail_b["visible_revisions"]] == [1]
    assert detail_b["read_only"] is True

    # B receives read/context visibility only — mutation requires stewardship.
    with pytest.raises(AuthorizationError):
        services.append_revision(pg_session, actor_b, project_b.id, series, {"chain": "B"})

    # B forms its own interpretation; the write-time invariant keeps A-private
    # scientific endpoints out of B's project-scoped records.
    b_evidence = services.create_evidence(
        pg_session, actor_b, project_b.id, kind="computation", polarity="contradicts",
        source_kind="scientific_object_revision", source_id=revision.revision_id,
        target_kind="scientific_object_revision", target_id=revision.revision_id,
    )
    b_decision = services.create_decision(
        pg_session, actor_b, project_b.id, title="PG-B decision", statement="B truth",
        cites=[{"evidence_id": b_evidence.id, "cited_as": "supports"}],
        selects=[{"target_id": series, "target_kind": "scientific_object_series"}],
    )
    services.commit_decision(pg_session, actor_b, project_b.id, b_decision.id)

    # A sees none of B's interpretation.
    detail_a = queries.object_detail(pg_session, project_a.id, series)
    assert detail_a["evidence"] == []
    assert detail_a["decisions"] == []

    # Delete Project A: the global resource and B's context survive.
    services.delete_project(pg_session, actor_a, project_a.id)
    assert pg_session.get(ScientificObjectSeries, series) is not None
    assert pg_session.get(ScientificObjectRevision, revision.revision_id) is not None
    assert (
        pg_session.scalar(
            select(ProjectResourceLink.id).where(ProjectResourceLink.project_id == project_b.id)
        )
        is not None
    )
    assert pg_session.get(ResourceStewardship, series).steward_project_id is None
    assert pg_session.get(Evidence, b_evidence.id).archived_at is None
    assert pg_session.get(Decision, b_decision.id).archived_at is None
    detail_b_after = queries.object_detail(pg_session, project_b.id, series)
    assert [e["id"] for e in detail_b_after["evidence"]] == [str(b_evidence.id)]
    assert [d["id"] for d in detail_b_after["decisions"]] == [str(b_decision.id)]


def test_phase6_agent_vertical_slice_on_postgres(pg_session: Session, tmp_path) -> None:
    """Phase-6/8 PostgreSQL slice (TODO.md #16): Actor -> Project -> object/revision
    -> Evidence -> ContextSelection -> ContextBuilder -> bounded Agent loop ->
    Decision draft -> explicit authorized commit -> reloaded committed Knowledge."""
    from revolab.content_store import ContentStore
    from revolab.models import Decision, Evidence, ScientificObjectRevision
    from revolab.testing.fake_model import ScriptedModelBackend
    from revolab.tools.registry import build_default_registry
    from revolab.tools.runtime import LocalToolRuntime

    registry = DriverRegistry()

    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, "PG-Agent")
    series_id = services.create_object(
        pg_session, actor, project.id, "protein", "PG-Variant", payload={"organism": "PG"}
    )
    revision = pg_session.scalar(
        select(ScientificObjectRevision)
        .where(ScientificObjectRevision.series_id == series_id)
        .order_by(ScientificObjectRevision.revision_seq)
        .limit(1)
    )
    evidence = services.create_evidence(
        pg_session,
        actor,
        project.id,
        kind="computation",
        polarity="supports",
        source_kind="scientific_object_revision",
        source_id=revision.revision_id,
        target_kind="scientific_object_revision",
        target_id=revision.revision_id,
    )

    context = build_context(
        pg_session,
        actor,
        project.id,
        registry,
        ContextSelectionCreate(series_ids=[series_id], graph_depth=0),
    )
    assert context.budget.series_count == 1
    assert context.budget.revision_count == 1
    assert {ref.evidence_id for ref in context.evidence} == {evidence.id}

    model = ScriptedModelBackend(
        steps=[
            {
                "finish": "tool_calls",
                "tool_calls": [
                    {
                        "id": "call_draft",
                        "name": "decision.record_draft",
                        "arguments": {
                            "title": "Select variant",
                            "statement": "Select the variant for validation.",
                            "next_actions": ["validate experimentally"],
                            "cites": [
                                {
                                    "evidence_id": str(evidence.id),
                                    "cited_as": "supports",
                                }
                            ],
                            "selects": [
                                {
                                    "target_id": str(revision.revision_id),
                                    "target_kind": "scientific_object_revision",
                                }
                            ],
                        },
                    }
                ],
            },
            {"finish": "stop", "content": "Recorded a draft only."},
        ]
    )
    local_registry = build_default_registry()
    runner = AgentTurnRunner(
        model,
        LocalToolRuntime(local_registry),
        registry,
        InMemorySecretStore(),
        ContentStore(tmp_path),
        local_registry=local_registry,
    )
    result = runner.run(
        pg_session,
        actor,
        project.id,
        "Draft a conclusion.",
        ContextSelectionCreate(series_ids=[series_id], graph_depth=0),
    )
    assert result.termination_reason.value == "final_response"
    assert [entry.tool_id for entry in result.tool_trace] == ["decision.record_draft"]
    # Scope to the Decision THIS turn produced (the shared PG database also
    # holds decisions from earlier slices in this file).
    traced = result.tool_trace[0].result
    assert traced is not None and traced.resource_id is not None
    draft = pg_session.get(Decision, traced.resource_id)
    assert draft is not None
    assert draft.status == DecisionStatus.DRAFT.value
    assert draft.committed_at is None

    # Reload: the draft is in the decision log but is NOT committed knowledge.
    reloaded = pg_session.get(Decision, draft.id)
    assert reloaded.status == DecisionStatus.DRAFT.value
    assert pg_session.get(Evidence, evidence.id) is not None

    committed = services.commit_decision(pg_session, actor, project.id, draft.id)
    assert committed.status == DecisionStatus.COMMITTED.value
    assert committed.committed_at is not None

    # Final reload: the promotion gate persisted durable committed truth.
    final = pg_session.get(Decision, draft.id)
    assert final.status == DecisionStatus.COMMITTED.value


def test_phase7_tool_harness_vertical_slice_on_postgres(pg_session: Session, tmp_path) -> None:
    """Phase-7 PostgreSQL slice: internal CSV -> local table.select (persisted
    derived artifact) -> Evidence -> Decision draft, plus a fake REvoCompute
    tabular run whose artifact is analyzed by a local REvoLab Tool."""
    from revolab.content_store import ContentStore
    from revolab.models import ArtifactReference, ScientificObjectRevision, ToolInvocation
    from revolab.schemas import ToolInvocationCreate
    from revolab.secret_store import InMemorySecretStore
    from revolab.testing.fake_compute import FakeComputeDriver
    from revolab.tools.registry import build_default_registry
    from revolab.tools.runtime import LocalToolRuntime
    from revolab.tools.types import InvocationContext

    registry = DriverRegistry()
    registry.register(FakeComputeDriver())
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    content_store = ContentStore(tmp_path)
    secrets = InMemorySecretStore()

    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, "PG-Tools")
    runtime = LocalToolRuntime(build_default_registry())
    ctx = InvocationContext(
        session=pg_session,
        registry=registry,
        secret_store=secrets,
        content_store=content_store,
        actor_id=actor,
        project_id=project.id,
    )

    series_id = services.create_object(
        pg_session, actor, project.id, "protein", "PG-Tool-Target", payload={"organism": "PG"}
    )
    revision_id = pg_session.scalar(
        select(ScientificObjectRevision.revision_id)
        .where(ScientificObjectRevision.series_id == series_id)
        .order_by(ScientificObjectRevision.revision_seq)
        .limit(1)
    )

    artifact = services.create_internal_artifact(
        pg_session, actor, project.id, content_store, b"x,y\n1,2\n2,4\n", content_type="text/csv"
    )

    selected = runtime.invoke(
        ctx,
        ToolInvocationCreate(
            tool_id="table.select",
            input={"artifact_id": str(artifact.artifact_id), "columns": ["x", "y"], "limit": 10},
            persist=True,
        ),
    )
    assert selected.result_kind.value == "artifact"
    assert selected.persisted is True
    assert selected.resource_id is not None
    assert pg_session.get(ArtifactReference, selected.resource_id) is not None
    invocation = pg_session.scalar(select(ToolInvocation))
    assert invocation is not None
    assert invocation.result_resource_id == selected.resource_id

    evidence = services.create_evidence(
        pg_session,
        actor,
        project.id,
        kind="computation",
        polarity="supports",
        source_kind="artifact_reference",
        source_id=selected.resource_id,
        target_kind="scientific_object_revision",
        target_id=revision_id,
    )
    assert evidence.id is not None

    draft = services.create_decision(
        pg_session,
        actor,
        project.id,
        title="PG tools draft",
        statement="derived table valid",
        cites=[{"evidence_id": evidence.id, "cited_as": "supports"}],
        selects=[{"target_id": series_id, "target_kind": "scientific_object_series"}],
    )
    assert draft.status == DecisionStatus.DRAFT.value

    # Remote flow: fake REvoCompute tabular run -> ArtifactReference -> local tool.
    submitted = services.compute_submit(
        pg_session,
        registry,
        InMemorySecretStore(),
        content_store,
        actor,
        project.id,
        "fakecompute",
        "tabular",
        [],
        {"rows": 3},
    )
    artifacts = services.compute_refresh_artifacts(
        pg_session,
        registry,
        InMemorySecretStore(),
        actor,
        project.id,
        "fakecompute",
        submitted["run_resource_id"],
        submitted["native_id"],
    )
    external_artifact_id = artifacts[0]["resource_id"]
    described = runtime.invoke(
        ctx,
        ToolInvocationCreate(tool_id="table.describe", input={"artifact_id": str(external_artifact_id)}),
    )
    assert described.value is not None
    assert described.value["rows"] == 3


def _phase9_runner(pg_session: Session, actor, project, tmp_path, model) -> AgentTurnRunner:
    from revolab.content_store import ContentStore
    from revolab.tools.registry import build_default_registry
    from revolab.tools.runtime import LocalToolRuntime

    local_registry = build_default_registry()
    registry = DriverRegistry()
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return AgentTurnRunner(
        model,
        LocalToolRuntime(local_registry),
        registry,
        InMemorySecretStore(),
        ContentStore(tmp_path),
        local_registry=local_registry,
    )


def test_phase9_conversation_persistence_on_postgres(pg_session: Session, tmp_path) -> None:
    """TODO.md #14 PostgreSQL acceptance: conversation create, turn persistence,
    second-turn server-owned history, Actor/Project isolation, membership change,
    and Project tombstone all work on the migrated schema."""
    from revolab.agent.conversations import (
        create_conversation,
        get_conversation,
        list_conversations,
        run_conversation_turn,
    )
    from revolab.domain.errors import AuthorizationError, NotFoundError
    from revolab.enums import ConversationRole, Role
    from revolab.models import ConversationMessage, ProjectConversation
    from revolab.testing.fake_model import ScriptedModelBackend

    actor_a = services.create_actor(pg_session)
    actor_b = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor_a, "PG Conversations")
    services.add_membership(pg_session, actor_a, project.id, actor_b, Role.MEMBER.value)

    conversation = create_conversation(pg_session, actor_a, project.id, title="PG slice")
    assert pg_session.get(ProjectConversation, conversation.id) is not None

    first_model = ScriptedModelBackend(steps=[{"finish": "stop", "content": "first response overwrite"}])
    run_conversation_turn(
        pg_session,
        actor_a,
        project.id,
        conversation.id,
        _phase9_runner(pg_session, actor_a, project, tmp_path, first_model),
        message="first message",
    )

    # Turn persistence + server-owned second-turn history over PostgreSQL.
    second_model = ScriptedModelBackend(steps=[{"finish": "stop", "content": "second response"}])
    result = run_conversation_turn(
        pg_session,
        actor_a,
        project.id,
        conversation.id,
        _phase9_runner(pg_session, actor_a, project, tmp_path, second_model),
        message="second message",
    )
    assert result.turn.budget.history_messages == 2
    assert any(message.content == "first message" for message in second_model.requests[0].messages)
    assert any(message.content == "first response overwrite" for message in second_model.requests[0].messages)

    detail = get_conversation(pg_session, actor_a, project.id, conversation.id)
    assert [message.role for message in detail.messages] == [
        ConversationRole.USER,
        ConversationRole.ASSISTANT,
        ConversationRole.USER,
        ConversationRole.ASSISTANT,
    ]
    assert detail.total_messages == 4
    assert pg_session.scalar(
        select(ConversationMessage.role).where(
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.seq == 1,
        )
    ) == ConversationRole.USER.value

    # Actor/Project isolation: another member cannot read a private conversation.
    assert list_conversations(pg_session, actor_b, project.id) == []
    with pytest.raises(NotFoundError):
        get_conversation(pg_session, actor_b, project.id, conversation.id)

    # Membership revocation takes effect immediately, even for a persisted row.
    own = create_conversation(pg_session, actor_b, project.id, title="B private")
    assert get_conversation(pg_session, actor_b, project.id, own.id).id == own.id
    services.remove_membership(pg_session, actor_a, project.id, actor_b)
    with pytest.raises(AuthorizationError):
        get_conversation(pg_session, actor_b, project.id, own.id)

    # Project tombstone blocks all conversation access for the owner too.
    services.delete_project(pg_session, actor_a, project.id)
    with pytest.raises(AuthorizationError):
        get_conversation(pg_session, actor_a, project.id, conversation.id)


def test_phase9_role_check_enforced_on_postgres(pg_session: Session) -> None:
    """The Phase-9 durable-message enums are DB CHECK constrained on PostgreSQL:
    raw (ORM-bypassed) invalid role/termination_reason values must be rejected
    by the database itself."""
    from uuid import uuid4

    from revolab.agent.conversations import create_conversation

    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, "PG Conv Check")
    conversation = create_conversation(pg_session, actor, project.id)

    with pytest.raises(IntegrityError):
        pg_session.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, conversation_id, seq, role, content) "
                "VALUES (CAST(:id AS UUID), CAST(:conversation_id AS UUID), :seq, :role, :content)"
            ),
            {
                "id": str(uuid4()),
                "conversation_id": str(conversation.id),
                "seq": 1,
                "role": "bogus",
                "content": "malformed role",
            },
        )
    pg_session.rollback()

    with pytest.raises(IntegrityError):
        pg_session.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, conversation_id, seq, role, content, termination_reason) "
                "VALUES (CAST(:id AS UUID), CAST(:conversation_id AS UUID), :seq, :role, :content, :reason)"
            ),
            {
                "id": str(uuid4()),
                "conversation_id": str(conversation.id),
                "seq": 2,
                "role": "assistant",
                "content": "malformed reason",
                "reason": "bogus_reason",
            },
        )
    pg_session.rollback()


def test_phase9_concurrent_turns_serialize_on_postgres(pg_session: Session, tmp_path) -> None:
    """TODO.md #16 / reviewer race-find: two concurrent turns on ONE conversation
    serialize their load->run->persist section. The first turn executes a POLICY
    truth tool (decision.record_draft) mid-turn — the exact path that used to
    commit and release the row lock early — and the second turn's model must not
    run until the first turn fully commits."""
    import threading
    import time

    from sqlalchemy.orm import Session as ORMSession

    from revolab.agent.conversations import create_conversation, run_conversation_turn
    from revolab.agent.model_backend import ModelResponse, ModelToolCall

    class BlockingTruthModel:
        def __init__(self, label: str) -> None:
            self.label = label
            self.requests = []
            self.started = threading.Event()
            self.release = threading.Event()

        def complete(self, request):
            self.requests.append(request)
            if self.label == "A" and len(self.requests) == 1:
                return ModelResponse(
                    finish="tool_calls",
                    tool_calls=(
                        ModelToolCall(
                            id="call_draft",
                            name="decision.record_draft",
                            arguments_raw='{"title": "Serialized draft", "statement": "draft"}',
                            arguments={"title": "Serialized draft", "statement": "draft"},
                        ),
                    ),
                )
            if self.label == "A" and len(self.requests) == 2:
                self.started.set()
                self.release.wait(timeout=15)
            return ModelResponse(finish="stop", content=f"{self.label}-response")

    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, "PG Conv Race")
    conversation = create_conversation(pg_session, actor, project.id)
    engine = pg_session.get_bind()

    model_a = BlockingTruthModel("A")
    model_b = BlockingTruthModel("B")
    results: dict[str, object] = {}

    def worker(label: str, model: BlockingTruthModel, message: str) -> None:
        with ORMSession(bind=engine) as session:
            results[label] = run_conversation_turn(
                session,
                actor,
                project.id,
                conversation.id,
                _phase9_runner(session, actor, project, tmp_path, model),
                message=message,
            )

    thread_a = threading.Thread(target=worker, args=("A", model_a, "A-message"))
    thread_a.start()
    # A has executed decision.record_draft and is blocking in its second model
    # call. After the fix its truth-tool writes are uncommitted, so A still holds
    # the conversation row lock (no mid-turn commit released it).
    assert model_a.started.wait(timeout=10)

    thread_b = threading.Thread(target=worker, args=("B", model_b, "B-message"))
    thread_b.start()
    time.sleep(0.75)
    # B is still waiting on the conversation row lock; it has not reached the model.
    assert len(model_b.requests) == 0

    model_a.release.set()
    thread_a.join(timeout=20)
    thread_b.join(timeout=20)
    assert not thread_a.is_alive()
    assert not thread_b.is_alive()

    # The second turn's model saw the first turn's persisted user message, proving
    # the record_draft-inside-turn path no longer breaks serialization.
    assert any(message.content == "A-message" for message in model_b.requests[0].messages)

    # The mid-turn truth tool REALLY executed (not silently refused): its trace
    # is COMPLETED and the Decision draft committed only when the turn committed.
    from revolab.enums import DecisionStatus
    from revolab.models import Decision

    turn_a = results["A"]
    trace_ids = [entry.tool_id for entry in turn_a.turn.tool_trace]
    assert trace_ids == ["decision.record_draft"]
    assert turn_a.turn.tool_trace[0].status.value == "completed"
    with ORMSession(bind=engine) as check:
        draft = check.scalar(
            select(Decision).where(Decision.project_id == project.id).order_by(Decision.created_at.desc()).limit(1)
        )
        assert draft is not None
        assert draft.status == DecisionStatus.DRAFT.value


def test_phase10_notes_schema_and_lifecycle_on_postgres(pg_session: Session) -> None:
    """Phase-10 Project Notebook acceptance on the migrated PostgreSQL schema:
    durable Note + immutable revisions + non-semantic mentions, project-shared
    authorization, membership/tombstone immediacy, and DB-level constraints."""
    from revolab.domain.errors import AuthorizationError, ConflictError, ValidationError
    from revolab.enums import Role
    from revolab.models import ProjectNote
    from revolab.notes import (
        append_revision,
        create_note,
        get_note,
        list_notes,
        list_revisions,
        patch_note,
    )
    from revolab.schemas import NoteMentionCreate

    inspector = inspect(pg_session.get_bind())
    tables = set(inspector.get_table_names())
    assert {"project_notes", "project_note_revisions", "note_mentions"} <= tables

    actor_a = services.create_actor(pg_session)
    actor_b = services.create_actor(pg_session)
    actor_viewer = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor_a, "PG Notes")
    services.add_membership(pg_session, actor_a, project.id, actor_b, Role.MEMBER.value)
    services.add_membership(pg_session, actor_a, project.id, actor_viewer, Role.VIEWER.value)

    series = services.create_object(
        pg_session, actor_a, project.id, "protein", "PG-Note-Target", payload={}
    )
    note = create_note(
        pg_session,
        actor_a,
        project.id,
        title="PG working note",
        body="initial body",
        mentions=[NoteMentionCreate(resource_id=series)],
    )
    assert pg_session.get(ProjectNote, note.id) is not None
    assert note.latest is not None and note.latest.revision_seq == 1
    assert note.latest.mentions[0].resolved is True

    # Project-shared read for every readable member (incl. viewer).
    assert get_note(pg_session, actor_b, project.id, note.id).title == "PG working note"
    assert get_note(pg_session, actor_viewer, project.id, note.id).id == note.id
    assert [row.id for row in list_notes(pg_session, actor_b, project.id)] == [note.id]

    # Member append + stale-base conflict (409), then a later append succeeds.
    appended = append_revision(
        pg_session, actor_b, project.id, note.id, base_revision_seq=1, body="member revision"
    )
    assert appended.revision_seq == 2
    with pytest.raises(ConflictError):
        append_revision(
            pg_session, actor_a, project.id, note.id, base_revision_seq=1, body="stale"
        )
    assert append_revision(
        pg_session, actor_a, project.id, note.id, base_revision_seq=2, body="third"
    ).revision_seq == 3
    assert [r.revision_seq for r in list_revisions(pg_session, actor_a, project.id, note.id)] == [1, 2, 3]

    # Viewer cannot mutate; a hidden-resource mention fails closed.
    with pytest.raises(AuthorizationError):
        append_revision(
            pg_session, actor_viewer, project.id, note.id, base_revision_seq=3, body="viewer"
        )
    other_actor = services.create_actor(pg_session)
    other_project = services.create_project(pg_session, other_actor, "PG Notes Other")
    hidden = services.create_object(
        pg_session, other_actor, other_project.id, "protein", "PG-Hidden", payload={}
    )
    with pytest.raises(AuthorizationError):
        create_note(
            pg_session,
            actor_a,
            project.id,
            title="bad",
            body="bad",
            mentions=[NoteMentionCreate(resource_id=hidden)],
        )
    # The rejected create left no ghost Note behind (atomic command).
    assert (
        pg_session.scalar(
            select(ProjectNote.id).where(
                ProjectNote.project_id == project.id, ProjectNote.title == "bad"
            )
        )
        is None
    )

    # Archiving is non-destructive but blocks new revisions.
    patch_note(pg_session, actor_a, project.id, note.id, archive=True)
    with pytest.raises(ValidationError):
        append_revision(
            pg_session, actor_a, project.id, note.id, base_revision_seq=3, body="after archive"
        )
    assert len(list_revisions(pg_session, actor_a, project.id, note.id)) == 3

    # Membership revocation, then Project tombstone, take effect immediately.
    services.remove_membership(pg_session, actor_a, project.id, actor_b)
    with pytest.raises(AuthorizationError):
        get_note(pg_session, actor_b, project.id, note.id)
    services.delete_project(pg_session, actor_a, project.id)
    with pytest.raises(AuthorizationError):
        get_note(pg_session, actor_a, project.id, note.id)


def test_phase10_note_constraints_enforced_by_postgres(pg_session: Session) -> None:
    """The Note model's DB constraints hold even when the ORM is bypassed: the
    (note_id, revision_seq) uniqueness and the exactly-one-mention-target check."""
    from uuid import uuid4

    from revolab.models import ProjectNoteRevision
    from revolab.notes import append_revision, create_note

    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, "PG Note Checks")
    note = create_note(pg_session, actor, project.id, title="C", body="v1")
    append_revision(pg_session, actor, project.id, note.id, base_revision_seq=1, body="v2")
    revision_id = pg_session.scalar(
        select(ProjectNoteRevision.revision_id).where(ProjectNoteRevision.note_id == note.id).limit(1)
    )
    # Valid FK targets, so the two-target case below can ONLY fail on the CHECK.
    valid_resource_id = services.create_object(
        pg_session, actor, project.id, "protein", "PG-Check-Target", payload={}
    )
    valid_decision = services.create_decision(
        pg_session, actor, project.id, title="PG check decision", statement="d"
    )

    # (note_id, revision_seq) is unique at the DB level.
    with pytest.raises(IntegrityError):
        pg_session.execute(
            text(
                "INSERT INTO project_note_revisions "
                "(revision_id, note_id, revision_seq, body, created_by_actor_id) "
                "VALUES (CAST(:rid AS UUID), CAST(:nid AS UUID), 1, 'dup', CAST(:aid AS UUID))"
            ),
            {"rid": str(uuid4()), "nid": str(note.id), "aid": str(actor)},
        )
    pg_session.rollback()

    # A mention with no target violates the exactly-one-target check.
    with pytest.raises(IntegrityError):
        pg_session.execute(
            text(
                "INSERT INTO note_mentions (id, revision_id, ordinal) "
                "VALUES (CAST(:id AS UUID), CAST(:rid AS UUID), 0)"
            ),
            {"id": str(uuid4()), "rid": str(revision_id)},
        )
    pg_session.rollback()

    # A mention with two VALID targets violates it too — and would otherwise
    # satisfy both foreign keys, so this assertion is mutation-sensitive.
    with pytest.raises(IntegrityError):
        pg_session.execute(
            text(
                "INSERT INTO note_mentions "
                "(id, revision_id, ordinal, target_resource_id, target_decision_id) "
                "VALUES (CAST(:id AS UUID), CAST(:rid AS UUID), 1, CAST(:x AS UUID), CAST(:y AS UUID))"
            ),
            {
                "id": str(uuid4()),
                "rid": str(revision_id),
                "x": str(valid_resource_id),
                "y": str(valid_decision.id),
            },
        )
    pg_session.rollback()


def test_phase10_concurrent_append_conflicts_on_postgres(pg_session: Session) -> None:
    """The required concurrent stale-write regression: two members appending from
    the SAME base revision must NOT both succeed. The note row lock orders them on
    PostgreSQL and the (note_id, revision_seq) uniqueness is the backstop."""
    import threading

    from sqlalchemy.orm import Session as ORMSession

    from revolab.domain.errors import ConflictError
    from revolab.models import ProjectNoteRevision
    from revolab.notes import append_revision, create_note

    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, "PG Note Race")
    note = create_note(pg_session, actor, project.id, title="Race", body="v1")
    engine = pg_session.get_bind()
    pg_session.rollback()

    barrier = threading.Barrier(2)
    outcomes: dict[str, object] = {}

    def worker(label: str) -> None:
        with ORMSession(bind=engine) as session:
            barrier.wait(timeout=10)
            try:
                outcomes[label] = append_revision(
                    session,
                    actor,
                    project.id,
                    note.id,
                    base_revision_seq=1,
                    body=f"{label} body",
                ).revision_seq
            except ConflictError as exc:
                outcomes[label] = exc

    threads = [threading.Thread(target=worker, args=(label,)) for label in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    succeeded = [value for value in outcomes.values() if isinstance(value, int)]
    conflicted = [value for value in outcomes.values() if isinstance(value, ConflictError)]
    assert succeeded == [2]
    assert len(conflicted) == 1

    with ORMSession(bind=engine) as check:
        seqs = list(
            check.scalars(
                select(ProjectNoteRevision.revision_seq)
                .where(ProjectNoteRevision.note_id == note.id)
                .order_by(ProjectNoteRevision.revision_seq)
            )
        )
        assert seqs == [1, 2]


def test_phase10_append_takes_note_row_lock_on_postgres(pg_session: Session) -> None:
    """BOTH mutators request the PostgreSQL row lock: removing `with_for_update`
    from `notes._mutable_note` (or dropping it from `patch_note`) makes this fail,
    so the ordering claim in notes.py/ADR-0016 is executable, not just asserted."""
    from sqlalchemy import event

    from revolab.notes import append_revision, create_note, patch_note

    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, "PG Note Lock")
    note = create_note(pg_session, actor, project.id, title="Lock", body="v1")
    engine = pg_session.get_bind()
    statements: list[str] = []

    def recorder(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    def recording(call):  # type: ignore[no-untyped-def]
        event.listen(engine, "before_cursor_execute", recorder)
        try:
            call()
        finally:
            event.remove(engine, "before_cursor_execute", recorder)

    recording(lambda: append_revision(pg_session, actor, project.id, note.id, base_revision_seq=1, body="v2"))
    append_statements = list(statements)
    statements.clear()
    recording(lambda: patch_note(pg_session, actor, project.id, note.id, title="Lock renamed"))
    patch_statements = list(statements)

    assert any("FOR UPDATE" in statement.upper() for statement in append_statements), append_statements
    assert any("FOR UPDATE" in statement.upper() for statement in patch_statements), patch_statements


# ---------------------------------------------------------------------------
# Phase 11 — durable explicit-action handoff (PostgreSQL acceptance truth)
# ---------------------------------------------------------------------------


class _Phase11State:
    def __init__(self) -> None:
        self.submit_calls = 0
        self.error = None
        # A per-state tag keeps native ids unique across runs against a shared,
        # non-truncated acceptance database.
        self.tag = uuid4().hex[:8]

    def native_id(self, ordinal: int) -> str:
        return f"native-{self.tag}-{ordinal}"


class _Phase11Compute:
    provider_key = "phase11prov"
    kind = CapabilityKind.COMPUTE

    def __init__(self, state: _Phase11State) -> None:
        self._state = state

    def list_task_kinds(self, credentials):  # type: ignore[no-untyped-def]
        from revolab.capabilities import TaskKindRef

        return [TaskKindRef(kind_id="echo", display_name="Echo")]

    def task_kind_schema(self, kind_id, credentials):  # type: ignore[no-untyped-def]
        from revolab.capabilities import InputSpec, TaskKindSchema

        return TaskKindSchema(
            kind_id=kind_id,
            display_name=kind_id,
            description=None,
            parameter_schema={},
            input_spec=InputSpec(required=False, multiple=True),
        )

    def submit(self, kind_id, inputs, params, credentials):  # type: ignore[no-untyped-def]
        from revolab.capabilities import RunHandle

        self._state.submit_calls += 1
        if self._state.error is not None:
            raise self._state.error
        return RunHandle(
            authority="phase11prov",
            native_id=self._state.native_id(self._state.submit_calls),
            task_type=kind_id,
        )

    def get_run(self, native_id, credentials):  # type: ignore[no-untyped-def]
        from revolab.capabilities import RunView

        return RunView(authority="phase11prov", native_id=native_id, status="finished")

    def list_artifacts(self, native_id, credentials):  # type: ignore[no-untyped-def]
        return []


class _Phase11Resolution:
    provider_key = "phase11prov"
    kind = CapabilityKind.ARTIFACT_RESOLUTION

    def resolve(self, artifact, credentials):  # type: ignore[no-untyped-def]
        from revolab.capabilities import ArtifactHandle

        return ArtifactHandle(authority=artifact.authority, native_id=artifact.native_id, data=b"")


class _Phase11Driver:
    name = "phase11prov"
    display_name = "Phase 11 Provider"
    description = "postgres acceptance provider"
    required_credential_kinds: tuple[str, ...] = ()
    authorities = ("phase11prov",)

    def __init__(self, state: _Phase11State) -> None:
        self.capabilities = {
            CapabilityKind.COMPUTE: _Phase11Compute(state),
            CapabilityKind.ARTIFACT_RESOLUTION: _Phase11Resolution(),
        }

    def start(self, context: DriverContext) -> None:
        pass

    def stop(self) -> None:
        pass

    def probe_health(self) -> ProviderRuntimeHealth:
        return ProviderRuntimeHealth.READY


def _phase11_registry(state: _Phase11State) -> DriverRegistry:
    registry = DriverRegistry()
    registry.register(_Phase11Driver(state))
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return registry


def _phase11_setup(pg_session: Session, state: _Phase11State, tmp_path):  # type: ignore[no-untyped-def]
    from revolab.content_store import ContentStore
    from revolab.enums import AgentToolAutonomy, ToolExecutionClass, ToolSideEffectClass
    from revolab.models import ScientificObjectRevision
    from revolab.schemas import ComputeSubmissionCreate
    from revolab.tools.registry import build_default_registry

    registry = _phase11_registry(state)
    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, "PG Phase11")
    series_id = services.create_object(pg_session, actor, project.id, "protein", "Target")
    revision_id = pg_session.scalar(
        select(ScientificObjectRevision.revision_id)
        .where(ScientificObjectRevision.series_id == series_id)
        .order_by(ScientificObjectRevision.revision_seq)
        .limit(1)
    )
    arguments = ComputeSubmissionCreate(
        provider_key="phase11prov",
        task_kind="echo",
        inputs=[{"kind": "scientific_object_revision", "resource_id": revision_id}],
        params={"message": "hello"},
    ).model_dump(mode="json")
    row = actions.propose_action_request(
        pg_session,
        actor_id=actor,
        project_id=project.id,
        conversation_id=None,
        tool_id="phase11prov.compute.submit",
        autonomy=AgentToolAutonomy.EXPLICIT_ACTION,
        execution_class=ToolExecutionClass.REMOTE,
        side_effect_class=ToolSideEffectClass.EXTERNAL_ACTION,
        arguments=arguments,
    )
    pg_session.commit()
    return {
        "actions": actions,
        "actor": actor,
        "project": project,
        "revision_id": revision_id,
        "action_id": row.id,
        "registry": registry,
        "state": state,
        "store": InMemorySecretStore(),
        "content_store": ContentStore(tmp_path),
        "local_registry": build_default_registry(),
    }


def _phase11_execution(pg_session: Session, ctx: dict):  # type: ignore[no-untyped-def]
    return ctx["actions"].ActionExecution(
        session=pg_session,
        registry=ctx["registry"],
        secret_store=ctx["store"],
        content_store=ctx["content_store"],
        local_registry=ctx["local_registry"],
    )


def test_phase11_schema_has_no_authority_or_secret_columns(pg_session: Session) -> None:
    inspector = inspect(pg_session.get_bind())
    assert "action_requests" in set(inspector.get_table_names())
    columns = {column["name"] for column in inspector.get_columns("action_requests")}
    assert {
        "id",
        "project_id",
        "actor_id",
        "conversation_id",
        "tool_id",
        "arguments",
        "status",
        "created_at",
    } <= columns
    # The migrated schema carries the durable state-machine invariant: a
    # `succeeded` action always names the canonical result it produced. Alembic's
    # `check` does not compare CHECK constraints, so the model/migration agreement
    # is asserted explicitly here (and in the SQLite raw-insert regression).
    from revolab.models import ActionRequest as _ActionRequestModel

    model_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in _ActionRequestModel.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_action_succeeded_has_result" in model_checks
    db_checks = {
        constraint["name"]: str(constraint.get("sqltext", ""))
        for constraint in inspector.get_check_constraints("action_requests")
    }
    assert "ck_action_succeeded_has_result" in db_checks
    migrated_sql = db_checks["ck_action_succeeded_has_result"].lower()
    for token in ("succeeded", "result_run_id", "result_decision_id", "is not null"):
        assert token in migrated_sql, migrated_sql
        assert token in model_checks["ck_action_succeeded_has_result"].lower()
    # Persist intent, never authority: no credential/authorization/health snapshot.
    assert not {
        "secret_ref",
        "credential",
        "membership_role",
        "authorized",
        "provider_health",
        "capability_availability",
        "provider_status",
        "run_status",
        "native_id",
    } & columns


def test_phase11_lifecycle_and_ambiguity_on_postgres(pg_session: Session, tmp_path) -> None:
    from revolab.capabilities import CapabilityError
    from revolab.enums import ActionRequestStatus, CapabilityErrorKind
    from revolab.models import RunReference

    state = _Phase11State()
    ctx = _phase11_setup(pg_session, state, tmp_path)
    actions_mod = ctx["actions"]

    executed = actions_mod.execute_action_request(
        _phase11_execution(pg_session, ctx), ctx["actor"], ctx["project"].id, ctx["action_id"]
    )
    assert executed.status == ActionRequestStatus.SUCCEEDED.value
    assert state.submit_calls == 1
    run = pg_session.get(RunReference, executed.result_run_id)
    assert run is not None and run.native_id == state.native_id(1)
    # Terminal: no silent second execution.
    with pytest.raises(Exception):
        actions_mod.execute_action_request(
            _phase11_execution(pg_session, ctx), ctx["actor"], ctx["project"].id, ctx["action_id"]
        )
    assert state.submit_calls == 1

    # Ambiguous external outcome: represented honestly, never auto-retried.
    state2 = _Phase11State()
    ctx2 = _phase11_setup(pg_session, state2, tmp_path)
    state2.error = CapabilityError(
        CapabilityErrorKind.NETWORK,
        "provider unreachable",
        provider_key="phase11prov",
        capability_kind=CapabilityKind.COMPUTE,
        retryable=True,
    )
    ambiguous = actions_mod.execute_action_request(
        _phase11_execution(pg_session, ctx2), ctx2["actor"], ctx2["project"].id, ctx2["action_id"]
    )
    assert ambiguous.status == ActionRequestStatus.AMBIGUOUS.value
    assert state2.submit_calls == 1


def test_phase11_post_claim_failure_settles_terminally_on_postgres(
    pg_session: Session, tmp_path, monkeypatch
) -> None:
    """A DB-level failure after the durable claim aborts the PostgreSQL transaction.
    The terminal write must still land (`_settle` rolls back and retries), so a
    confirmed external submission can never be stranded in `executing`.

    SQLite does not abort a transaction on a failed statement, so this path is only
    genuinely exercised here, on the acceptance substrate."""
    from sqlalchemy import text

    state = _Phase11State()
    ctx = _phase11_setup(pg_session, state, tmp_path)
    actions_mod = ctx["actions"]

    def explode(*args, **kwargs):  # type: ignore[no-untyped-def]
        pg_session.execute(text("SELECT * FROM table_that_does_not_exist_phase11"))

    monkeypatch.setattr(services, "record_compute_run", explode)

    executed = actions_mod.execute_action_request(
        _phase11_execution(pg_session, ctx), ctx["actor"], ctx["project"].id, ctx["action_id"]
    )
    assert executed.status == ActionRequestStatus.AMBIGUOUS.value
    assert executed.resolved_at is not None
    assert state.submit_calls == 1
    # Not stranded: a further execute fails closed instead of re-submitting.
    with pytest.raises(Exception):
        actions_mod.execute_action_request(
            _phase11_execution(pg_session, ctx), ctx["actor"], ctx["project"].id, ctx["action_id"]
        )
    assert state.submit_calls == 1


def test_phase11_actor_and_project_isolation_on_postgres(pg_session: Session, tmp_path) -> None:
    from revolab.domain.errors import NotFoundError

    state = _Phase11State()
    ctx = _phase11_setup(pg_session, state, tmp_path)
    actions_mod = ctx["actions"]
    other = services.create_actor(pg_session)
    services.add_membership(pg_session, ctx["actor"], ctx["project"].id, other, Role.MEMBER.value)

    with pytest.raises(NotFoundError):
        actions_mod.get_action_request(pg_session, other, ctx["project"].id, ctx["action_id"])
    with pytest.raises(NotFoundError):
        actions_mod.execute_action_request(
            _phase11_execution(pg_session, ctx), other, ctx["project"].id, ctx["action_id"]
        )
    assert state.submit_calls == 0


def test_phase11_concurrent_execute_claims_once_on_postgres(pg_engine: Engine, tmp_path) -> None:
    """The durable one-shot claim is the concurrency truth: two simultaneous human
    clicks produce at most ONE external submission and ONE canonical RunReference."""
    import threading

    from sqlalchemy.orm import Session as ORMSession

    from revolab.models import RunReference

    with ORMSession(pg_engine) as setup:
        state = _Phase11State()
        ctx = _phase11_setup(setup, state, tmp_path)
        action_id = ctx["action_id"]
        actor_id = ctx["actor"]
        project_id = ctx["project"].id
        registry = ctx["registry"]
        store = ctx["store"]
        content_store = ctx["content_store"]
        local_registry = ctx["local_registry"]

    # Force BOTH workers to be about to take the one-shot claim at the same
    # instant: this exercises the atomic conditional UPDATE itself (PostgreSQL
    # row-lock semantics under READ COMMITTED), not just the stale-`pending` guard.
    original_claim = actions._claim
    claim_barrier = threading.Barrier(2, timeout=30)

    def racing_claim(worker_session, action_request_id):  # type: ignore[no-untyped-def]
        claim_barrier.wait()
        return original_claim(worker_session, action_request_id)

    actions._claim = racing_claim  # type: ignore[assignment]
    outcomes: list[str] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            with ORMSession(pg_engine) as worker_session:
                result = actions.execute_action_request(
                    actions.ActionExecution(
                        session=worker_session,
                        registry=registry,
                        secret_store=store,
                        content_store=content_store,
                        local_registry=local_registry,
                    ),
                    actor_id,
                    project_id,
                    action_id,
                )
                with lock:
                    outcomes.append(result.status)
        except Exception as exc:
            with lock:
                errors.append(exc)

    first = threading.Thread(target=worker)
    second = threading.Thread(target=worker)
    try:
        first.start()
        second.start()
        first.join(timeout=45)
        second.join(timeout=45)
    finally:
        actions._claim = original_claim  # type: ignore[assignment]

    assert state.submit_calls == 1
    assert outcomes == [ActionRequestStatus.SUCCEEDED.value]
    assert len(errors) == 1
    with ORMSession(pg_engine) as check:
        assert (
            check.scalar(
                select(func.count())
                .select_from(RunReference)
                .where(RunReference.native_id == state.native_id(1))
            )
            == 1
        )
