"""PostgreSQL acceptance for the migrated schema (Phase 3-5 vertical slices:
credential/availability, REvoCompute, and collaboration/sharing).

Runs only when `REVOLAB_TEST_DATABASE_URL` points at a migrated PostgreSQL
database (CI runs `alembic upgrade head && alembic check` first, then this file).
The default SQLite in-memory fixtures in `conftest.py` are deliberately not used
here: PostgreSQL semantics are architecture truth (TODO.md section 10).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator, Mapping
from types import MappingProxyType
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint, Engine, create_engine, event, func, inspect, select, text
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


# ---------------------------------------------------------------------------
# Phase 12 — Project search acceptance on the migrated PostgreSQL schema:
# native lexical retrieval/ranking, authorization-aware corpus filters, the
# latest-Note-revision rule, private-conversation isolation, identifier search,
# and bounded top-N without application-side whole-Project materialization.
# ---------------------------------------------------------------------------


class _Phase12State:
    """One uniquely tagged Project with one searchable row of every corpus."""

    def __init__(self, session: Session, tag: str) -> None:
        from revolab.agent.conversations import create_conversation
        from revolab.models import (
            ConversationMessage,
            ProjectNote,
            ScientificObjectRevision,
        )
        from revolab.notes import append_revision as append_note_revision
        from revolab.notes import create_note

        self.tag = tag
        self.native_id = f"run-{tag}"
        self.external_id = f"P{tag.upper()}"
        self.actor = services.create_actor(session)
        self.project = services.create_project(session, self.actor, f"Search {tag}")
        self.series = services.create_object(
            session, self.actor, self.project.id, "protein", f"Kinase scaffold {tag}"
        )
        services.attach_external_identity(
            session, self.actor, self.project.id, self.series, "uniprot", self.external_id
        )
        revision = session.scalar(
            select(ScientificObjectRevision).where(
                ScientificObjectRevision.series_id == self.series
            )
        )
        self.evidence = services.create_evidence(
            session,
            self.actor,
            self.project.id,
            kind="experimental",
            label=f"Substrate positioning assay {tag}",
            interpretation="positions the substrate",
            target_kind="scientific_object_revision",
            target_id=revision.revision_id,
        )
        self.decision = services.create_decision(
            session,
            self.actor,
            self.project.id,
            title=f"Substrate positioning conclusion {tag}",
            statement="the conclusion statement",
        )
        create_note(
            session,
            self.actor,
            self.project.id,
            title=f"Working note {tag}",
            body=f"old {tag} hypothesis",
            mentions=[],
        )
        self.note = session.scalar(
            select(ProjectNote).where(ProjectNote.title == f"Working note {tag}")
        )
        append_note_revision(
            session,
            self.actor,
            self.project.id,
            self.note.id,
            base_revision_seq=1,
            body=f"latest {tag} conclusion",
            mentions=[],
        )
        self.run = services.create_run_reference(
            session,
            self.actor,
            self.project.id,
            "revocompute",
            self.native_id,
            task_type=f"fold-{tag}",
        )
        conversation = create_conversation(
            session, self.actor, self.project.id, f"Private planning {tag}"
        )
        session.add(
            ConversationMessage(
                conversation_id=conversation.id,
                seq=1,
                role="user",
                content=f"unique private phrase {tag}",
            )
        )
        session.commit()

    def search(self, session: Session, query: str, **kwargs):
        from revolab import search as search_service

        return search_service.search(session, self.actor, self.project.id, query=query, **kwargs)


def test_phase12_postgres_lexical_search_is_native_and_bounded(pg_session: Session) -> None:
    from revolab.enums import SearchScope, SearchTargetKind

    state = _Phase12State(pg_session, uuid4().hex[:8])
    statements: list[str] = []

    def _record(_conn, _cursor, statement, _params, _context, _many) -> None:  # type: ignore[no-untyped-def]
        statements.append(statement)

    engine = pg_session.get_bind()
    event.listen(engine, "before_cursor_execute", _record)
    try:
        result = state.search(pg_session, state.tag)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    kinds = {hit.target_kind for hit in result.hits}
    assert SearchTargetKind.SCIENTIFIC_OBJECT_SERIES in kinds
    assert SearchTargetKind.EVIDENCE in kinds
    assert SearchTargetKind.DECISION in kinds
    assert SearchTargetKind.NOTE in kinds
    assert SearchTargetKind.RUN_REFERENCE in kinds
    # PostgreSQL native text search is genuinely part of the emitted query (not a
    # Python-side rescoring), and every corpus query is bounded by SQL.
    joins = " \n".join(statements).lower()
    assert "to_tsvector" in joins
    assert "ts_rank" in joins
    assert "limit" in joins
    assert "project_resource_links" in joins
    # Wildcards/identifiers are bound parameters, never interpolated query text.
    assert state.tag not in joins
    # The private corpus is not part of the default Project-shared scope.
    assert all(hit.private is False for hit in result.hits)
    assert state.search(pg_session, "anything", scope=SearchScope.PROJECT_SHARED).scope is (
        SearchScope.PROJECT_SHARED
    )


def test_phase12_postgres_latest_revision_and_identifiers(pg_session: Session) -> None:
    state = _Phase12State(pg_session, uuid4().hex[:8])

    old = state.search(pg_session, f"old {state.tag} hypothesis")
    assert [hit.target_id for hit in old.hits if hit.target_kind.value == "note"] == []
    latest = state.search(pg_session, f"latest {state.tag} conclusion")
    assert [hit.target_id for hit in latest.hits if hit.target_kind.value == "note"] == [
        state.note.id
    ]
    by_identifier = state.search(pg_session, state.external_id)
    assert state.series in {hit.target_id for hit in by_identifier.hits}
    by_native_id = state.search(pg_session, state.native_id)
    assert state.run.run_id in {hit.target_id for hit in by_native_id.hits}


def test_phase12_postgres_private_conversation_isolation(pg_session: Session) -> None:
    from revolab import search as search_service
    from revolab.enums import SearchScope

    state = _Phase12State(pg_session, uuid4().hex[:8])
    other = services.create_actor(pg_session)
    services.add_membership(
        pg_session, state.actor, state.project.id, other, Role.MEMBER.value
    )

    mine = search_service.search(
        pg_session,
        state.actor,
        state.project.id,
        query=f"private phrase {state.tag}",
        scope=SearchScope.MY_CONVERSATIONS,
    )
    assert [hit.target_kind.value for hit in mine.hits] == ["conversation"]
    assert mine.hits[0].private is True

    theirs = search_service.search(
        pg_session,
        other,
        state.project.id,
        query=f"private phrase {state.tag}",
        scope=SearchScope.MY_CONVERSATIONS,
    )
    assert theirs.hits == []


def test_phase12_postgres_top_n_is_bounded_in_sql(pg_session: Session) -> None:
    tag = uuid4().hex[:8]
    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, f"Bounds {tag}")
    for index in range(25):
        services.create_decision(
            pg_session,
            actor,
            project.id,
            title=f"Bounded {tag} finding {index:02d}",
            statement="bounded search text",
        )

    statements: list[str] = []

    def _record(_conn, _cursor, statement, _params, _context, _many) -> None:  # type: ignore[no-untyped-def]
        statements.append(statement)

    engine = pg_session.get_bind()
    event.listen(engine, "before_cursor_execute", _record)
    try:
        from revolab import search as search_service

        result = search_service.search(
            pg_session, actor, project.id, query=f"bounded {tag}", limit=5
        )
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert len(result.hits) == 5
    assert result.truncated is True
    assert all(hit.snippet is not None and len(hit.snippet) <= 240 for hit in result.hits)
    # The LIMIT is pushed into each corpus query, so the whole Project is never
    # materialized in Python to compute a top-N.
    assert any("limit" in statement.lower() for statement in statements)


def test_phase12_postgres_cross_project_non_leakage(pg_session: Session) -> None:
    from revolab import search as search_service

    state = _Phase12State(pg_session, uuid4().hex[:8])
    other_actor = services.create_actor(pg_session)
    other_project = services.create_project(pg_session, other_actor, f"Other {state.tag}")

    for query in (str(state.series), state.external_id, state.native_id, f"Kinase scaffold {state.tag}"):
        leaked = search_service.search(
            pg_session, other_actor, other_project.id, query=query
        )
        assert leaked.hits == []


def test_phase12_postgres_unicode_case_folding(pg_session: Session) -> None:
    """PostgreSQL folds case per its locale, so an uppercase-accented query finds
    the row. (SQLite folds ASCII only — a documented substrate difference; the
    authorization/target-kind/bound contract is identical.)"""
    from revolab import search as search_service

    tag = uuid4().hex[:8]
    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, f"Unicode {tag}")
    series = services.create_object(pg_session, actor, project.id, "protein", f"Caf\u00e9 kinase {tag}")

    result = search_service.search(pg_session, actor, project.id, query=f"CAF\u00c9 KINASE {tag}")

    assert series in {hit.target_id for hit in result.hits}


# ---------------------------------------------------------------------------
# Phase 13 — external literature discovery & explicit import on PostgreSQL
#
# PostgreSQL is the final persistence truth: the global unique
# (authority, native_id) identity and the per-Project link uniqueness must hold
# under a genuine concurrent race, and no raw IntegrityError may reach the caller.
# ---------------------------------------------------------------------------


def _literature_registry():
    from revolab.testing.fake_literature import FakeLiteratureDriver

    registry = DriverRegistry()
    registry.register(FakeLiteratureDriver())
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return registry


def _seed_literature(registry, authority, native_id, title="Seeded publication"):
    """Register one exact candidate in the in-process fake provider's state."""
    from revolab.capabilities import LiteratureCandidate

    handle = registry.get("fakeliterature")
    handle.driver.state.seed(
        LiteratureCandidate(
            provider_key="fakeliterature",
            authority=authority,
            native_id=native_id,
            title=title,
        )
    )


