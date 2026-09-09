"""Phase-7 Project Tool Harness tests: closed local runtime, catalog projection,
local analysis tools, result semantics, authority, and the REvoCompute-produced
artifact analyzed by a local REvoLab Tool.
"""

from __future__ import annotations

from types import MappingProxyType

import pytest
from sqlalchemy import select

from revolab import services
from revolab.content_store import ContentStore
from revolab.domain.errors import AuthorizationError, ValidationError
from revolab.drivers import DriverContext, DriverRegistry
from revolab.enums import (
    AgentToolAutonomy,
    Role,
    ToolExecutionClass,
    ToolResultKind,
    ToolSideEffectClass,
)
from revolab.models import ArtifactReference, ToolInvocation
from revolab.schemas import ToolInvocationCreate
from revolab.secret_store import InMemorySecretStore
from revolab.testing.fake_compute import FakeComputeDriver
from revolab.tools.registry import ALL_LOCAL_TOOLS, LocalToolRegistry, build_default_registry
from revolab.tools.runtime import LocalToolRuntime
from revolab.tools.types import InvocationContext

CSV = b"x,y,label\n1,2,a\n2,4,b\n3,6,a\n"


def _actor(session):
    return services.create_actor(session)


def _project(session, actor, name="Tools P"):
    return services.create_project(session, actor, name)


def _registry(*drivers) -> DriverRegistry:
    registry = DriverRegistry()
    for driver in drivers:
        registry.register(driver)
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return registry


def _make_ctx(session, registry, secrets, content_store, actor, project) -> InvocationContext:
    return InvocationContext(
        session=session,
        registry=registry,
        secret_store=secrets,
        content_store=content_store,
        actor_id=actor,
        project_id=project.id,
    )


def _upload(session, actor, project, content_store: ContentStore, data: bytes) -> ArtifactReference:
    return services.create_internal_artifact(
        session, actor, project.id, content_store, data, content_type="text/csv"
    )


def _runtime() -> LocalToolRuntime:
    return LocalToolRuntime(build_default_registry())


# ---------------------------------------------------------------------------
# Registry closedness
# ---------------------------------------------------------------------------


def test_registry_rejects_duplicate_tool_id():
    registry = LocalToolRegistry()
    registry.register(ALL_LOCAL_TOOLS[0])
    with pytest.raises(ValueError):
        registry.register(ALL_LOCAL_TOOLS[0])


def test_local_tool_set_is_closed_no_arbitrary_execution():
    ids = {spec.id for spec in ALL_LOCAL_TOOLS}
    assert ids == {
        "artifact.inspect",
        "decision.commit",
        "decision.record_draft",
        "evidence.create",
        "plot.xy",
        "table.describe",
        "table.select",
    }
    for tool_id in ids:
        for banned in ("eval", "exec", "shell", "sql", "http", "path", "url", "credential"):
            assert banned not in tool_id


