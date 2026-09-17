"""Phase-15 Agent-facing structure Tool tests (TODO.md sections 52-54, 79).

The Agent may AUTONOMOUSLY cross the remote READ-ONLY PDB structure discovery
boundary, but it must never import, never take coordinate byte custody, never
create ExternalIdentity/ExternalReference/ArtifactReference/ScientificObjects,
never create Evidence, and never let external provider text change its authority.
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
from revolab.domain.errors import ValidationError
from revolab.drivers import DriverContext, DriverRegistry
from revolab.enums import (
    AgentTerminationReason,
    AgentToolAutonomy,
    AgentToolCallStatus,
    CapabilityKind,
    Role,
    ToolExecutionClass,
    ToolResultKind,
    ToolSideEffectClass,
    ToolSource,
)
from revolab.models import (
    ActionRequest,
    ArtifactReference,
    Evidence,
    ExternalIdentity,
    ExternalReference,
    RunReference,
    ScientificObjectRevision,
    ScientificObjectSeries,
    ToolInvocation,
)
from revolab.schemas import ToolInvocationCreate
from revolab.secret_store import InMemorySecretStore
from revolab.testing.fake_structure import (
    FAKE_STRUCTURE_AUTHORITY,
    FAKE_STRUCTURE_PROVIDER_KEY,
    HOSTILE_QUERY_TOKEN,
    UNAVAILABLE_QUERY_TOKEN,
    FakeStructureDriver,
)
from revolab.tools import build_tool_catalog
from revolab.tools.registry import build_default_registry
from revolab.tools.runtime import LocalToolRuntime

TOOL_ID = f"{FAKE_STRUCTURE_PROVIDER_KEY}.structure.search"


def _actor(session):
    return services.create_actor(session)


def _project(session, actor, name="Structure Agent"):
    return services.create_project(session, actor, name)


def _registry(*drivers) -> DriverRegistry:
    registry = DriverRegistry()
    for driver in drivers:
        registry.register(driver)
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return registry


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


def _count(session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _assert_nothing_persisted(session) -> None:
    """The Agent structure search writes NOTHING and downloads no coordinates."""
    assert _count(session, ExternalIdentity) == 0
    assert _count(session, ExternalReference) == 0
    assert _count(session, ArtifactReference) == 0
    assert _count(session, ScientificObjectSeries) == 0
    assert _count(session, ScientificObjectRevision) == 0
    assert _count(session, Evidence) == 0
    assert _count(session, RunReference) == 0
    assert _count(session, ActionRequest) == 0
    # A remote read records no local ToolInvocation activity row either.
    assert _count(session, ToolInvocation) == 0


# ---------------------------------------------------------------------------
# Catalog semantics
# ---------------------------------------------------------------------------


def test_structure_tool_descriptor_is_remote_read_only_automatic(session):
    actor = _actor(session)
    project = _project(session, actor)
    catalog = build_tool_catalog(session, actor, project.id, _registry(FakeStructureDriver()))
    tool = next(t for t in catalog.tools if t.id == TOOL_ID)

    assert tool.source is ToolSource.PROVIDER
    assert tool.execution_class is ToolExecutionClass.REMOTE
    assert tool.side_effect_class is ToolSideEffectClass.READ_ONLY
    assert tool.autonomy is AgentToolAutonomy.AUTOMATIC
    assert tool.capability_kind is CapabilityKind.STRUCTURE_DISCOVERY
    assert tool.provider_key == FAKE_STRUCTURE_PROVIDER_KEY
    properties = tool.input_schema["properties"]
    assert set(properties) == {"query", "limit"}
    assert tool.input_schema.get("additionalProperties") is False
    # No URL/host/endpoint/import/coordinate field exists anywhere in the input.
    assert not {"url", "host", "endpoint", "graphql", "import", "coordinates"} & set(properties)
    output_properties = tool.output_schema["$defs"]["StructureCandidateRead"]["properties"]
    assert set(output_properties) == {
        "provider_key",
        "authority",
        "native_id",
        "title",
        "experimental_methods",
        "resolution_angstrom",
        "release_date",
        "polymer_entity_count",
    }


def test_no_agent_structure_import_tool_exists(session):
    actor = _actor(session)
    project = _project(session, actor)
    catalog = build_tool_catalog(session, actor, project.id, _registry(FakeStructureDriver()))
    assert not [t for t in catalog.tools if "import" in t.id]
    structure_tools = [
        t for t in catalog.tools if t.capability_kind is CapabilityKind.STRUCTURE_DISCOVERY
    ]
    assert [t.id for t in structure_tools] == [TOOL_ID]


def test_viewer_is_offered_the_read_only_structure_tool(session):
    owner = _actor(session)
    viewer = services.create_actor(session)
    project = _project(session, owner)
    services.add_membership(session, owner, project.id, viewer, Role.VIEWER)
    catalog = build_tool_catalog(session, viewer, project.id, _registry(FakeStructureDriver()))
    assert TOOL_ID in {t.id for t in catalog.tools}


def test_unavailable_provider_projects_no_structure_tool(session):
    actor = _actor(session)
    project = _project(session, actor)
    catalog = build_tool_catalog(
        session, actor, project.id, _registry(FakeStructureDriver(healthy=False))
    )
    assert TOOL_ID not in {t.id for t in catalog.tools}


# ---------------------------------------------------------------------------
# The Agent may search, and persists nothing
# ---------------------------------------------------------------------------


def test_agent_searches_external_structures_and_persists_nothing(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(FakeStructureDriver())
    model = _Scripted(
        [
            _tool_call(TOOL_ID, {"query": "human hemoglobin", "limit": 3}),
            _final(
                "Candidate: "
                f"{FAKE_STRUCTURE_AUTHORITY}:<entry> (external, not yet in Project). "
                "A human must explicitly import it."
            ),
        ]
    )
    runner = _runner(
        session, registry, InMemorySecretStore(), ContentStore(tmp_path), model=model
    )
    turn = runner.run(session, actor, project.id, "Find the crystal structure of hemoglobin.")

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
    assert value["provider_key"] == FAKE_STRUCTURE_PROVIDER_KEY
    assert value["candidates"]
    for candidate in value["candidates"]:
        assert candidate["authority"] == FAKE_STRUCTURE_AUTHORITY
        assert candidate["native_id"]
        # A candidate never carries coordinate bytes.
        assert "coordinate_bytes" not in candidate
        assert "coordinates" not in candidate
    assert turn.pending_actions == []
    _assert_nothing_persisted(session)


def test_the_agent_tool_result_stays_bounded(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(FakeStructureDriver())
    bounds = AgentLoopBounds(max_tool_result_chars=600)
    model = _Scripted([_tool_call(TOOL_ID, {"query": "kinase", "limit": 5}), _final()])
    runner = _runner(
        session, registry, InMemorySecretStore(), ContentStore(tmp_path), model=model, bounds=bounds
    )
    turn = runner.run(session, actor, project.id, "Find a kinase structure.")
    assert len(json.dumps(turn.tool_trace[0].result.value)) < 20_000


def test_agent_tool_rejects_invalid_arguments_without_touching_the_provider(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(FakeStructureDriver())
    model = _Scripted([_tool_call(TOOL_ID, {"query": "", "limit": 0}), _final()])
    runner = _runner(
        session, registry, InMemorySecretStore(), ContentStore(tmp_path), model=model
    )
    turn = runner.run(session, actor, project.id, "bad input")
    assert turn.tool_trace[0].status is AgentToolCallStatus.FAILED
    _assert_nothing_persisted(session)


def test_agent_tool_reports_a_provider_failure_cleanly(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(FakeStructureDriver())
    model = _Scripted(
        [_tool_call(TOOL_ID, {"query": f"{UNAVAILABLE_QUERY_TOKEN} thing"}), _final()]
    )
    runner = _runner(
        session, registry, InMemorySecretStore(), ContentStore(tmp_path), model=model
    )
    turn = runner.run(session, actor, project.id, "provider outage")
    assert turn.tool_trace[0].status is AgentToolCallStatus.FAILED
    _assert_nothing_persisted(session)


def test_hostile_provider_text_cannot_change_authority_or_tool_catalog(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(FakeStructureDriver())
    catalog_before = {
        t.id for t in build_tool_catalog(session, actor, project.id, registry).tools
    }
    model = _Scripted(
        [_tool_call(TOOL_ID, {"query": f"{HOSTILE_QUERY_TOKEN} title"}), _final()]
    )
    runner = _runner(
        session, registry, InMemorySecretStore(), ContentStore(tmp_path), model=model
    )
    turn = runner.run(session, actor, project.id, "hostile title")
    assert turn.tool_trace[0].status is AgentToolCallStatus.COMPLETED
    # The hostile title is ordinary untrusted data: no import, no authority change,
    # no wider ToolCatalog, no Evidence, no ActionRequest.
    catalog_after = {
        t.id for t in build_tool_catalog(session, actor, project.id, registry).tools
    }
    assert catalog_after == catalog_before
    assert turn.pending_actions == []
    _assert_nothing_persisted(session)


def test_the_structure_tool_is_not_executed_by_the_local_runtime(session, tmp_path):
    from revolab.tools.runtime import LocalToolRuntime
    from revolab.tools.types import InvocationContext

    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(FakeStructureDriver())
    runtime = LocalToolRuntime(build_default_registry())
    context = InvocationContext(
        session=session,
        actor_id=actor,
        project_id=project.id,
        registry=registry,
        secret_store=InMemorySecretStore(),
        content_store=ContentStore(tmp_path),
    )
    # A remote Tool is not a registered LOCAL tool: the closed local runtime fails
    # closed rather than dispatching it.
    with pytest.raises(ValidationError):
        runtime.invoke(context, ToolInvocationCreate(tool_id=TOOL_ID, input={"query": "kinase"}))
    _assert_nothing_persisted(session)