def _import_literature(session, registry, actor, project_id, authority, native_id):
    from revolab import literature as literature_service

    _seed_literature(registry, authority, native_id, title=f"Seeded {native_id}")
    return literature_service.import_literature(
        session,
        registry,
        InMemorySecretStore(),
        actor,
        project_id,
        provider_key="fakeliterature",
        authority=authority,
        native_id=native_id,
    )


def test_phase13_postgres_cross_project_import_reuses_one_global_reference(
    pg_session: Session,
) -> None:
    from revolab.models import LiteratureReference, ProjectResourceLink
    from revolab.testing.fake_literature import FAKE_LITERATURE_AUTHORITY

    tag = uuid4().hex[:8]
    owner_a = services.create_actor(pg_session)
    owner_b = services.create_actor(pg_session)
    project_a = services.create_project(pg_session, owner_a, f"Lit A {tag}")
    project_b = services.create_project(pg_session, owner_b, f"Lit B {tag}")
    registry = _literature_registry()

    authority = FAKE_LITERATURE_AUTHORITY
    native_id = f"shared-{tag}"
    row_a = _import_literature(pg_session, registry, owner_a, project_a.id, authority, native_id)
    row_b = _import_literature(pg_session, registry, owner_b, project_b.id, authority, native_id)

    assert row_a.literature_id == row_b.literature_id
    assert (
        pg_session.scalar(
            select(func.count())
            .select_from(LiteratureReference)
            .where(
                LiteratureReference.authority == authority,
                LiteratureReference.native_id == native_id,
            )
        )
        == 1
    )
    links = pg_session.scalars(
        select(ProjectResourceLink).where(
            ProjectResourceLink.resource_id == row_a.literature_id
        )
    ).all()
    assert {link.project_id for link in links} == {project_a.id, project_b.id}


def test_phase13_postgres_identity_unique_constraint_is_the_backstop(
    pg_session: Session,
) -> None:
    from revolab.domain import provenance
    from revolab.models import LiteratureReference

    authority = "fakepubmed"
    native_id = f"dup-{uuid4().hex[:8]}"
    provenance.create_literature_reference_row(
        pg_session, authority, native_id, title="first"
    )
    pg_session.commit()
    with pytest.raises(IntegrityError):
        provenance.create_literature_reference_row(
            pg_session, authority, native_id, title="second"
        )
        pg_session.flush()
    pg_session.rollback()
    assert (
        pg_session.scalar(
            select(func.count())
            .select_from(LiteratureReference)
            .where(
                LiteratureReference.authority == authority,
                LiteratureReference.native_id == native_id,
            )
        )
        == 1
    )