def test_runtime_rejects_unknown_tool_ids(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    runtime = _runtime()
    ctx = _make_ctx(session, _registry(), InMemorySecretStore(), ContentStore(tmp_path), actor, project)
    for tool_id in ("python.eval", "shell.run", "sql.query", "file.read", "http.get"):
        with pytest.raises(ValidationError):
            runtime.invoke(ctx, ToolInvocationCreate(tool_id=tool_id, input={}))


# ---------------------------------------------------------------------------
# Catalog projection
# ---------------------------------------------------------------------------


def test_catalog_authority_matrix(session):
    owner = _actor(session)
    viewer = services.create_actor(session)
    project = _project(session, owner)
    services.add_membership(session, owner, project.id, viewer, Role.VIEWER.value)
    from revolab.tools import build_tool_catalog

    owner_catalog = build_tool_catalog(session, owner, project.id, _registry())
    by_id = {tool.id: tool for tool in owner_catalog.tools}
    assert by_id["table.describe"].available is True
    assert by_id["evidence.create"].available is True
    assert by_id["decision.commit"].available is True
    assert by_id["table.select"].execution_class is ToolExecutionClass.LOCAL
    assert by_id["evidence.create"].side_effect_class is ToolSideEffectClass.CREATES_PROJECT_TRUTH
    assert by_id["decision.commit"].autonomy is AgentToolAutonomy.EXPLICIT_ACTION

    viewer_catalog = build_tool_catalog(session, viewer, project.id, _registry())
    viewer_by_id = {tool.id: tool for tool in viewer_catalog.tools}
    assert viewer_by_id["table.describe"].available is True
    assert viewer_by_id["evidence.create"].available is False
    assert viewer_by_id["decision.commit"].available is False


def test_local_tools_independent_of_provider_credentials(session):
    actor = _actor(session)
    project = _project(session, actor)
    from revolab.tools import build_tool_catalog

    # A READY zero-credential provider projects remote tools; local tools remain.
    catalog = build_tool_catalog(session, actor, project.id, _registry(FakeComputeDriver()))
    ids = {tool.id for tool in catalog.tools}
    assert "table.describe" in ids
    assert "fakecompute.compute.submit" in ids
    # The remote compute tool is a remote execution class, never local.
    submit = next(tool for tool in catalog.tools if tool.id == "fakecompute.compute.submit")
    assert submit.execution_class is ToolExecutionClass.REMOTE

    # With no providers at all, the local tools are still exactly the fixed set.
    local_only = build_tool_catalog(session, actor, project.id, _registry())
    assert {tool.id for tool in local_only.tools} == {spec.id for spec in ALL_LOCAL_TOOLS}


# ---------------------------------------------------------------------------
# Local analysis execution
# ---------------------------------------------------------------------------


def _analysis_ctx(session, actor, project, tmp_path, *drivers):
    registry = _registry(*drivers)
    store = InMemorySecretStore()
    content_store = ContentStore(tmp_path)
    return _make_ctx(session, registry, store, content_store, actor, project)


def test_table_describe_typed_output(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    ctx = _analysis_ctx(session, actor, project, tmp_path)
    artifact = _upload(session, actor, project, ctx.content_store, CSV)

    result = _runtime().invoke(ctx, ToolInvocationCreate(tool_id="table.describe", input={"artifact_id": str(artifact.artifact_id)}))
    assert result.status == "completed"
    assert result.result_kind is ToolResultKind.EPHEMERAL
    assert result.persisted is False
    value = result.value or {}
    assert value["rows"] == 3
    assert value["columns"] == 3
    x_stats = next(c for c in value["columns_stats"] if c["column"] == "x")
    assert x_stats["numeric"] is True
    assert x_stats["mean"] == 2.0
    assert x_stats["min"] == 1.0
    assert x_stats["max"] == 3.0


def test_table_select_and_persist(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    ctx = _analysis_ctx(session, actor, project, tmp_path)
    artifact = _upload(session, actor, project, ctx.content_store, CSV)

    result = _runtime().invoke(
        ctx,
        ToolInvocationCreate(
            tool_id="table.select",
            input={"artifact_id": str(artifact.artifact_id), "columns": ["x", "y"], "limit": 10},
            persist=True,
        ),
    )
    assert result.result_kind is ToolResultKind.ARTIFACT
    assert result.persisted is True
    assert result.resource_id is not None

    invocation = session.scalar(select(ToolInvocation))
    assert invocation is not None
    assert invocation.tool_id == "table.select"
    assert invocation.tool_version == "1.0.0"
    assert invocation.input_resource_ids == [str(artifact.artifact_id)]
    assert invocation.result_resource_id == result.resource_id
    assert invocation.parameters["columns"] == ["x", "y"]

    derived = session.get(ArtifactReference, result.resource_id)
    assert derived is not None
    assert derived.authority == "revolab"
    assert derived.content_type == "text/csv"


def test_plot_xy_typed_specification(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    ctx = _analysis_ctx(session, actor, project, tmp_path)
    artifact = _upload(session, actor, project, ctx.content_store, CSV)

    result = _runtime().invoke(
        ctx,
        ToolInvocationCreate(
            tool_id="plot.xy",
            input={"artifact_id": str(artifact.artifact_id), "x_column": "x", "y_columns": ["y"], "title": "t"},
        ),
    )
    assert result.result_kind is ToolResultKind.EPHEMERAL
    value = result.value or {}
    assert value["kind"] == "xy"
    assert value["x_axis"] == "x"
    assert value["series"][0]["x"] == [1.0, 2.0, 3.0]
    assert value["series"][0]["y"] == [2.0, 4.0, 6.0]


def test_viewer_cannot_persist_derived_result(session, tmp_path):
    owner = _actor(session)
    viewer = services.create_actor(session)
    project = _project(session, owner)
    services.add_membership(session, owner, project.id, viewer, Role.VIEWER.value)
    ctx = _analysis_ctx(session, viewer, project, tmp_path)
    artifact = _upload(session, owner, project, ctx.content_store, CSV)

    # Read-only analysis is available to a viewer.
    described = _runtime().invoke(
        ctx, ToolInvocationCreate(tool_id="table.describe", input={"artifact_id": str(artifact.artifact_id)})
    )
    assert described.result_kind is ToolResultKind.EPHEMERAL

    # Persisting a derived result is a mutation: viewer authority fails closed.
    with pytest.raises(AuthorizationError):
        _runtime().invoke(
            ctx,
            ToolInvocationCreate(
                tool_id="table.select",
                input={"artifact_id": str(artifact.artifact_id), "columns": ["x"]},
                persist=True,
            ),
        )


def test_viewer_cannot_invoke_truth_tools(session, tmp_path):
    owner = _actor(session)
    viewer = services.create_actor(session)
    project = _project(session, owner)
    services.add_membership(session, owner, project.id, viewer, Role.VIEWER.value)
    ctx = _make_ctx(session, _registry(), InMemorySecretStore(), ContentStore(tmp_path), viewer, project)

    with pytest.raises(AuthorizationError):
        _runtime().invoke(
            ctx,
            ToolInvocationCreate(
                tool_id="evidence.create",
                input={
                    "kind": "computation",
                    "target_kind": "scientific_object_revision",
                    "target_id": "8e2dd4ce-0000-4000-8000-000000000000",
                },
            ),
        )


def test_schema_validation_rejects_bad_input(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    ctx = _analysis_ctx(session, actor, project, tmp_path)
    with pytest.raises(ValidationError):
        _runtime().invoke(ctx, ToolInvocationCreate(tool_id="table.select", input={"artifact_id": "not-a-uuid"}))


def test_unsupported_and_oversized_artifacts_fail_closed(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    ctx = _analysis_ctx(session, actor, project, tmp_path)

    binary = _upload(session, actor, project, ctx.content_store, b"\x00\xff\xfe\x00" * 8)
    with pytest.raises(ValidationError):
        _runtime().invoke(
            ctx, ToolInvocationCreate(tool_id="table.describe", input={"artifact_id": str(binary.artifact_id)})
        )

    oversized = _upload(session, actor, project, ctx.content_store, b"x\n" + b"1\n" * 600_000)
    with pytest.raises(ValidationError):
        _runtime().invoke(
            ctx,
            ToolInvocationCreate(tool_id="table.describe", input={"artifact_id": str(oversized.artifact_id)}),
        )


def test_table_describe_truncation_bound(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    ctx = _analysis_ctx(session, actor, project, tmp_path)
    artifact = _upload(session, actor, project, ctx.content_store, b"x\n" + b"1\n" * 10_500)
    result = _runtime().invoke(
        ctx, ToolInvocationCreate(tool_id="table.describe", input={"artifact_id": str(artifact.artifact_id)})
    )
    assert (result.value or {})["truncated"] is True
    assert (result.value or {})["rows"] == 10_000


# ---------------------------------------------------------------------------
# REvoCompute-produced artifact analyzed by a local REvoLab Tool
# ---------------------------------------------------------------------------


def test_revocompute_artifact_analyzed_locally(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(FakeComputeDriver())
    content_store = ContentStore(tmp_path)
    ctx = _make_ctx(session, registry, InMemorySecretStore(), content_store, actor, project)

    submitted = services.compute_submit(
        session,
        registry,
        InMemorySecretStore(),
        content_store,
        actor,
        project.id,
        "fakecompute",
        "tabular",
        [],
        {"rows": 4},
    )
    artifacts = services.compute_refresh_artifacts(
        session,
        registry,
        InMemorySecretStore(),
        actor,
        project.id,
        "fakecompute",
        submitted["run_resource_id"],
        submitted["native_id"],
    )
    assert len(artifacts) == 1
    artifact_id = artifacts[0]["resource_id"]

    result = _runtime().invoke(
        ctx, ToolInvocationCreate(tool_id="table.describe", input={"artifact_id": str(artifact_id)})
    )
    assert result.result_kind is ToolResultKind.EPHEMERAL
    value = result.value or {}
    assert value["rows"] == 4
    assert value["columns"] == 3
    assert value["columns_stats"][2]["column"] == "value"
    assert value["columns_stats"][2]["numeric"] is True


# ---------------------------------------------------------------------------
# HTTP slice: human and Agent share one catalog; invocation is typed
# ---------------------------------------------------------------------------


def _http_headers(actor_id: str) -> dict[str, str]:
    return {"X-Actor-Id": actor_id}


def test_project_tools_api_catalog_and_invoke(client):
    actor_id = client.post("/api/actors").json()["actor_id"]
    headers = _http_headers(actor_id)
    project = client.post("/api/projects", json={"name": "Tools API"}, headers=headers).json()
    pid = project["id"]

    uploaded = client.post(
        f"/api/projects/{pid}/artifacts",
        headers=headers,
        files={"file": ("data.csv", b"x,y\n1,2\n3,4\n", "text/csv")},
    )
    assert uploaded.status_code == 201
    artifact_id = uploaded.json()["resource_id"]

    catalog = client.get(f"/api/projects/{pid}/tools", headers=headers)
    assert catalog.status_code == 200
    by_id = {tool["id"]: tool for tool in catalog.json()["tools"]}
    assert by_id["table.describe"]["execution_class"] == "local"
    assert by_id["table.describe"]["available"] is True

    agent_catalog = client.get(f"/api/projects/{pid}/agent/tools", headers=headers)
    assert {tool["id"] for tool in agent_catalog.json()["tools"]} == set(by_id)

    described = client.post(
        f"/api/projects/{pid}/tools/invocations",
        json={"tool_id": "table.describe", "input": {"artifact_id": artifact_id}},
        headers=headers,
    )
    assert described.status_code == 201
    assert described.json()["result_kind"] == "ephemeral"
    assert described.json()["value"]["rows"] == 2

    selected = client.post(
        f"/api/projects/{pid}/tools/invocations",
        json={
            "tool_id": "table.select",
            "input": {"artifact_id": artifact_id, "columns": ["x", "y"]},
            "persist": True,
        },
        headers=headers,
    )
    assert selected.status_code == 201
    body = selected.json()
    assert body["result_kind"] == "artifact"
    assert body["persisted"] is True
    assert body["resource_id"]
