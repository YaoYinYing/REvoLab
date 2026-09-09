"""Phase-4 Core/domain compute tests over a provider-neutral fake capability.

The fake provider is deliberately NOT REvoCompute-shaped: Core must be unable to
name any REvoCompute task/runner vocabulary. Everything below speaks neutral
`ComputeCapability`/`ArtifactResolutionCapability` value objects.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select

from revolab import services
from revolab.capabilities import (
    ArtifactHandle,
    CapabilityError,
    ExternalArtifactRef,
    InputBinding,
    InputSpec,
    ResolvedInput,
    RunHandle,
    RunView,
    TaskKindRef,
    TaskKindSchema,
)
from revolab.content_store import ContentStore
from revolab.domain import compute as compute_domain
from revolab.domain.errors import AuthorizationError, ValidationError
from revolab.drivers import Capability, DriverContext, DriverRegistry
from revolab.enums import (
    CapabilityErrorKind,
    CapabilityKind,
    ProviderRuntimeHealth,
    RelationType,
    ResourceKind,
)
from revolab.models import (
    ArtifactReference,
    GlobalProvenanceEdge,
    ProjectMembership,
    RunReference,
    ScientificObjectRevision,
)


class _State:
    def __init__(self) -> None:
        self.submit_calls = 0
        self._runs: dict[str, bytes] = {}

    def submit(self, inputs: Sequence[ResolvedInput], params: Mapping[str, Any]) -> str:
        self.submit_calls += 1
        native_id = f"run-{self.submit_calls}"
        message = str(params.get("message", "hello"))
        self._runs[native_id] = (
            message.encode() + b"\n" + ";".join(i.filename for i in inputs).encode()
        )
        return native_id

    def bytes(self, native_id: str) -> bytes:
        return self._runs[native_id]


class _Compute:
    provider_key = "fakecompute"
    kind = CapabilityKind.COMPUTE

    def __init__(self, state: _State) -> None:
        self._state = state

    def list_task_kinds(self, credentials: Any) -> list[TaskKindRef]:
        return [TaskKindRef(kind_id="echo", display_name="Echo", description=None, category=None)]

    def task_kind_schema(self, kind_id: str, credentials: Any) -> TaskKindSchema:
        return TaskKindSchema(
            kind_id="echo",
            display_name="Echo",
            description=None,
            parameter_schema={"type": "object", "properties": {}, "additionalProperties": False},
            input_spec=InputSpec(label="input", required=False),
        )

    def submit(
        self,
        kind_id: str,
        inputs: Sequence[ResolvedInput],
        params: Mapping[str, Any],
        credentials: Any,
    ) -> RunHandle:
        return RunHandle(authority="fakecompute", native_id=self._state.submit(inputs, params), task_type=kind_id)

    def get_run(self, native_id: str, credentials: Any) -> RunView:
        return RunView(authority="fakecompute", native_id=native_id, status="finished")

    def list_artifacts(self, native_id: str, credentials: Any) -> list[ArtifactHandle]:
        data = self._state.bytes(native_id)
        return [
            ArtifactHandle(
                authority="fakecompute",
                native_id=f"{native_id}:out.txt",
                version_id="",
                content_type="text/plain",
                size=len(data),
                checksum=f"sha256-{len(data)}",
            )
        ]


class _Resolve:
    provider_key = "fakecompute"
    kind = CapabilityKind.ARTIFACT_RESOLUTION

    def __init__(self, state: _State) -> None:
        self._state = state

    def resolve(self, artifact: ExternalArtifactRef, credentials: Any) -> ArtifactHandle:
        run = artifact.native_id.partition(":")[0]
        data = self._state.bytes(run)
        return ArtifactHandle(
            authority="fakecompute",
            native_id=artifact.native_id,
            data=data,
            content_type="text/plain",
            size=len(data),
        )


class _Driver:
    def __init__(self, state: _State, *, required_kinds: tuple[str, ...] = (), healthy: bool = True) -> None:
        self.name = "fakecompute"
        self.display_name = "Fake Compute"
        self.description: str | None = None
        self.required_credential_kinds = required_kinds
        self.capabilities: Mapping[CapabilityKind, Capability] = {
            CapabilityKind.COMPUTE: _Compute(state),
            CapabilityKind.ARTIFACT_RESOLUTION: _Resolve(state),
        }
        self.healthy = healthy

    def start(self, context: DriverContext) -> None:
        pass

    def stop(self) -> None:
        pass

    def probe_health(self) -> ProviderRuntimeHealth:
        return ProviderRuntimeHealth.READY if self.healthy else ProviderRuntimeHealth.UNREACHABLE


def _started(state: _State, *, required_kinds: tuple[str, ...] = (), healthy: bool = True) -> DriverRegistry:
    registry = DriverRegistry()
    registry.register(_Driver(state, required_kinds=required_kinds, healthy=healthy))
    registry.start_all(DriverContext(environment="test", settings=MappingProxyType({})))
    return registry


def _actor(session) -> UUID:
    return services.create_actor(session)


def _project(session, actor_id: UUID) -> UUID:
    return services.create_project(session, actor_id, "P").id


def _revision(session, actor_id: UUID, project_id: UUID, payload: dict[str, Any]) -> UUID:
    series = services.create_object(session, actor_id, project_id, "sequence", "obj", payload=payload)
    return session.scalar(
        select(ScientificObjectRevision.revision_id)
        .where(ScientificObjectRevision.series_id == series)
        .order_by(ScientificObjectRevision.revision_seq)
        .limit(1)
    )


def test_submit_creates_run_reference_and_input_provenance(session, secret_store, tmp_path) -> None:
    state = _State()
    registry = _started(state)
    actor = _actor(session)
    project = _project(session, actor)
    revision = _revision(session, actor, project, {"sequence": "MEEPQ"})

    result = services.compute_submit(
        session,
        registry,
        secret_store,
        ContentStore(tmp_path),
        actor,
        project,
        "fakecompute",
        "echo",
        [InputBinding(kind=ResourceKind.SCIENTIFIC_OBJECT_REVISION, resource_id=revision, role=None)],
        {"message": "hello"},
    )

    run = session.get(RunReference, result["run_resource_id"])
    assert run is not None
    assert run.authority == "fakecompute"
    assert run.native_id == "run-1"

    edge = session.scalar(
        select(GlobalProvenanceEdge).where(
            GlobalProvenanceEdge.relation_type == RelationType.CONSUMED_AS_INPUT_BY.value,
            GlobalProvenanceEdge.target_id == run.run_id,
        )
    )
    assert edge is not None
    assert edge.source_id == revision
    assert edge.source_kind == ResourceKind.SCIENTIFIC_OBJECT_REVISION.value


def test_refresh_artifacts_creates_artifact_and_produced_edge(session, secret_store, tmp_path) -> None:
    state = _State()
    registry = _started(state)
    actor = _actor(session)
    project = _project(session, actor)
    revision = _revision(session, actor, project, {})
    submitted = services.compute_submit(
        session, registry, secret_store, ContentStore(tmp_path), actor, project,
        "fakecompute", "echo", [InputBinding(kind=ResourceKind.SCIENTIFIC_OBJECT_REVISION, resource_id=revision)], {},
    )
    artifacts = services.compute_refresh_artifacts(
        session, registry, secret_store, actor, project, "fakecompute",
        submitted["run_resource_id"], submitted["native_id"],
    )
    assert len(artifacts) == 1
    artifact = session.get(ArtifactReference, artifacts[0]["resource_id"])
    assert artifact is not None
    produced = session.scalar(
        select(GlobalProvenanceEdge).where(
            GlobalProvenanceEdge.relation_type == RelationType.PRODUCED.value,
            GlobalProvenanceEdge.source_id == submitted["run_resource_id"],
            GlobalProvenanceEdge.target_id == artifact.artifact_id,
        )
    )
    assert produced is not None


def test_resolve_artifact_returns_bytes(session, secret_store, tmp_path) -> None:
    state = _State()
    registry = _started(state)
    actor = _actor(session)
    project = _project(session, actor)
    revision = _revision(session, actor, project, {})
    submitted = services.compute_submit(
        session, registry, secret_store, ContentStore(tmp_path), actor, project,
        "fakecompute", "echo", [InputBinding(kind=ResourceKind.SCIENTIFIC_OBJECT_REVISION, resource_id=revision)], {},
    )
    artifacts = services.compute_refresh_artifacts(
        session, registry, secret_store, actor, project, "fakecompute",
        submitted["run_resource_id"], submitted["native_id"],
    )
    identity = artifacts[0]
    resolved = compute_domain.resolve_artifact(
        session, registry, secret_store, actor, "fakecompute",
        ExternalArtifactRef(
            authority="fakecompute", native_id=identity["native_id"], version_id=identity["version_id"] or "",
            content_type=identity["content_type"], size=identity["size"], checksum=identity["checksum"],
        ),
        permitted=True,
    )
    assert resolved.data == state.bytes(submitted["native_id"])


def test_provider_disappearance_leaves_references_intact(session, secret_store, tmp_path) -> None:
    state = _State()
    registry = _started(state)
    actor = _actor(session)
    project = _project(session, actor)
    revision = _revision(session, actor, project, {})
    submitted = services.compute_submit(
        session, registry, secret_store, ContentStore(tmp_path), actor, project,
        "fakecompute", "echo", [InputBinding(kind=ResourceKind.SCIENTIFIC_OBJECT_REVISION, resource_id=revision)], {},
    )

    # Replace the live provider with an unreachable one (same key) — the stored
    # reference and graph truth must remain intact, while live resolution fails.
    down = _started(_State(), healthy=False)
    with pytest.raises(CapabilityError) as excinfo:
        compute_domain.get_run(
            session, down, secret_store, actor, "fakecompute", submitted["native_id"], permitted=True
        )
    assert excinfo.value.kind is CapabilityErrorKind.PROVIDER_UNAVAILABLE

    assert session.get(RunReference, submitted["run_resource_id"]) is not None
    edge = session.scalar(
        select(GlobalProvenanceEdge).where(
            GlobalProvenanceEdge.target_id == submitted["run_resource_id"]
        )
    )
    assert edge is not None


def test_invalid_input_kind_fails_before_execution(session, secret_store, tmp_path) -> None:
    state = _State()
    registry = _started(state)
    actor = _actor(session)
    project = _project(session, actor)
    series = services.create_object(session, actor, project, "protein", "obj", payload={})

    with pytest.raises(ValidationError):
        services.compute_submit(
            session, registry, secret_store, ContentStore(tmp_path), actor, project, "fakecompute", "echo",
            [InputBinding(kind=ResourceKind.SCIENTIFIC_OBJECT_SERIES, resource_id=series)], {},
        )
    assert state.submit_calls == 0


def test_missing_credential_fails_before_execution(session, secret_store, tmp_path) -> None:
    state = _State()
    registry = _started(state, required_kinds=("api_key",))
    actor = _actor(session)
    project = _project(session, actor)
    revision = _revision(session, actor, project, {})

    with pytest.raises(AuthorizationError):
        services.compute_submit(
            session, registry, secret_store, ContentStore(tmp_path), actor, project, "fakecompute", "echo",
            [InputBinding(kind=ResourceKind.SCIENTIFIC_OBJECT_REVISION, resource_id=revision)], {},
        )
    assert state.submit_calls == 0


def test_not_authorized_fails_before_execution(session, secret_store, tmp_path) -> None:
    state = _State()
    registry = _started(state)
    owner = _actor(session)
    project = _project(session, owner)
    revision = _revision(session, owner, project, {})
    viewer = _actor(session)
    session.add(ProjectMembership(project_id=project, actor_id=viewer, role="viewer"))
    session.commit()

    with pytest.raises(AuthorizationError):
        services.compute_submit(
            session, registry, secret_store, ContentStore(tmp_path), viewer, project, "fakecompute", "echo",
            [InputBinding(kind=ResourceKind.SCIENTIFIC_OBJECT_REVISION, resource_id=revision)], {},
        )
    assert state.submit_calls == 0


def test_input_not_visible_fails_before_execution(session, secret_store, tmp_path) -> None:
    state = _State()
    registry = _started(state)
    actor = _actor(session)
    project = _project(session, actor)
    other_project = services.create_project(session, actor, "Other").id
    revision = _revision(session, actor, other_project, {})

    with pytest.raises(AuthorizationError):
        services.compute_submit(
            session, registry, secret_store, ContentStore(tmp_path), actor, project, "fakecompute", "echo",
            [InputBinding(kind=ResourceKind.SCIENTIFIC_OBJECT_REVISION, resource_id=revision)], {},
        )
    assert state.submit_calls == 0