def _literature_conflict_spies(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Record which concurrent-conflict recovery paths were entered.

    `_persist_literature_reference_from_resolver` consults these helpers ONLY in an
    `IntegrityError` recovery branch, so a non-zero count is proof that the loser
    path really ran (and a barrier that timed out cannot masquerade as success).
    """
    from revolab import services as services_module

    calls = {"identity": 0, "link": 0}
    lock = threading.Lock()
    real_identity = services_module._is_literature_identity_conflict
    real_link = services_module._is_link_uniqueness_conflict

    def identity(exc):
        with lock:
            calls["identity"] += 1
        return real_identity(exc)

    def link(exc):
        with lock:
            calls["link"] += 1
        return real_link(exc)

    monkeypatch.setattr(services_module, "_is_literature_identity_conflict", identity)
    monkeypatch.setattr(services_module, "_is_link_uniqueness_conflict", link)
    return calls


def test_phase13_postgres_concurrent_import_creates_one_reference_without_integrity_error(
    pg_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from revolab.domain import provenance as provenance_module
    from revolab.models import LiteratureReference, ProjectResourceLink
    from revolab.testing.fake_literature import FAKE_LITERATURE_AUTHORITY

    tag = uuid4().hex[:8]
    with Session(pg_engine) as setup:
        actor = services.create_actor(setup)
        project_a = services.create_project(setup, actor, f"Race A {tag}")
        project_b = services.create_project(setup, actor, f"Race B {tag}")
        actor_id = actor
        project_a_id = project_a.id
        project_b_id = project_b.id

    authority = FAKE_LITERATURE_AUTHORITY
    native_id = f"race-{tag}"

    barrier = threading.Barrier(2)
    real_find = provenance_module.find_literature_reference
    local = threading.local()

    def synced_find(session, a, n):
        result = real_find(session, a, n)
        # Force BOTH racers past their initial "not found" check before either
        # inserts, so the unique constraint is genuinely exercised. Each thread
        # syncs only on its FIRST lookup (post-rollback lookups must not block).
        if (
            result is None
            and a == authority
            and n == native_id
            and not getattr(local, "synced", False)
        ):
            local.synced = True
            # A barrier timeout MUST fail the test: silently proceeding would let
            # the race assertions pass without any loser ever hitting the unique
            # index (a false pass). The worker records the raised error.
            barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(provenance_module, "find_literature_reference", synced_find)
    conflict_calls = _literature_conflict_spies(monkeypatch)

    outcomes: dict[str, object] = {}
    errors: dict[str, BaseException] = {}
    lock = threading.Lock()

    def worker(name: str, project_id) -> None:
        try:
            with Session(pg_engine) as session:
                row = services._persist_literature_reference_from_resolver(
                    session, actor_id, project_id, authority, native_id, title="Raced"
                )
                # Force the materialization of the identity before the session closes.
                assert row.literature_id is not None
                with lock:
                    outcomes[name] = row.literature_id
        except BaseException as exc:
            with lock:
                errors[name] = exc

    threads = [
        threading.Thread(target=worker, args=("a", project_a_id)),
        threading.Thread(target=worker, args=("b", project_b_id)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    # No raw IntegrityError (or anything else) reached the caller.
    assert errors == {}
    assert len(outcomes) == 2
    assert len(set(outcomes.values())) == 1
    # ...and the loser recovery path genuinely ran (not a barrier false-pass).
    assert conflict_calls["identity"] + conflict_calls["link"] >= 1
    winner = next(iter(outcomes.values()))

    with Session(pg_engine) as verify:
        assert (
            verify.scalar(
                select(func.count())
                .select_from(LiteratureReference)
                .where(
                    LiteratureReference.authority == authority,
                    LiteratureReference.native_id == native_id,
                )
            )
            == 1
        )
        links = verify.scalars(
            select(ProjectResourceLink).where(ProjectResourceLink.resource_id == winner)
        ).all()
        assert {link.project_id for link in links} == {project_a_id, project_b_id}


def test_phase13_postgres_same_project_concurrent_import_links_once(
    pg_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from revolab.domain import provenance as provenance_module
    from revolab.models import LiteratureReference, ProjectResourceLink
    from revolab.testing.fake_literature import FAKE_LITERATURE_AUTHORITY

    tag = uuid4().hex[:8]
    with Session(pg_engine) as setup:
        actor = services.create_actor(setup)
        project = services.create_project(setup, actor, f"Same Race {tag}")
        actor_id = actor
        project_id = project.id

    authority = FAKE_LITERATURE_AUTHORITY
    native_id = f"same-{tag}"
    barrier = threading.Barrier(2)
    real_find = provenance_module.find_literature_reference
    local = threading.local()

    def synced_find(session, a, n):
        result = real_find(session, a, n)
        if (
            result is None
            and a == authority
            and n == native_id
            and not getattr(local, "synced", False)
        ):
            local.synced = True
            # A barrier timeout MUST fail the test: silently proceeding would let
            # the race assertions pass without any loser ever hitting the unique
            # index (a false pass). The worker records the raised error.
            barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(provenance_module, "find_literature_reference", synced_find)
    conflict_calls = _literature_conflict_spies(monkeypatch)

    outcomes: list[object] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            with Session(pg_engine) as session:
                row = services._persist_literature_reference_from_resolver(
                    session, actor_id, project_id, authority, native_id, title="Raced once"
                )
                with lock:
                    outcomes.append(row.literature_id)
        except BaseException as exc:
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    assert errors == []
    assert len(outcomes) == 2
    assert len(set(outcomes)) == 1
    # The loser recovery path genuinely ran (not a barrier false-pass).
    assert conflict_calls["identity"] + conflict_calls["link"] >= 1
    winner = outcomes[0]

    with Session(pg_engine) as verify:
        assert (
            verify.scalar(
                select(func.count())
                .select_from(LiteratureReference)
                .where(LiteratureReference.native_id == native_id)
            )
            == 1
        )
        assert (
            verify.scalar(
                select(func.count())
                .select_from(ProjectResourceLink)
                .where(
                    ProjectResourceLink.project_id == project_id,
                    ProjectResourceLink.resource_id == winner,
                )
            )
            == 1
        )


def test_phase13_postgres_existing_reference_link_race_is_idempotent(
    pg_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent import of an ALREADY-EXISTING global reference into the SAME
    Project must resolve to ONE link with no raw `IntegrityError`.

    This exercises the existing-reference fast path (which the create-path races
    above do not): the link insert is forced to interleave AFTER both racers observe
    "not visible", so the `(project_id, resource_id)` unique constraint is genuinely
    exercised and the recovery branch must handle it.
    """
    from revolab.domain import persistence as persistence_module
    from revolab.models import LiteratureReference, ProjectResourceLink
    from revolab.testing.fake_literature import FAKE_LITERATURE_AUTHORITY

    tag = uuid4().hex[:8]
    authority = FAKE_LITERATURE_AUTHORITY
    native_id = f"link-race-{tag}"

    with Session(pg_engine) as setup:
        actor = services.create_actor(setup)
        project_a = services.create_project(setup, actor, f"Seed A {tag}")
        project_b = services.create_project(setup, actor, f"Seed race B {tag}")
        actor_id = actor
        project_a_id = project_a.id
        project_b_id = project_b.id
        # Pre-create the global reference AND link it into project A, so the racers
        # take the existing-reference path rather than the create path.
        seeded = services._persist_literature_reference_from_resolver(
            setup, actor_id, project_a_id, authority, native_id, title="Seeded"
        )
        seeded_id = seeded.literature_id

    barrier = threading.Barrier(2)
    local = threading.local()
    real_is_visible = persistence_module.is_visible

    def synced_link(session, project_id, resource_id, *, folder=None):
        # Mirror `persistence.link`, but interleave both racers AFTER the visibility
        # check so the unique-constraint loser path is deterministic.
        if not real_is_visible(session, project_id, resource_id):
            if not getattr(local, "synced", False):
                local.synced = True
                barrier.wait(timeout=10)
            session.add(
                ProjectResourceLink(
                    project_id=project_id, resource_id=resource_id, folder=folder
                )
            )

    monkeypatch.setattr(persistence_module, "link", synced_link)
    conflict_calls = _literature_conflict_spies(monkeypatch)

    outcomes: list[object] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            with Session(pg_engine) as session:
                row = services._persist_literature_reference_from_resolver(
                    session, actor_id, project_b_id, authority, native_id, title="Raced"
                )
                with lock:
                    outcomes.append(row.literature_id)
        except BaseException as exc:
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    # No raw IntegrityError reached the caller, and both racers resolved to the SAME
    # seeded reference.
    assert errors == []
    assert len(outcomes) == 2
    assert set(outcomes) == {seeded_id}
    # The LINK-conflict recovery specifically ran (an identity conflict is
    # impossible here: the reference already existed).
    assert conflict_calls["link"] >= 1
    assert conflict_calls["identity"] == 0

    with Session(pg_engine) as verify:
        assert (
            verify.scalar(
                select(func.count())
                .select_from(LiteratureReference)
                .where(
                    LiteratureReference.authority == authority,
                    LiteratureReference.native_id == native_id,
                )
            )
            == 1
        )
        assert (
            verify.scalar(
                select(func.count())
                .select_from(ProjectResourceLink)
                .where(
                    ProjectResourceLink.project_id == project_b_id,
                    ProjectResourceLink.resource_id == seeded_id,
                )
            )
            == 1
        )


def test_phase13_postgres_evidence_source_integrity_and_search_visibility(
    pg_session: Session,
) -> None:
    from revolab import search as search_service
    from revolab.enums import EvidenceKind, ResourceKind
    from revolab.models import Evidence
    from revolab.testing.fake_literature import FAKE_LITERATURE_AUTHORITY

    tag = uuid4().hex[:8]
    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, f"Lit search {tag}")
    registry = _literature_registry()
    native_id = f"evidence-{tag}"
    row = _import_literature(
        pg_session, registry, actor, project.id, FAKE_LITERATURE_AUTHORITY, native_id
    )

    # Import alone creates zero Evidence.
    assert (
        pg_session.scalar(
            select(func.count()).select_from(Evidence).where(Evidence.project_id == project.id)
        )
        == 0
    )
    # The imported publication is visible to Phase-12 Project search.
    hits = search_service.search(pg_session, actor, project.id, query=native_id)
    assert row.literature_id in {hit.target_id for hit in hits.hits}

    decision = services.create_decision(
        pg_session, actor, project.id, title=f"Adopt {tag}", statement="s"
    )
    evidence = services.create_evidence(
        pg_session,
        actor,
        project.id,
        kind=EvidenceKind.LITERATURE.value,
        source_kind=ResourceKind.LITERATURE_REFERENCE.value,
        source_id=row.literature_id,
        target_kind="decision",
        target_id=decision.id,
    )
    assert evidence.source_resource_id == row.literature_id
    assert (
        pg_session.scalar(
            select(func.count()).select_from(Evidence).where(Evidence.project_id == project.id)
        )
        == 1
    )


def test_phase13_postgres_authorization_negatives(pg_session: Session) -> None:
    from revolab import literature as literature_service
    from revolab.domain.errors import AuthorizationError
    from revolab.models import LiteratureReference
    from revolab.testing.fake_literature import FAKE_LITERATURE_AUTHORITY

    tag = uuid4().hex[:8]
    owner = services.create_actor(pg_session)
    viewer = services.create_actor(pg_session)
    stranger = services.create_actor(pg_session)
    project = services.create_project(pg_session, owner, f"Lit authz {tag}")
    services.add_membership(pg_session, owner, project.id, viewer, Role.VIEWER.value)
    registry = _literature_registry()
    authority = FAKE_LITERATURE_AUTHORITY
    native_id = f"authz-{tag}"

    # A viewer may discover...
    discovered = literature_service.discover_literature(
        pg_session,
        registry,
        InMemorySecretStore(),
        viewer,
        project.id,
        provider_key="fakeliterature",
        query="kinase",
    )
    assert discovered.candidates
    # ...but must not import.
    with pytest.raises(AuthorizationError):
        _import_literature(pg_session, registry, viewer, project.id, authority, native_id)
    with pytest.raises(AuthorizationError):
        _import_literature(pg_session, registry, stranger, project.id, authority, native_id)
    assert (
        pg_session.scalar(
            select(func.count())
            .select_from(LiteratureReference)
            .where(LiteratureReference.native_id == native_id)
        )
        == 0
    )


# ---------------------------------------------------------------------------
# Phase 14 — external protein discovery & explicit import on PostgreSQL
#
# PostgreSQL is the final persistence truth: the global unique
# (authority, native_id) identity is the concurrency linearization point for a first
# import, the atomic bundle means a committed ExternalIdentity ALWAYS implies a
# committed complete bundle, and no raw IntegrityError may reach the caller.
# ---------------------------------------------------------------------------


def _protein_registry(*, healthy: bool = True):
    from revolab.testing.fake_protein import FakeProteinDriver

    registry = DriverRegistry()
    registry.register(FakeProteinDriver(healthy=healthy))
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return registry


def _seed_protein(registry, native_id, *, sequence=None, protein_name=None, length=None):
    """Register one exact record in the in-process fake provider's state."""
    from revolab.capabilities import ResolvedProteinRecord
    from revolab.testing.fake_protein import (
        FAKE_PROTEIN_AUTHORITY,
        FAKE_PROTEIN_PROVIDER_KEY,
        deterministic_sequence,
    )

    handle = registry.get(FAKE_PROTEIN_PROVIDER_KEY)
    canonical = sequence if sequence is not None else deterministic_sequence(native_id, 37)
    record = ResolvedProteinRecord(
        provider_key=FAKE_PROTEIN_PROVIDER_KEY,
        authority=FAKE_PROTEIN_AUTHORITY,
        native_id=native_id,
        canonical_sequence=canonical,
        protein_name=protein_name or f"Seeded protein {native_id}",
        organism_name="Synthetic organism",
        entry_name=f"SEED_{native_id}".upper(),
        primary_gene_name="SEED",
        sequence_length=len(canonical) if length is None else length,
        reviewed=True,
        source_release="test_2026_01",
        source_release_date="01-January-2026",
    )
    handle.driver.state.seed(record)
    return record


