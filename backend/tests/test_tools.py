"""Phase-7 Project Tool Harness tests: closed local runtime, catalog projection,
local analysis tools, result semantics, authority, and the REvoCompute-produced
artifact analyzed by a local REvoLab Tool.
"""

from __future__ import annotations

import json
from types import MappingProxyType
from typing import ClassVar
from uuid import uuid4

import pytest
from sqlalchemy import select

from revolab import services
from revolab.capabilities import ArtifactHandle, CapabilityKind, ExternalArtifactRef
from revolab.content_store import ContentStore
from revolab.domain.errors import AuthorizationError, ValidationError
from revolab.drivers import DriverContext, DriverRegistry
from revolab.enums import (
    AgentToolAutonomy,
    ProviderRuntimeHealth,
    Role,
    ToolExecutionClass,
    ToolResultKind,
    ToolSideEffectClass,
)
from revolab.models import (
    ArtifactReference,
    GlobalProvenanceEdge,
    GlobalResourceRegistry,
    ScientificObjectRevision,
    ToolInvocation,
)
from revolab.schemas import TableDescribeCreate, TableDescribeRead, ToolInvocationCreate
from revolab.secret_store import InMemorySecretStore
from revolab.testing.fake_compute import FakeComputeDriver
from revolab.tools.registry import (
    ALL_LOCAL_TOOLS,
    LocalToolRegistry,
    LocalToolSpec,
    build_default_registry,
)
from revolab.tools.runtime import LocalToolRuntime
from revolab.tools.sources import MAX_ANALYSIS_BYTES
from revolab.tools.types import HandlerOutput, InvocationContext

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
    assert by_id["evidence.create"].side_effect_class is ToolSideEffectClass.DOMAIN_MUTATION
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


# ---------------------------------------------------------------------------
# Reviewer-driven regressions (five-reviewer reconciliation)
# ---------------------------------------------------------------------------

class _BigNoSizeCapability:
    provider_key = "bignosize"
    kind = CapabilityKind.ARTIFACT_RESOLUTION

    def resolve(self, artifact: ExternalArtifactRef, credentials: object) -> ArtifactHandle:
        return ArtifactHandle(
            authority="bignosize",
            native_id=artifact.native_id,
            version_id="",
            content_type="text/csv",
            size=None,
            checksum=None,
            data=b"x" * (MAX_ANALYSIS_BYTES + 10),
        )

    def preview(
        self,
        artifact: ExternalArtifactRef,
        credentials: object,
        *,
        offset: int = 0,
        limit: int,
    ) -> ArtifactHandle:
        # A well-behaved provider returns at most `limit` bytes; the "real"
        # artifact is much larger than the bound and its size is unknown.
        return ArtifactHandle(
            authority="bignosize",
            native_id=artifact.native_id,
            version_id="",
            content_type="text/csv",
            size=None,
            checksum=None,
            data=b"x" * limit,
        )


class _BigNoSizeDriver:
    name = "bignosize"
    display_name = "No-size Artifact Provider"
    description = "synthetic provider whose artifacts have no recorded size"
    required_credential_kinds = ()
    authorities = ("bignosize",)
    capabilities: ClassVar[dict[CapabilityKind, object]] = {
        CapabilityKind.ARTIFACT_RESOLUTION: _BigNoSizeCapability()
    }

    def start(self, context: DriverContext) -> None:
        pass

    def stop(self) -> None:
        pass

    def probe_health(self) -> ProviderRuntimeHealth:
        return ProviderRuntimeHealth.READY


class _CredComputeCapability:
    provider_key = "credtool"
    kind = CapabilityKind.COMPUTE


class _CredComputeDriver:
    name = "credtool"
    display_name = "Credentialed Tool Provider"
    description = "synthetic driver requiring a credential"
    required_credential_kinds = ("api_key",)
    authorities = ("credtool",)
    capabilities: ClassVar[dict[CapabilityKind, object]] = {CapabilityKind.COMPUTE: _CredComputeCapability()}

    def start(self, context: DriverContext) -> None:
        pass

    def stop(self) -> None:
        pass

    def probe_health(self) -> ProviderRuntimeHealth:
        return ProviderRuntimeHealth.READY


