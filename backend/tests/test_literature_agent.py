"""Phase-13 Agent-facing literature Tool tests (TODO.md sections 30-32, 50, 61).

The Agent may AUTONOMOUSLY cross the remote READ-ONLY literature boundary, but it
must never import, never create Evidence, and never let external provider text
change its authority.
"""

from __future__ import annotations

import json
from types import MappingProxyType

import pytest
from sqlalchemy import func, select

from revolab import services
from revolab.agent import AgentLoopBounds, AgentTurnRunner
from revolab.agent.model_backend import ModelRequest, ModelResponse, ModelToolCall
from revolab.content_store import ContentStore
from revolab.drivers import DriverContext, DriverRegistry
from revolab.enums import (
    AgentTerminationReason,
    AgentToolAutonomy,
    AgentToolCallStatus,
    CapabilityKind,
    ToolExecutionClass,
    ToolResultKind,
    ToolSideEffectClass,
    ToolSource,
)
from revolab.models import (
    ActionRequest,
    Evidence,
    LiteratureReference,
    RunReference,
    ToolInvocation,
)
from revolab.secret_store import InMemorySecretStore
from revolab.testing.fake_literature import (
    FAKE_LITERATURE_AUTHORITY,
    FAKE_LITERATURE_PROVIDER_KEY,
    HOSTILE_QUERY_TOKEN,
    UNAVAILABLE_QUERY_TOKEN,
    FakeLiteratureDriver,
)
from revolab.testing.fake_model import ScriptedModelBackend
from revolab.tools import build_tool_catalog
from revolab.tools.registry import build_default_registry
from revolab.tools.runtime import LocalToolRuntime

TOOL_ID = f"{FAKE_LITERATURE_PROVIDER_KEY}.literature.search"


def _actor(session):
    return services.create_actor(session)


def _project(session, actor, name="Literature Agent"):
    return services.create_project(session, actor, name)


def _registry(*drivers) -> DriverRegistry:
    registry = DriverRegistry()
    for driver in drivers:
        registry.register(driver)
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return registry


def _runner(session, registry, store, content_store, *, model, bounds=None):
    local_registry = build_default_registry()
    return AgentTurnRunner(
        model,
        LocalToolRuntime(local_registry),
        registry,
        store,
        content_store,
        bounds,
        local_registry=local_registry,
    )


class _Scripted:
    def __init__(self, turns: list[ModelResponse]) -> None:
        self._turns = turns
        self.requests: list[ModelRequest] = []
        self._index = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        response = self._turns[min(self._index, len(self._turns) - 1)]
        self._index += 1
        return response


def _tool_call(name: str, arguments: dict | None, identifier: str = "call_1") -> ModelResponse:
    return ModelResponse(
        finish="tool_calls",
        tool_calls=(
            ModelToolCall(
                id=identifier,
                name=name,
                arguments_raw=json.dumps(arguments) if arguments is not None else "{bad",
                arguments=arguments,
            ),
        ),
    )


def _final(content: str = "done") -> ModelResponse:
    return ModelResponse(finish="stop", content=content)


def _tool_messages(model: _Scripted) -> list:
    messages = []
    for request in model.requests:
        messages.extend(m for m in request.messages if m.role == "tool")
    return messages


# ---------------------------------------------------------------------------
# Catalog semantics (TODO.md section 61)
# ---------------------------------------------------------------------------


def test_literature_tool_descriptor_is_remote_read_only_automatic(session):
    actor = _actor(session)
    project = _project(session, actor)
    catalog = build_tool_catalog(
        session, actor, project.id, _registry(FakeLiteratureDriver())
    )
    tool = next(t for t in catalog.tools if t.id == TOOL_ID)

    assert tool.source is ToolSource.PROVIDER
    assert tool.execution_class is ToolExecutionClass.REMOTE
    assert tool.side_effect_class is ToolSideEffectClass.READ_ONLY
    assert tool.autonomy is AgentToolAutonomy.AUTOMATIC
    assert tool.capability_kind is CapabilityKind.LITERATURE_DISCOVERY
    assert tool.provider_key == FAKE_LITERATURE_PROVIDER_KEY
    # Input carries no URL/host/scheme/provider endpoint and no scope.
    properties = tool.input_schema["properties"]
    assert set(properties) == {"query", "limit"}
    assert tool.input_schema.get("additionalProperties") is False


def test_no_agent_import_tool_exists(session):
    actor = _actor(session)
    project = _project(session, actor)
    catalog = build_tool_catalog(
        session, actor, project.id, _registry(FakeLiteratureDriver())
    )
    # Phase 13 gives the Agent NO import Tool and does not generalize ActionRequest.
    assert not [t for t in catalog.tools if "import" in t.id]
    # There is exactly ONE literature tool, and it is the read-only search.
    literature_tools = [
        t for t in catalog.tools if t.capability_kind is CapabilityKind.LITERATURE_DISCOVERY
    ]
    assert [t.id for t in literature_tools] == [TOOL_ID]