def _import_protein(session, registry, actor, project_id, native_id, *, sequence=None):
    from revolab import proteins as protein_service
    from revolab.testing.fake_protein import (
        FAKE_PROTEIN_AUTHORITY,
        FAKE_PROTEIN_PROVIDER_KEY,
    )

    _seed_protein(registry, native_id, sequence=sequence)
    return protein_service.import_protein(
        session,
        registry,
        InMemorySecretStore(),
        actor,
        project_id,
        provider_key=FAKE_PROTEIN_PROVIDER_KEY,
        authority=FAKE_PROTEIN_AUTHORITY,
        native_id=native_id,
    )


def _protein_bundle_counts(session: Session) -> tuple[int, ...]:
    from revolab.models import (
        ExternalIdentity,
        ExternalReference,
        GlobalProvenanceEdge,
        ProjectResourceLink,
        ResourceStewardship,
        ScientificObjectExternalIdentity,
        ScientificObjectRevision,
        ScientificObjectSeries,
    )

    models = (
        ExternalIdentity,
        ExternalReference,
        ScientificObjectSeries,
        ScientificObjectRevision,
        ScientificObjectExternalIdentity,
        GlobalProvenanceEdge,
        ProjectResourceLink,
        ResourceStewardship,
    )
    return tuple(
        int(session.scalar(select(func.count()).select_from(model)) or 0) for model in models
    )


def _protein_bundle_tuple(result) -> tuple:
    return (
        result.protein_series_id,
        result.sequence_series_id,
        result.protein_revision_id,
        result.sequence_revision_id,
        result.external_reference_id,
    )


def test_phase14_postgres_initial_import_creates_the_canonical_bundle(
    pg_session: Session,
) -> None:
    from revolab.domain import persistence
    from revolab.models import (
        ExternalIdentity,
        ExternalReference,
        GlobalProvenanceEdge,
        ProjectResourceLink,
        ResourceStewardship,
        ScientificObjectExternalIdentity,
        ScientificObjectRevision,
    )
    from revolab.testing.fake_protein import FAKE_PROTEIN_AUTHORITY

    tag = uuid4().hex[:8]
    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, f"Phase14 initial {tag}")
    registry = _protein_registry()
    native_id = f"seed-{tag}"

    result = _import_protein(pg_session, registry, actor, project.id, native_id)

    identity = pg_session.scalars(
        select(ExternalIdentity).where(
            ExternalIdentity.authority == FAKE_PROTEIN_AUTHORITY,
            ExternalIdentity.native_id == native_id,
        )
    ).one()
    assert identity.kind == "protein"
    references = pg_session.scalars(
        select(ExternalReference).where(
            ExternalReference.external_identity_id == identity.external_identity_id
        )
    ).all()
    assert len(references) == 1
    assert references[0].checksum is not None
    assert references[0].as_of is not None
    assert set(references[0].cache_metadata or {}) <= {
        "resolver_provider",
        "source_release",
        "source_release_date",
    }
    mappings = pg_session.scalars(
        select(ScientificObjectExternalIdentity).where(
            ScientificObjectExternalIdentity.external_identity_id
            == identity.external_identity_id
        )
    ).all()
    assert {mapping.qualifier: mapping.series_id for mapping in mappings} == {
        "identity": result.protein_series_id,
        "sequence": result.sequence_series_id,
    }
    assert all(mapping.is_canonical for mapping in mappings)

    edges = pg_session.scalars(
        select(GlobalProvenanceEdge).where(
            (GlobalProvenanceEdge.source_id == result.external_reference_id)
            | (GlobalProvenanceEdge.source_id == result.sequence_series_id)
        )
    ).all()
    shapes = {(edge.relation_type, edge.source_kind, edge.target_kind) for edge in edges}
    assert shapes == {
        ("represents", "scientific_object_series", "scientific_object_series"),
        ("imported_as", "external_reference", "scientific_object_revision"),
    }
    imported = [edge for edge in edges if edge.relation_type == "imported_as"]
    assert len(imported) == 2
    assert {edge.target_id for edge in imported} == {
        result.protein_revision_id,
        result.sequence_revision_id,
    }
    represents = [edge for edge in edges if edge.relation_type == "represents"]
    assert represents[0].target_id == result.protein_series_id

    sequence_revision = pg_session.get(ScientificObjectRevision, result.sequence_revision_id)
    assert sequence_revision is not None
    assert sequence_revision.checksum == persistence.payload_checksum(sequence_revision.payload)
    assert sequence_revision.payload["kind"] == "protein"
    protein_revision = pg_session.get(ScientificObjectRevision, result.protein_revision_id)
    assert protein_revision is not None
    assert protein_revision.payload["source_sequence_ref"] is None

    linked = set(
        pg_session.scalars(
            select(ProjectResourceLink.resource_id).where(
                ProjectResourceLink.project_id == project.id
            )
        )
    )
    assert {
        result.protein_series_id,
        result.protein_revision_id,
        result.sequence_series_id,
        result.sequence_revision_id,
        result.external_reference_id,
    } <= linked
    stewards = {
        row.resource_id: row.steward_project_id
        for row in pg_session.scalars(select(ResourceStewardship))
    }
    assert stewards[result.protein_series_id] == project.id
    assert stewards[result.sequence_series_id] == project.id
    assert stewards[result.external_reference_id] == project.id
    # The ExternalIdentity is never Project-owned (it is the global registry).
    assert identity.external_identity_id not in stewards


def test_phase14_postgres_repeat_and_cross_project_import_reuse_the_bundle(
    pg_session: Session,
) -> None:
    from revolab.models import ExternalIdentity, ExternalReference, ProjectResourceLink

    tag = uuid4().hex[:8]
    actor_a = services.create_actor(pg_session)
    project_a = services.create_project(pg_session, actor_a, f"Phase14 A {tag}")
    actor_b = services.create_actor(pg_session)
    project_b = services.create_project(pg_session, actor_b, f"Phase14 B {tag}")
    registry = _protein_registry()
    native_id = f"reuse-{tag}"

    first = _import_protein(pg_session, registry, actor_a, project_a.id, native_id)
    before = _protein_bundle_counts(pg_session)

    again = _import_protein(pg_session, registry, actor_a, project_a.id, native_id)
    assert again == first

    from_b = _import_protein(pg_session, registry, actor_b, project_b.id, native_id)
    assert _protein_bundle_tuple(from_b) == _protein_bundle_tuple(first)

    after = _protein_bundle_counts(pg_session)
    # Only the LINK count may grow: 5 links for the SAME bundle into project B.
    assert after[:6] == before[:6]
    assert after[6] - before[6] == 5

    identity = pg_session.scalars(
        select(ExternalIdentity).where(ExternalIdentity.native_id == native_id)
    ).one()
    assert (
        pg_session.scalar(
            select(func.count())
            .select_from(ExternalReference)
            .where(ExternalReference.external_identity_id == identity.external_identity_id)
        )
        == 1
    )
    for project in (project_a, project_b):
        visible = set(
            pg_session.scalars(
                select(ProjectResourceLink.resource_id).where(
                    ProjectResourceLink.project_id == project.id
                )
            )
        )
        assert {first.protein_series_id, first.sequence_series_id} <= visible
    # Project B gained only a read lens: it did not steal stewardship.
    from revolab.models import ResourceStewardship

    stewards = {
        row.resource_id: row.steward_project_id
        for row in pg_session.scalars(select(ResourceStewardship))
    }
    assert stewards[first.protein_series_id] == project_a.id


def test_phase14_postgres_identity_and_mapping_uniqueness_are_the_backstop(
    pg_session: Session,
) -> None:
    from revolab.domain import scientific_object
    from revolab.domain.errors import ConflictError
    from revolab.models import ExternalIdentity, ScientificObjectExternalIdentity

    tag = uuid4().hex[:8]
    authority = f"mirrorauthority-{tag}"
    native_id = f"unique-{tag}"
    # The database constraint is the real backstop: a second row for the same
    # durable (authority, native_id) is refused by PostgreSQL.
    pg_session.add(ExternalIdentity(authority=authority, native_id=native_id, kind="protein"))
    pg_session.flush()
    pg_session.add(ExternalIdentity(authority=authority, native_id=native_id, kind="protein"))
    with pytest.raises(IntegrityError):
        pg_session.flush()
    pg_session.rollback()

    identity = scientific_object.get_or_create_external_identity(
        pg_session, authority, native_id, kind="protein"
    )
    pg_session.commit()
    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, f"Phase14 unique {tag}")
    first_series = services.create_object(pg_session, actor, project.id, "protein", "P")
    second_series = services.create_object(pg_session, actor, project.id, "sequence", "S")
    services.attach_external_identity(
        pg_session, actor, project.id, first_series, authority, native_id, qualifier="sequence"
    )
    # One series per (external_identity, qualifier): a second target is refused.
    with pytest.raises(ConflictError):
        services.attach_external_identity(
            pg_session, actor, project.id, second_series, authority, native_id, qualifier="sequence"
        )
    pg_session.rollback()
    assert (
        pg_session.scalar(
            select(func.count())
            .select_from(ScientificObjectExternalIdentity)
            .where(
                ScientificObjectExternalIdentity.external_identity_id
                == identity.external_identity_id
            )
        )
        == 1
    )