def test_remote_tool_id_rejected_by_local_runtime(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    ctx = _make_ctx(session, _registry(FakeComputeDriver()), InMemorySecretStore(), ContentStore(tmp_path), actor, project)
    with pytest.raises(ValidationError):
        _runtime().invoke(ctx, ToolInvocationCreate(tool_id="fakecompute.compute.submit", input={}))


def test_persist_rejected_for_non_derived_tools(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    ctx = _analysis_ctx(session, actor, project, tmp_path)
    artifact = _upload(session, actor, project, ctx.content_store, CSV)
    for tool_id in ("table.describe", "evidence.create"):
        with pytest.raises(ValidationError):
            _runtime().invoke(
                ctx,
                ToolInvocationCreate(tool_id=tool_id, input={"artifact_id": str(artifact.artifact_id)}, persist=True),
            )


def test_validated_parameters_persisted_only(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    ctx = _analysis_ctx(session, actor, project, tmp_path)
    artifact = _upload(session, actor, project, ctx.content_store, CSV)

    result = _runtime().invoke(
        ctx,
        ToolInvocationCreate(
            tool_id="table.select",
            input={"artifact_id": str(artifact.artifact_id), "columns": ["x", "y"], "extra": "DROP-SENTINEL"},
            persist=True,
        ),
    )
    assert result.persisted is True
    invocation = session.scalar(select(ToolInvocation))
    assert invocation is not None
    assert invocation.parameters == {
        "artifact_id": str(artifact.artifact_id),
        "columns": ["x", "y"],
        "filter_column": None,
        "filter_value": None,
        "limit": 50,
    }
    assert "DROP-SENTINEL" not in json.dumps(invocation.parameters)


def test_miswired_handler_output_rejected(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    ctx = _analysis_ctx(session, actor, project, tmp_path)

    registry = LocalToolRegistry()
    registry.register(
        LocalToolSpec(
            id="bad.table",
            name="Bad table",
            description="mis-wired for the test",
            autonomy=AgentToolAutonomy.AUTOMATIC,
            side_effect_class=ToolSideEffectClass.READ_ONLY,
            input_model=TableDescribeCreate,
            output_model=TableDescribeRead,
            requires_mutation=False,
            handler=lambda ctx, parsed, persist: HandlerOutput(
                kind=ToolResultKind.EPHEMERAL,
                value=parsed,  # wrong type on purpose: TableDescribeCreate != TableDescribeRead
            ),
        )
    )
    runtime = LocalToolRuntime(registry)
    with pytest.raises(RuntimeError):
        runtime.invoke(
            ctx,
            ToolInvocationCreate(tool_id="bad.table", input={"artifact_id": str(uuid4())}),
        )


def test_external_size_none_analysis_is_bounded(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(_BigNoSizeDriver())
    ctx = _make_ctx(session, registry, InMemorySecretStore(), ContentStore(tmp_path), actor, project)
    artifact = services.create_artifact_reference(
        session, actor, project.id, "bignosize", "big.csv", content_type="text/csv", size=None
    )
    with pytest.raises(ValidationError):
        _runtime().invoke(
            ctx,
            ToolInvocationCreate(tool_id="table.describe", input={"artifact_id": str(artifact.artifact_id)}),
        )


def test_derived_artifact_writes_no_provenance_edge(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    ctx = _analysis_ctx(session, actor, project, tmp_path)
    artifact = _upload(session, actor, project, ctx.content_store, CSV)

    result = _runtime().invoke(
        ctx,
        ToolInvocationCreate(
            tool_id="table.select",
            input={"artifact_id": str(artifact.artifact_id), "columns": ["x", "y"]},
            persist=True,
        ),
    )
    assert session.get(ArtifactReference, result.resource_id) is not None
    edges = session.scalars(
        select(GlobalProvenanceEdge).where(GlobalProvenanceEdge.target_id == result.resource_id)
    ).all()
    assert edges == []
    invocation = session.scalar(select(ToolInvocation))
    assert invocation is not None
    assert session.get(GlobalResourceRegistry, invocation.id) is None


def test_tombstone_preserves_derived_artifact_and_invocation(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    ctx = _analysis_ctx(session, actor, project, tmp_path)
    artifact = _upload(session, actor, project, ctx.content_store, CSV)
    result = _runtime().invoke(
        ctx,
        ToolInvocationCreate(
            tool_id="table.select",
            input={"artifact_id": str(artifact.artifact_id), "columns": ["x"]},
            persist=True,
        ),
    )
    services.delete_project(session, actor, project.id)
    assert session.get(ArtifactReference, result.resource_id) is not None
    assert session.scalar(select(ToolInvocation)) is not None


def test_plot_non_numeric_y_rejected(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    ctx = _analysis_ctx(session, actor, project, tmp_path)
    artifact = _upload(session, actor, project, ctx.content_store, b"x,label\n1,a\n2,b\n")
    with pytest.raises(ValidationError):
        _runtime().invoke(
            ctx,
            ToolInvocationCreate(
                tool_id="plot.xy",
                input={"artifact_id": str(artifact.artifact_id), "x_column": "x", "y_columns": ["label"]},
            ),
        )


def test_plot_truncation_caps_points(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    ctx = _analysis_ctx(session, actor, project, tmp_path)
    rows = "\n".join(f"{i},{i * 2}" for i in range(6_000))
    artifact = _upload(session, actor, project, ctx.content_store, f"x,y\n{rows}\n".encode())
    result = _runtime().invoke(
        ctx,
        ToolInvocationCreate(
            tool_id="plot.xy",
            input={"artifact_id": str(artifact.artifact_id), "x_column": "x", "y_columns": ["y"]},
        ),
    )
    value = result.value or {}
    assert len(value["series"][0]["y"]) == 5_000
    assert value["source_rows"] == 6_000
    assert value["rendered_points"] == 5_000
    assert value["truncated"] is True


def test_plot_under_bound_is_not_truncated(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    ctx = _analysis_ctx(session, actor, project, tmp_path)
    artifact = _upload(session, actor, project, ctx.content_store, b"x,y\n1,2\n2,4\n")
    result = _runtime().invoke(
        ctx,
        ToolInvocationCreate(
            tool_id="plot.xy",
            input={"artifact_id": str(artifact.artifact_id), "x_column": "x", "y_columns": ["y"]},
        ),
    )
    value = result.value or {}
    assert value["source_rows"] == 2
    assert value["rendered_points"] == 2
    assert value["truncated"] is False


def test_table_select_propagates_source_truncation(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    ctx = _analysis_ctx(session, actor, project, tmp_path)
    artifact = _upload(session, actor, project, ctx.content_store, b"x\n" + b"1\n" * 10_500)
    result = _runtime().invoke(
        ctx,
        ToolInvocationCreate(
            tool_id="table.select",
            input={"artifact_id": str(artifact.artifact_id), "columns": ["x"], "limit": 10},
        ),
    )
    value = result.value or {}
    assert value["source_truncated"] is True
    assert value["row_count"] == 10


def test_truth_tool_owner_success_paths(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    ctx = _analysis_ctx(session, actor, project, tmp_path)
    series_id = services.create_object(session, actor, project.id, "protein", "TruthTarget", payload={"organism": "T"})
    revision_id = session.scalar(
        select(ScientificObjectRevision.revision_id)
        .where(ScientificObjectRevision.series_id == series_id)
        .order_by(ScientificObjectRevision.revision_seq)
        .limit(1)
    )

    evidence = _runtime().invoke(
        ctx,
        ToolInvocationCreate(
            tool_id="evidence.create",
            input={
                "kind": "computation",
                "polarity": "supports",
                "target_kind": "scientific_object_revision",
                "target_id": str(revision_id),
            },
        ),
    )
    assert evidence.result_kind is ToolResultKind.EVIDENCE
    assert evidence.resource_id is not None

    draft = _runtime().invoke(
        ctx,
        ToolInvocationCreate(
            tool_id="decision.record_draft",
            input={"title": "Truth draft", "statement": "drafted via tool"},
        ),
    )
    assert draft.result_kind is ToolResultKind.DECISION
    assert draft.value is not None
    assert draft.value["status"] == "draft"

    committed = _runtime().invoke(
        ctx,
        ToolInvocationCreate(tool_id="decision.commit", input={"decision_id": str(draft.resource_id)}),
    )
    assert committed.value is not None
    assert committed.value["status"] == "committed"


def test_catalog_serialized_has_no_secret_fields(session):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(_CredComputeDriver())
    store = InMemorySecretStore()
    sentinel = "SENTINEL-catalog-never-leaks"
    services.provision_credential(session, store, registry, actor, "credtool", "api_key", sentinel)
    from revolab.tools import build_tool_catalog

    catalog = build_tool_catalog(session, actor, project.id, registry)
    serialized = json.dumps([tool.model_dump() for tool in catalog.tools], sort_keys=True)
    assert sentinel not in serialized
    assert "secret_ref" not in serialized
    assert "secret" not in serialized


def test_tool_invocations_read_endpoint(client):
    actor_id = client.post("/api/actors").json()["actor_id"]
    headers = _http_headers(actor_id)
    project = client.post("/api/projects", json={"name": "Invocation Log"}, headers=headers).json()
    pid = project["id"]
    uploaded = client.post(
        f"/api/projects/{pid}/artifacts",
        headers=headers,
        files={"file": ("data.csv", b"x,y\n1,2\n", "text/csv")},
    )
    artifact_id = uploaded.json()["resource_id"]

    persisted = client.post(
        f"/api/projects/{pid}/tools/invocations",
        json={"tool_id": "table.select", "input": {"artifact_id": artifact_id, "columns": ["x", "y"]}, "persist": True},
        headers=headers,
    )
    assert persisted.status_code == 201

    listing = client.get(f"/api/projects/{pid}/tool-invocations", headers=headers)
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 1
    assert rows[0]["tool_id"] == "table.select"
    assert rows[0]["tool_version"] == "1.0.0"
    assert rows[0]["parameters"]["columns"] == ["x", "y"]
    assert "secret" not in json.dumps(rows)


def test_external_compute_artifact_select_persist_over_http(client):
    from revolab import api as api_mod
    from revolab.main import app

    actor_id = client.post("/api/actors").json()["actor_id"]
    headers = _http_headers(actor_id)
    project = client.post("/api/projects", json={"name": "Ext Persist"}, headers=headers).json()
    pid = project["id"]
    obj = client.post(
        f"/api/projects/{pid}/objects",
        json={"object_type": "protein", "name": "ExtTarget", "payload": {"organism": "X"}},
        headers=headers,
    ).json()
    revision_id = obj["visible_revisions"][0]["revision_id"]

    registry = _registry(FakeComputeDriver())
    app.dependency_overrides[api_mod.get_driver_registry] = lambda: registry
    app.dependency_overrides[api_mod.get_secret_store] = lambda: InMemorySecretStore()
    try:
        submitted = client.post(
            f"/api/projects/{pid}/compute/submissions",
            json={
                "provider_key": "fakecompute",
                "task_kind": "tabular",
                "inputs": [{"kind": "scientific_object_revision", "resource_id": revision_id}],
                "params": {"rows": 4},
            },
            headers=headers,
        )
        run = submitted.json()
        artifacts = client.post(
            f"/api/projects/{pid}/runs/{run['run_resource_id']}/artifacts", headers=headers
        )
        artifact_id = artifacts.json()[0]["resource_id"]

        selected = client.post(
            f"/api/projects/{pid}/tools/invocations",
            json={
                "tool_id": "table.select",
                "input": {"artifact_id": artifact_id, "columns": ["group", "value"]},
                "persist": True,
            },
            headers=headers,
        )
        assert selected.status_code == 201, selected.text
        assert selected.json()["persisted"] is True
    finally:
        app.dependency_overrides.clear()