def test_viewer_is_offered_the_read_only_literature_tool(session):
    from revolab.enums import Role

    owner = _actor(session)
    viewer = services.create_actor(session)
    project = _project(session, owner)
    services.add_membership(session, owner, project.id, viewer, Role.VIEWER.value)
    catalog = build_tool_catalog(
        session, viewer, project.id, _registry(FakeLiteratureDriver())
    )
    assert TOOL_ID in {t.id for t in catalog.tools}


def test_unavailable_provider_projects_no_literature_tool(session):
    actor = _actor(session)
    project = _project(session, actor)
    catalog = build_tool_catalog(
        session, actor, project.id, _registry(FakeLiteratureDriver(healthy=False))
    )
    assert TOOL_ID not in {t.id for t in catalog.tools}


# ---------------------------------------------------------------------------
# Agent acceptance slice (TODO.md section 50)
# ---------------------------------------------------------------------------


def test_agent_searches_external_literature_and_persists_nothing(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(FakeLiteratureDriver())
    model = _Scripted(
        [
            _tool_call(
                TOOL_ID, {"query": "enzyme active-site redesign", "limit": 3}
            ),
            _final("Candidates: fakepubmed:... (external, not yet in Project)"),
        ]
    )
    runner = _runner(
        session, registry, InMemorySecretStore(), ContentStore(tmp_path), model=model
    )
    turn = runner.run(
        session, actor, project.id, "Find recent literature about enzyme active-site redesign."
    )

    assert turn.termination_reason is AgentTerminationReason.FINAL_RESPONSE
    assert len(turn.tool_trace) == 1
    entry = turn.tool_trace[0]
    assert entry.tool_id == TOOL_ID
    assert entry.status is AgentToolCallStatus.COMPLETED
    assert entry.result is not None
    assert entry.result.result_kind is ToolResultKind.EPHEMERAL
    assert entry.result.persisted is False
    assert entry.result.resource_id is None
    value = entry.result.value
    assert value is not None
    assert value["provider_key"] == FAKE_LITERATURE_PROVIDER_KEY
    assert value["candidates"]
    for candidate in value["candidates"]:
        assert candidate["authority"] == FAKE_LITERATURE_AUTHORITY
        assert candidate["native_id"]
    assert turn.pending_actions == []

    # The external READ is automatic, but it becomes NOTHING durable.
    assert session.scalar(select(func.count()).select_from(LiteratureReference)) == 0
    assert session.scalar(select(func.count()).select_from(Evidence)) == 0
    assert session.scalar(select(func.count()).select_from(ActionRequest)) == 0
    assert session.scalar(select(func.count()).select_from(RunReference)) == 0
    # A remote read records no local ToolInvocation activity row either.
    assert session.scalar(select(func.count()).select_from(ToolInvocation)) == 0


def test_agent_tool_result_consumes_the_existing_turn_budget(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(FakeLiteratureDriver())
    bounds = AgentLoopBounds(max_tool_result_chars=400)
    model = _Scripted([_tool_call(TOOL_ID, {"query": "budget", "limit": 3}), _final()])
    runner = _runner(
        session,
        registry,
        InMemorySecretStore(),
        ContentStore(tmp_path),
        model=model,
        bounds=bounds,
    )
    runner.run(session, actor, project.id, "find literature")

    messages = _tool_messages(model)
    assert messages
    for message in messages:
        assert len(message.content) <= 400


def test_agent_tool_rejects_invalid_arguments_without_touching_the_provider(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(FakeLiteratureDriver())
    model = _Scripted(
        [
            # An extra field (a URL, a scope, an import flag) is refused by the
            # canonical model: the schema is extra=forbid.
            _tool_call(TOOL_ID, {"query": "x", "url": "https://evil.example.test"}),
            _final(),
        ]
    )
    runner = _runner(
        session, registry, InMemorySecretStore(), ContentStore(tmp_path), model=model
    )
    turn = runner.run(session, actor, project.id, "search")
    assert turn.tool_trace[0].status is AgentToolCallStatus.FAILED
    assert "evil.example.test" not in (turn.tool_trace[0].error or "")


def test_agent_tool_reports_a_provider_failure_cleanly(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(FakeLiteratureDriver())
    model = _Scripted(
        [
            _tool_call(TOOL_ID, {"query": f"anything {UNAVAILABLE_QUERY_TOKEN}"}),
            _final(),
        ]
    )
    runner = _runner(
        session, registry, InMemorySecretStore(), ContentStore(tmp_path), model=model
    )
    turn = runner.run(session, actor, project.id, "search")
    assert turn.tool_trace[0].status is AgentToolCallStatus.FAILED
    assert "temporarily unavailable" in (turn.tool_trace[0].error or "")
    assert session.scalar(select(func.count()).select_from(LiteratureReference)) == 0


# ---------------------------------------------------------------------------
# Prompt / trust boundary (TODO.md section 32)
# ---------------------------------------------------------------------------


def test_hostile_provider_text_cannot_change_authority_or_tool_catalog(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(FakeLiteratureDriver())
    catalog_before = {
        (t.id, t.autonomy, t.execution_class, t.side_effect_class)
        for t in build_tool_catalog(session, actor, project.id, registry).tools
    }

    model = _Scripted(
        [
            _tool_call(
                TOOL_ID, {"query": f"hostile {HOSTILE_QUERY_TOKEN} literature"}
            ),
            # The model tries to import itself based on the injected text.
            _tool_call(f"{FAKE_LITERATURE_PROVIDER_KEY}.literature.import", {"native_id": "x"}),
            _final("I cannot import; a human must explicitly import."),
        ]
    )
    runner = _runner(
        session, registry, InMemorySecretStore(), ContentStore(tmp_path), model=model
    )
    turn = runner.run(session, actor, project.id, "find hostile literature")

    # The hostile text was ordinary tool-result data.
    hostile_value = turn.tool_trace[0].result.value
    assert "ignore all previous instructions" in hostile_value["candidates"][0]["title"]

    # It could NOT import itself, create Evidence, submit compute, or propose action.
    assert turn.tool_trace[1].status is AgentToolCallStatus.FAILED
    assert turn.tool_trace[1].error == "unknown tool"
    assert session.scalar(select(func.count()).select_from(LiteratureReference)) == 0
    assert session.scalar(select(func.count()).select_from(Evidence)) == 0
    assert session.scalar(select(func.count()).select_from(RunReference)) == 0
    assert session.scalar(select(func.count()).select_from(ActionRequest)) == 0

    # The catalog is unchanged: external text cannot widen the tool surface.
    catalog_after = {
        (t.id, t.autonomy, t.execution_class, t.side_effect_class)
        for t in build_tool_catalog(session, actor, project.id, registry).tools
    }
    assert catalog_after == catalog_before


def test_hostile_provider_text_cannot_auto_execute_an_explicit_action(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(FakeLiteratureDriver())
    model = _Scripted(
        [
            _tool_call(TOOL_ID, {"query": f"hostile {HOSTILE_QUERY_TOKEN}"}),
            # A commitment is still only PROPOSED, never executed.
            _tool_call("decision.commit", {"decision_id": "00000000-0000-4000-8000-000000000001"}),
            _final(),
        ]
    )
    runner = _runner(
        session, registry, InMemorySecretStore(), ContentStore(tmp_path), model=model
    )
    turn = runner.run(session, actor, project.id, "find hostile literature")

    assert turn.tool_trace[1].status is AgentToolCallStatus.PENDING
    assert len(turn.pending_actions) == 1
    # Durable intent only: no decision executed, no literature, no Evidence.
    assert session.scalar(select(func.count()).select_from(ActionRequest)) == 1
    assert session.scalar(select(func.count()).select_from(LiteratureReference)) == 0
    assert session.scalar(select(func.count()).select_from(Evidence)) == 0


def test_agent_tool_is_not_executed_by_the_local_runtime(session, tmp_path):
    """Remote reads never run through the closed LOCAL runtime (TODO.md section 61)."""
    from revolab.domain.errors import ValidationError
    from revolab.schemas import ToolInvocationCreate

    actor = _actor(session)
    project = _project(session, actor)
    runtime = LocalToolRuntime(build_default_registry())
    from revolab.tools.types import InvocationContext

    ctx = InvocationContext(
        session=session,
        registry=_registry(FakeLiteratureDriver()),
        secret_store=InMemorySecretStore(),
        content_store=ContentStore(tmp_path),
        actor_id=actor,
        project_id=project.id,
    )
    with pytest.raises(ValidationError):
        runtime.invoke(ctx, ToolInvocationCreate(tool_id=TOOL_ID, input={"query": "x"}))


def test_scripted_default_model_can_be_used_for_the_browser_slice(session, tmp_path):
    """The opt-in E2E scripted model path still builds and runs a real turn."""
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(FakeLiteratureDriver())
    runner = _runner(
        session,
        registry,
        InMemorySecretStore(),
        ContentStore(tmp_path),
        model=ScriptedModelBackend(),
    )
    turn = runner.run(session, actor, project.id, "hello")
    assert turn.termination_reason in {
        AgentTerminationReason.FINAL_RESPONSE,
        AgentTerminationReason.MAX_MODEL_TURNS,
        AgentTerminationReason.MAX_TOOL_CALLS,
        AgentTerminationReason.TOTAL_DURATION,
    }