def test_phase14_postgres_changed_snapshot_conflict_leaves_state_untouched(
    pg_session: Session,
) -> None:
    from revolab import proteins as protein_service
    from revolab.domain.errors import ConflictError
    from revolab.models import ScientificObjectRevision
    from revolab.testing.fake_protein import (
        FAKE_PROTEIN_AUTHORITY,
        FAKE_PROTEIN_PROVIDER_KEY,
        deterministic_sequence,
    )

    tag = uuid4().hex[:8]
    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, f"Phase14 conflict {tag}")
    registry = _protein_registry()
    native_id = f"conflict-{tag}"
    first = _import_protein(pg_session, registry, actor, project.id, native_id)
    before = _protein_bundle_counts(pg_session)

    _seed_protein(registry, native_id, sequence=deterministic_sequence("MUTATED", 37))
    with pytest.raises(ConflictError, match="changed since the imported snapshot"):
        protein_service.import_protein(
            pg_session,
            registry,
            InMemorySecretStore(),
            actor,
            project.id,
            provider_key=FAKE_PROTEIN_PROVIDER_KEY,
            authority=FAKE_PROTEIN_AUTHORITY,
            native_id=native_id,
        )
    assert _protein_bundle_counts(pg_session) == before
    revision = pg_session.get(ScientificObjectRevision, first.sequence_revision_id)
    assert revision is not None
    assert revision.payload["sequence"] == deterministic_sequence(native_id, 37)


def test_phase14_postgres_atomic_rollback_leaves_no_partial_import(
    pg_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure AFTER objects are staged must roll back the WHOLE local bundle."""
    from revolab import proteins as protein_service
    from revolab import services as services_module
    from revolab.models import ExternalIdentity, ProjectResourceLink
    from revolab.testing.fake_protein import (
        FAKE_PROTEIN_AUTHORITY,
        FAKE_PROTEIN_PROVIDER_KEY,
    )

    tag = uuid4().hex[:8]
    native_id = f"atomic-{tag}"
    with Session(pg_engine) as setup:
        actor = services.create_actor(setup)
        project = services.create_project(setup, actor, f"Phase14 atomic {tag}")
        actor_id = actor
        project_id = project.id
    registry = _protein_registry()
    _seed_protein(registry, native_id)

    with Session(pg_engine) as baseline:
        before = _protein_bundle_counts(baseline)

    def explode(*args, **kwargs):
        raise RuntimeError("injected failure after objects were staged")

    monkeypatch.setattr(services_module, "record_imported_as", explode)
    with Session(pg_engine) as session:
        with pytest.raises(RuntimeError):
            protein_service.import_protein(
                session,
                registry,
                InMemorySecretStore(),
                actor_id,
                project_id,
                provider_key=FAKE_PROTEIN_PROVIDER_KEY,
                authority=FAKE_PROTEIN_AUTHORITY,
                native_id=native_id,
            )
        # The request-session contract: an unhandled failure closes the session,
        # which rolls back every staged row.
        session.rollback()

    with Session(pg_engine) as verify:
        assert _protein_bundle_counts(verify) == before
        assert (
            verify.scalar(
                select(func.count())
                .select_from(ExternalIdentity)
                .where(ExternalIdentity.native_id == native_id)
            )
            == 0
        )
        assert (
            verify.scalar(
                select(func.count())
                .select_from(ProjectResourceLink)
                .where(ProjectResourceLink.project_id == project_id)
            )
            == 0
        )


def _protein_conflict_spies(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Record which concurrent-conflict recovery paths were entered.

    These helpers are consulted ONLY inside an `IntegrityError` recovery branch, so a
    non-zero count proves the loser path really ran (a timed-out barrier cannot
    masquerade as success).
    """
    from revolab import proteins as proteins_module

    calls = {"identity": 0, "link": 0}
    lock = threading.Lock()
    real_identity = proteins_module._is_external_identity_conflict
    real_link = proteins_module._is_link_conflict

    def identity(exc):
        with lock:
            calls["identity"] += 1
        return real_identity(exc)

    def link(exc):
        with lock:
            calls["link"] += 1
        return real_link(exc)

    monkeypatch.setattr(proteins_module, "_is_external_identity_conflict", identity)
    monkeypatch.setattr(proteins_module, "_is_link_conflict", link)
    return calls


@pytest.mark.parametrize("same_project", [True, False])
def test_phase14_postgres_concurrent_first_import_converges_on_one_bundle(
    pg_engine: Engine, monkeypatch: pytest.MonkeyPatch, same_project: bool
) -> None:
    from revolab import proteins as protein_service
    from revolab.domain import scientific_object as scientific_object_module
    from revolab.models import (
        ExternalIdentity,
        ExternalReference,
        GlobalProvenanceEdge,
        ProjectResourceLink,
        ScientificObjectRevision,
    )
    from revolab.testing.fake_protein import (
        FAKE_PROTEIN_AUTHORITY,
        FAKE_PROTEIN_PROVIDER_KEY,
    )

    tag = uuid4().hex[:8]
    with Session(pg_engine) as setup:
        actor = services.create_actor(setup)
        project_a = services.create_project(setup, actor, f"Phase14 race A {tag}")
        project_b = (
            project_a
            if same_project
            else services.create_project(setup, actor, f"Phase14 race B {tag}")
        )
        actor_id = actor
        project_a_id = project_a.id
        project_b_id = project_b.id

    native_id = f"race-{tag}"
    registry = _protein_registry()
    _seed_protein(registry, native_id)

    barrier = threading.Barrier(2)
    real_find = scientific_object_module.find_external_identity
    local = threading.local()

    def synced_find(session, authority, lookup):
        found = real_find(session, authority, lookup)
        # Force BOTH racers past their initial "not found" check before either
        # inserts, so the unique constraint is genuinely exercised. Each thread syncs
        # only on its FIRST lookup (post-rollback lookups must not block).
        if found is None and lookup == native_id and not getattr(local, "synced", False):
            local.synced = True
            barrier.wait(timeout=10)
        return found

    monkeypatch.setattr(scientific_object_module, "find_external_identity", synced_find)
    conflict_calls = _protein_conflict_spies(monkeypatch)

    outcomes: dict[str, tuple] = {}
    errors: dict[str, BaseException] = {}
    lock = threading.Lock()

    def worker(name: str, project_id) -> None:
        try:
            with Session(pg_engine) as session:
                result = protein_service.import_protein(
                    session,
                    registry,
                    InMemorySecretStore(),
                    actor_id,
                    project_id,
                    provider_key=FAKE_PROTEIN_PROVIDER_KEY,
                    authority=FAKE_PROTEIN_AUTHORITY,
                    native_id=native_id,
                )
                with lock:
                    outcomes[name] = _protein_bundle_tuple(result)
        except BaseException as exc:
            with lock:
                errors[name] = exc

    threads = [
        threading.Thread(target=worker, args=("a", project_a_id)),
        threading.Thread(target=worker, args=("b", project_b_id)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    # No raw IntegrityError (or anything else) reached a caller.
    assert errors == {}
    assert len(outcomes) == 2
    # Both racers converged on THE SAME global bundle.
    assert len(set(outcomes.values())) == 1
    # ...and the loser recovery path genuinely ran (not a barrier false-pass).
    assert conflict_calls["identity"] + conflict_calls["link"] >= 1

    bundle = next(iter(outcomes.values()))
    with Session(pg_engine) as verify:
        assert (
            verify.scalar(
                select(func.count())
                .select_from(ExternalIdentity)
                .where(
                    ExternalIdentity.authority == FAKE_PROTEIN_AUTHORITY,
                    ExternalIdentity.native_id == native_id,
                )
            )
            == 1
        )
        assert (
            verify.scalar(
                select(func.count())
                .select_from(ExternalReference)
                .join(
                    ExternalIdentity,
                    ExternalIdentity.external_identity_id
                    == ExternalReference.external_identity_id,
                )
                .where(ExternalIdentity.native_id == native_id)
            )
            == 1
        )
        assert (
            verify.scalar(
                select(func.count())
                .select_from(ScientificObjectRevision)
                .where(ScientificObjectRevision.series_id.in_([bundle[0], bundle[1]]))
            )
            == 2
        )
        assert (
            verify.scalar(
                select(func.count())
                .select_from(GlobalProvenanceEdge)
                .where(GlobalProvenanceEdge.source_id == bundle[4])
            )
            == 2
        )
        assert (
            verify.scalar(
                select(func.count())
                .select_from(GlobalProvenanceEdge)
                .where(
                    GlobalProvenanceEdge.relation_type == "represents",
                    GlobalProvenanceEdge.target_id == bundle[0],
                )
            )
            == 1
        )
        links = verify.scalars(
            select(ProjectResourceLink).where(ProjectResourceLink.resource_id == bundle[0])
        ).all()
        assert {link.project_id for link in links} == {project_a_id, project_b_id}


def test_phase14_postgres_existing_bundle_link_race_is_idempotent(
    pg_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two concurrent linkers of an EXISTING bundle into ONE new Project converge.

    This exercises the EXISTING-bundle fast path (which the first-import races above
    do not): the bundle is already global, so both racers skip creation and race only
    on the `uq_link_project_resource` insert for the SAME Project.
    """
    from revolab import proteins as protein_service
    from revolab.domain import persistence as persistence_module
    from revolab.models import ProjectResourceLink
    from revolab.testing.fake_protein import (
        FAKE_PROTEIN_AUTHORITY,
        FAKE_PROTEIN_PROVIDER_KEY,
    )

    tag = uuid4().hex[:8]
    native_id = f"link-race-{tag}"
    with Session(pg_engine) as setup:
        actor = services.create_actor(setup)
        project_seed = services.create_project(setup, actor, f"Phase14 link seed {tag}")
        project_race = services.create_project(setup, actor, f"Phase14 link race {tag}")
        actor_id = actor
        seed_project_id = project_seed.id
        race_project_id = project_race.id

    registry = _protein_registry()
    # Pre-create the global bundle (in a THIRD project) so neither racer creates it.
    with Session(pg_engine) as seed_session:
        seeded = _import_protein(seed_session, registry, actor_id, seed_project_id, native_id)
        seeded_tuple = _protein_bundle_tuple(seeded)

    barrier = threading.Barrier(2)
    real_link = persistence_module.link
    local = threading.local()

    def interleaved_link(session, project_id, resource_id, **kwargs):
        # Interleave AFTER the visibility read but BEFORE the insert, so both racers
        # observe "not visible" for the SAME Project and the unique index decides.
        if (
            not persistence_module.is_visible(session, project_id, resource_id)
            and not getattr(local, "synced", False)
        ):
            local.synced = True
            barrier.wait(timeout=10)
        return real_link(session, project_id, resource_id, **kwargs)

    monkeypatch.setattr(persistence_module, "link", interleaved_link)
    conflict_calls = _protein_conflict_spies(monkeypatch)

    outcomes: dict[str, tuple] = {}
    errors: dict[str, BaseException] = {}
    lock = threading.Lock()

    def worker(name: str) -> None:
        try:
            with Session(pg_engine) as session:
                result = protein_service.import_protein(
                    session,
                    registry,
                    InMemorySecretStore(),
                    actor_id,
                    race_project_id,
                    provider_key=FAKE_PROTEIN_PROVIDER_KEY,
                    authority=FAKE_PROTEIN_AUTHORITY,
                    native_id=native_id,
                )
                with lock:
                    outcomes[name] = _protein_bundle_tuple(result)
        except BaseException as exc:
            with lock:
                errors[name] = exc

    threads = [
        threading.Thread(target=worker, args=("a",)),
        threading.Thread(target=worker, args=("b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    # No raw IntegrityError reached a caller: the loser resolved to the winner.
    assert errors == {}
    assert len(outcomes) == 2
    assert set(outcomes.values()) == {seeded_tuple}
    # The link-conflict recovery branch genuinely ran.
    assert conflict_calls["link"] >= 1

    with Session(pg_engine) as verify:
        links = verify.scalars(
            select(ProjectResourceLink).where(
                ProjectResourceLink.resource_id == seeded_tuple[0]
            )
        ).all()
        assert {link.project_id for link in links} == {seed_project_id, race_project_id}
        assert len(links) == 2
        # No duplicate rows: the unique (project, resource) constraint held.
        assert (
            verify.scalar(
                select(func.count())
                .select_from(ProjectResourceLink)
                .where(
                    ProjectResourceLink.project_id == race_project_id,
                    ProjectResourceLink.resource_id == seeded_tuple[0],
                )
            )
            == 1
        )


def test_phase14_postgres_project_search_and_context_integration(
    pg_session: Session,
) -> None:
    from revolab import search as search_service
    from revolab.enums import SearchScope

    tag = uuid4().hex[:8]
    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, f"Phase14 search {tag}")
    registry = _protein_registry()
    native_id = f"searchseed{tag}"
    result = _import_protein(pg_session, registry, actor, project.id, native_id)

    by_accession = search_service.search(
        pg_session, actor, project.id, query=native_id, scope=SearchScope.PROJECT_SHARED
    ).hits
    series_hits = {
        hit.target_id
        for hit in by_accession
        if hit.target_kind.value == "scientific_object_series"
    }
    assert {result.protein_series_id, result.sequence_series_id} <= series_hits

    context = build_context(
        pg_session,
        actor,
        project.id,
        registry,
        ContextSelectionCreate(series_ids=[result.protein_series_id]),
    )
    assert result.protein_series_id in {ref.series_id for ref in context.series}


# ---------------------------------------------------------------------------
# Phase 15 — RCSB PDB structure discovery / immutable coordinate import
#
# PostgreSQL is the concurrency truth: the ExternalIdentity unique constraint is
# the scientific-identity linearization point, and the internal content-addressed
# ArtifactReference has its OWN unique identity, which the shared get-or-create
# must win/lose atomically WITHOUT aborting the loser's transaction.
# ---------------------------------------------------------------------------


def _structure_registry(*, healthy: bool = True):
    from revolab.testing.fake_structure import FakeStructureDriver

    registry = DriverRegistry()
    registry.register(FakeStructureDriver(healthy=healthy))
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return registry


def _seed_structure(registry, native_id, *, coordinate_seed=None, resolution=1.5):
    """Register one exact record in the in-process fake provider's state.

    `coordinate_seed` allows two DIFFERENT durable identities to carry byte-identical
    coordinates, which is what forces the internal-artifact race.
    """
    from revolab.capabilities import ResolvedStructureRecord
    from revolab.testing.fake_structure import (
        FAKE_STRUCTURE_AUTHORITY,
        FAKE_STRUCTURE_PROVIDER_KEY,
        deterministic_coordinates,
    )

    handle = registry.get(FAKE_STRUCTURE_PROVIDER_KEY)
    record = ResolvedStructureRecord(
        provider_key=FAKE_STRUCTURE_PROVIDER_KEY,
        authority=FAKE_STRUCTURE_AUTHORITY,
        native_id=native_id,
        coordinate_format="mmcif",
        coordinate_bytes=deterministic_coordinates(coordinate_seed or native_id),
        title=f"Seeded structure {native_id}",
        experimental_methods=("X-RAY DIFFRACTION",),
        resolution_angstrom=resolution,
        entry_revision_major=1,
        entry_revision_minor=0,
        entry_revision_date="2026-01-01T00:00:00Z",
    )
    handle.driver.state.seed(record)
    return record


def _import_structure(session, registry, content_store, actor, project_id, native_id):
    from revolab import structures as structure_service
    from revolab.testing.fake_structure import (
        FAKE_STRUCTURE_AUTHORITY,
        FAKE_STRUCTURE_PROVIDER_KEY,
    )

    return structure_service.import_structure(
        session,
        registry,
        InMemorySecretStore(),
        content_store,
        actor,
        project_id,
        provider_key=FAKE_STRUCTURE_PROVIDER_KEY,
        authority=FAKE_STRUCTURE_AUTHORITY,
        native_id=native_id,
    )


def _structure_bundle_counts(session: Session) -> tuple[int, ...]:
    from revolab.models import (
        ArtifactReference,
        ExternalIdentity,
        ExternalReference,
        GlobalProvenanceEdge,
        ProjectResourceLink,
        ResourceStewardship,
        ScientificObjectExternalIdentity,
        ScientificObjectRevision,
        ScientificObjectSeries,
    )

    models = (
        ExternalIdentity,
        ExternalReference,
        ArtifactReference,
        ScientificObjectSeries,
        ScientificObjectRevision,
        ScientificObjectExternalIdentity,
        GlobalProvenanceEdge,
        ProjectResourceLink,
        ResourceStewardship,
    )
    return tuple(
        int(session.scalar(select(func.count()).select_from(model)) or 0) for model in models
    )


def _structure_bundle_tuple(result) -> tuple:
    return (
        result.structure_series_id,
        result.structure_revision_id,
        result.coordinate_artifact_id,
        result.external_reference_id,
    )


def test_phase15_postgres_initial_import_creates_the_canonical_bundle(
    pg_session: Session, tmp_path
) -> None:
    from revolab.content_store import ContentStore
    from revolab.models import (
        ArtifactReference,
        ExternalIdentity,
        GlobalProvenanceEdge,
        ScientificObjectExternalIdentity,
        ScientificObjectRevision,
    )
    from revolab.testing.fake_structure import FAKE_STRUCTURE_AUTHORITY

    tag = uuid4().hex[:8]
    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, f"Phase15 initial {tag}")
    native_id = f"P15{tag}"[:12]
    registry = _structure_registry()
    _seed_structure(registry, native_id)
    content_store = ContentStore(tmp_path / "content")

    result = _import_structure(pg_session, registry, content_store, actor, project.id, native_id)

    identity = pg_session.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.authority == FAKE_STRUCTURE_AUTHORITY,
            ExternalIdentity.native_id == native_id,
        )
    )
    assert identity is not None and identity.kind == "structure"
    mapping = pg_session.scalar(
        select(ScientificObjectExternalIdentity).where(
            ScientificObjectExternalIdentity.external_identity_id
            == identity.external_identity_id
        )
    )
    assert mapping is not None
    assert mapping.qualifier == "identity" and mapping.is_canonical is True
    assert mapping.series_id == result.structure_series_id

    revision = pg_session.get(ScientificObjectRevision, result.structure_revision_id)
    assert revision is not None
    assert revision.payload["pdb_id"] is None
    assert revision.payload["coordinates_ref"] is None
    assert revision.payload["resolution"] == 1.5

    artifact = pg_session.get(ArtifactReference, result.coordinate_artifact_id)
    assert artifact is not None
    assert artifact.authority == "revolab"
    assert artifact.checksum == artifact.native_id
    assert artifact.content_type == "chemical/x-cif"
    assert content_store.get(artifact.native_id).startswith(b"data_")

    imported = list(
        pg_session.scalars(
            select(GlobalProvenanceEdge).where(
                GlobalProvenanceEdge.relation_type == "imported_as",
                GlobalProvenanceEdge.target_id == result.structure_revision_id,
            )
        )
    )
    assert {edge.source_id for edge in imported} == {
        result.external_reference_id,
        result.coordinate_artifact_id,
    }
    assert {edge.target_id for edge in imported} == {result.structure_revision_id}


def test_phase15_postgres_repeat_import_is_idempotent(pg_session: Session, tmp_path) -> None:
    from revolab.content_store import ContentStore

    tag = uuid4().hex[:8]
    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, f"Phase15 repeat {tag}")
    native_id = f"P15{tag}"[:12]
    registry = _structure_registry()
    _seed_structure(registry, native_id)
    content_store = ContentStore(tmp_path / "content")

    first = _import_structure(pg_session, registry, content_store, actor, project.id, native_id)
    before = _structure_bundle_counts(pg_session)
    second = _import_structure(pg_session, registry, content_store, actor, project.id, native_id)
    assert second == first
    assert _structure_bundle_counts(pg_session) == before


def test_phase15_postgres_cross_project_reuse_keeps_stewardship(
    pg_session: Session, tmp_path
) -> None:
    from revolab.content_store import ContentStore
    from revolab.models import ArtifactReference, ExternalIdentity, ResourceStewardship

    tag = uuid4().hex[:8]
    owner_a = services.create_actor(pg_session)
    project_a = services.create_project(pg_session, owner_a, f"Phase15 A {tag}")
    owner_b = services.create_actor(pg_session)
    project_b = services.create_project(pg_session, owner_b, f"Phase15 B {tag}")
    native_id = f"P15{tag}"[:12]
    registry = _structure_registry()
    _seed_structure(registry, native_id)
    content_store = ContentStore(tmp_path / "content")

    first = _import_structure(pg_session, registry, content_store, owner_a, project_a.id, native_id)
    second = _import_structure(pg_session, registry, content_store, owner_b, project_b.id, native_id)
    assert second == first
    assert (
        pg_session.scalar(
            select(func.count())
            .select_from(ExternalIdentity)
            .where(ExternalIdentity.native_id == native_id)
        )
        == 1
    )
    assert (
        pg_session.scalar(
            select(func.count())
            .select_from(ArtifactReference)
            .where(ArtifactReference.artifact_id == first.coordinate_artifact_id)
        )
        == 1
    )
    for resource_id in (first.structure_series_id, first.coordinate_artifact_id):
        stewardship = pg_session.get(ResourceStewardship, resource_id)
        assert stewardship is not None
        assert stewardship.steward_project_id == project_a.id


def _structure_conflict_spies(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    from revolab import structures as structures_module

    calls = {"identity": 0, "link": 0}
    real_identity = structures_module._is_external_identity_conflict
    real_link = structures_module._is_link_conflict

    def identity(exc):
        if real_identity(exc):
            calls["identity"] += 1
        return real_identity(exc)

    def link(exc):
        if real_link(exc):
            calls["link"] += 1
        return real_link(exc)

    monkeypatch.setattr(structures_module, "_is_external_identity_conflict", identity)
    monkeypatch.setattr(structures_module, "_is_link_conflict", link)
    return calls


@pytest.mark.parametrize("same_project", [True, False])
def test_phase15_postgres_concurrent_first_import_converges_on_one_bundle(
    pg_engine: Engine, monkeypatch: pytest.MonkeyPatch, same_project: bool, tmp_path
) -> None:
    from revolab.content_store import ContentStore
    from revolab.domain import scientific_object as scientific_object_module
    from revolab.models import (
        ArtifactReference,
        ExternalIdentity,
        ExternalReference,
        ScientificObjectRevision,
    )
    from revolab.testing.fake_structure import FAKE_STRUCTURE_AUTHORITY

    tag = uuid4().hex[:8]
    with Session(pg_engine) as setup:
        actor = services.create_actor(setup)
        project_a = services.create_project(setup, actor, f"Phase15 race A {tag}")
        project_b = (
            project_a
            if same_project
            else services.create_project(setup, actor, f"Phase15 race B {tag}")
        )
        actor_id = actor
        project_a_id = project_a.id
        project_b_id = project_b.id

    native_id = f"P15{tag}"[:12]
    registry = _structure_registry()
    _seed_structure(registry, native_id)
    content_store = ContentStore(tmp_path / "content")

    barrier = threading.Barrier(2)
    real_find = scientific_object_module.find_external_identity
    local = threading.local()

    def synced_find(session, authority, lookup):
        found = real_find(session, authority, lookup)
        # Force BOTH racers past their initial "not found" check before either
        # inserts, so the unique constraint is genuinely exercised. Each thread syncs
        # only on its FIRST lookup (post-rollback lookups must not block).
        if found is None and lookup == native_id and not getattr(local, "synced", False):
            local.synced = True
            barrier.wait(timeout=10)
        return found

    monkeypatch.setattr(scientific_object_module, "find_external_identity", synced_find)
    conflict_calls = _structure_conflict_spies(monkeypatch)

    outcomes: dict[str, tuple] = {}
    errors: dict[str, BaseException] = {}
    lock = threading.Lock()

    def worker(name: str, project_id) -> None:
        try:
            with Session(pg_engine) as session:
                result = _import_structure(
                    session, registry, content_store, actor_id, project_id, native_id
                )
                with lock:
                    outcomes[name] = _structure_bundle_tuple(result)
        except BaseException as exc:
            with lock:
                errors[name] = exc

    threads = [
        threading.Thread(target=worker, args=("a", project_a_id)),
        threading.Thread(target=worker, args=("b", project_b_id)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    # No raw IntegrityError (or anything else) reached a caller.
    assert errors == {}
    assert len(outcomes) == 2
    # Both racers converged on THE SAME global bundle.
    assert len(set(outcomes.values())) == 1
    # ...and the loser recovery path genuinely ran (not a barrier false-pass).
    assert conflict_calls["identity"] + conflict_calls["link"] >= 1

    with Session(pg_engine) as verify:
        assert (
            verify.scalar(
                select(func.count())
                .select_from(ExternalIdentity)
                .where(ExternalIdentity.native_id == native_id)
            )
            == 1
        )
        assert (
            verify.scalar(
                select(func.count())
                .select_from(ExternalReference)
                .join(
                    ExternalIdentity,
                    ExternalIdentity.external_identity_id
                    == ExternalReference.external_identity_id,
                )
                .where(ExternalIdentity.native_id == native_id)
            )
            == 1
        )
        assert (
            verify.scalar(
                select(func.count())
                .select_from(ScientificObjectRevision)
                .where(ScientificObjectRevision.series_id == outcomes["a"][0])
            )
            == 1
        )
        assert (
            verify.scalar(
                select(func.count())
                .select_from(ArtifactReference)
                .where(ArtifactReference.artifact_id == outcomes["a"][2])
            )
            == 1
        )
    assert FAKE_STRUCTURE_AUTHORITY


def test_phase15_postgres_content_only_race_yields_one_artifact(
    pg_engine: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Two DIFFERENT PDB identities with byte-identical coordinates.

    The identity constraint cannot serialize this case, so the internal
    content-addressed ArtifactReference get-or-create is the only linearization
    point. It must converge on ONE artifact with no raw uniqueness failure and
    without aborting either import.
    """
    from revolab.content_store import ContentStore
    from revolab.domain import provenance as provenance_module
    from revolab.models import (
        ArtifactReference,
        GlobalProvenanceEdge,
        GlobalResourceRegistry,
    )

    tag = uuid4().hex[:8]
    shared_seed = f"shared-{tag}"
    with Session(pg_engine) as setup:
        actor = services.create_actor(setup)
        project = services.create_project(setup, actor, f"Phase15 content race {tag}")
        project_id = project.id
        actor_id = actor

    registry = _structure_registry()
    # Two distinct durable identities, IDENTICAL coordinate bytes.
    native_a = f"P15A{tag}"[:12]
    native_b = f"P15B{tag}"[:12]
    _seed_structure(registry, native_a, coordinate_seed=shared_seed)
    _seed_structure(registry, native_b, coordinate_seed=shared_seed)
    content_store = ContentStore(tmp_path / "content")

    # The acceptance database is shared and is NOT truncated, so the lost-race
    # registry cleanup is proven RELATIVELY: the race must add exactly ONE
    # `artifact_reference` registry row (the winner's), never one per racer.
    with Session(pg_engine) as pre:
        registry_rows_before = int(
            pre.scalar(
                select(func.count())
                .select_from(GlobalResourceRegistry)
                .where(GlobalResourceRegistry.resource_kind == "artifact_reference")
            )
            or 0
        )

    barrier = threading.Barrier(2)
    real_find = provenance_module.find_artifact_reference
    local = threading.local()

    def synced_find(session, authority, native_id, version_id):
        found = real_find(session, authority, native_id, version_id)
        if (
            found is None
            and authority == "revolab"
            and not getattr(local, "synced", False)
        ):
            local.synced = True
            barrier.wait(timeout=10)
        return found

    monkeypatch.setattr(provenance_module, "find_artifact_reference", synced_find)

    outcomes: dict[str, tuple] = {}
    errors: dict[str, BaseException] = {}
    lock = threading.Lock()

    def worker(name: str, native_id: str) -> None:
        try:
            with Session(pg_engine) as session:
                result = _import_structure(
                    session, registry, content_store, actor_id, project_id, native_id
                )
                with lock:
                    outcomes[name] = _structure_bundle_tuple(result)
        except BaseException as exc:
            with lock:
                errors[name] = exc

    threads = [
        threading.Thread(target=worker, args=("a", native_a)),
        threading.Thread(target=worker, args=("b", native_b)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    assert errors == {}
    assert len(outcomes) == 2
    # Two DISTINCT structures (different identities)...
    assert outcomes["a"][0] != outcomes["b"][0]
    # ...sharing exactly ONE internal ArtifactReference.
    assert outcomes["a"][2] == outcomes["b"][2]
    with Session(pg_engine) as verify:
        assert (
            verify.scalar(select(func.count()).select_from(ArtifactReference)) >= 1
        )
        assert (
            verify.scalar(
                select(func.count())
                .select_from(ArtifactReference)
                .where(ArtifactReference.artifact_id == outcomes["a"][2])
            )
            == 1
        )
        # The SAME artifact is imported as BOTH revisions, and no registry row was
        # orphaned by the losing insert-if-absent.
        artifact_edges = list(
            verify.scalars(
                select(GlobalProvenanceEdge).where(
                    GlobalProvenanceEdge.relation_type == "imported_as",
                    GlobalProvenanceEdge.source_id == outcomes["a"][2],
                )
            )
        )
        assert {edge.target_id for edge in artifact_edges} == {
            outcomes["a"][1],
            outcomes["b"][1],
        }
        registry_rows_after = int(
            verify.scalar(
                select(func.count())
                .select_from(GlobalResourceRegistry)
                .where(GlobalResourceRegistry.resource_kind == "artifact_reference")
            )
            or 0
        )
        assert registry_rows_after == registry_rows_before + 1
        # The winner's own registry row is present exactly once.
        assert (
            verify.scalar(
                select(func.count())
                .select_from(GlobalResourceRegistry)
                .where(GlobalResourceRegistry.resource_id == outcomes["a"][2])
            )
            == 1
        )


def test_phase15_postgres_atomic_rollback_leaves_no_partial_bundle(
    pg_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from revolab import services as services_module
    from revolab.content_store import ContentStore
    from revolab.models import ExternalIdentity, ProjectResourceLink

    tag = uuid4().hex[:8]
    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, f"Phase15 rollback {tag}")
    native_id = f"P15{tag}"[:12]
    registry = _structure_registry()
    _seed_structure(registry, native_id)
    content_store = ContentStore(tmp_path / "content")

    def explode(*args, **kwargs):
        raise RuntimeError("injected failure after the bundle was staged")

    monkeypatch.setattr(services_module, "record_imported_as", explode)
    try:
        _import_structure(pg_session, registry, content_store, actor, project.id, native_id)
    except RuntimeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("the injected failure did not propagate")
    pg_session.rollback()

    with Session(pg_session.get_bind()) as verify:
        assert (
            verify.scalar(
                select(func.count())
                .select_from(ExternalIdentity)
                .where(ExternalIdentity.native_id == native_id)
            )
            == 0
        )
        assert (
            verify.scalar(
                select(func.count())
                .select_from(ProjectResourceLink)
                .where(ProjectResourceLink.project_id == project.id)
            )
            == 0
        )
    # The ContentStore is NOT transactional: an unreachable content-addressed blob
    # may remain. It is immutable, content-addressed, and not Project truth.
    assert content_store is not None


def test_phase15_postgres_changed_snapshot_fails_closed(pg_session: Session, tmp_path) -> None:
    from revolab.content_store import ContentStore
    from revolab.domain.errors import ConflictError

    tag = uuid4().hex[:8]
    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, f"Phase15 changed {tag}")
    native_id = f"P15{tag}"[:12]
    registry = _structure_registry()
    seed = _seed_structure(registry, native_id)
    content_store = ContentStore(tmp_path / "content")

    first = _import_structure(pg_session, registry, content_store, actor, project.id, native_id)
    before = _structure_bundle_counts(pg_session)
    import dataclasses

    handle = registry.get(seed.provider_key)
    handle.driver.state.seed(dataclasses.replace(seed, resolution_angstrom=9.0))
    with pytest.raises(ConflictError, match="changed since the imported snapshot"):
        _import_structure(pg_session, registry, content_store, actor, project.id, native_id)
    assert _structure_bundle_counts(pg_session) == before
    assert first.structure_revision_id is not None


def test_phase15_postgres_incomplete_mapping_fails_closed(
    pg_session: Session, tmp_path
) -> None:
    from revolab.content_store import ContentStore
    from revolab.domain.errors import ConflictError
    from revolab.models import ScientificObjectSeries

    tag = uuid4().hex[:8]
    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, f"Phase15 incomplete {tag}")
    native_id = f"P15{tag}"[:12]
    registry = _structure_registry()
    _seed_structure(registry, native_id)
    content_store = ContentStore(tmp_path / "content")

    result = _import_structure(pg_session, registry, content_store, actor, project.id, native_id)
    series = pg_session.get(ScientificObjectSeries, result.structure_series_id)
    assert series is not None
    series.object_type = "protein"
    pg_session.commit()
    before = _structure_bundle_counts(pg_session)
    with pytest.raises(ConflictError, match="incomplete mapping"):
        _import_structure(pg_session, registry, content_store, actor, project.id, native_id)
    assert _structure_bundle_counts(pg_session) == before


def test_phase15_postgres_project_search_integration(pg_session: Session, tmp_path) -> None:
    from revolab import search as search_service
    from revolab.content_store import ContentStore
    from revolab.enums import SearchScope

    tag = uuid4().hex[:8]
    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, f"Phase15 search {tag}")
    native_id = f"P15{tag}"[:12]
    registry = _structure_registry()
    _seed_structure(registry, native_id)
    content_store = ContentStore(tmp_path / "content")
    result = _import_structure(pg_session, registry, content_store, actor, project.id, native_id)

    hits = search_service.search(
        pg_session, actor, project.id, query=native_id, scope=SearchScope.PROJECT_SHARED
    ).hits
    assert result.structure_series_id in {hit.target_id for hit in hits}


def test_phase15_postgres_viewer_cannot_import(pg_session: Session, tmp_path) -> None:
    from revolab.content_store import ContentStore
    from revolab.domain.errors import AuthorizationError
    from revolab.enums import Role
    from revolab.models import ExternalIdentity

    tag = uuid4().hex[:8]
    owner = services.create_actor(pg_session)
    viewer = services.create_actor(pg_session)
    project = services.create_project(pg_session, owner, f"Phase15 authz {tag}")
    services.add_membership(pg_session, owner, project.id, viewer, Role.VIEWER)
    native_id = f"P15{tag}"[:12]
    registry = _structure_registry()
    _seed_structure(registry, native_id)
    content_store = ContentStore(tmp_path / "content")

    with pytest.raises(AuthorizationError):
        _import_structure(pg_session, registry, content_store, viewer, project.id, native_id)
    assert (
        pg_session.scalar(
            select(func.count())
            .select_from(ExternalIdentity)
            .where(ExternalIdentity.native_id == native_id)
        )
        == 0
    )
