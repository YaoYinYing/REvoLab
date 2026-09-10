"""Phase-6 Agent Context & Tools: bounded context, ToolCatalog authority
projection, artifact inspection, and the proposal -> draft -> commit slice.

Safety regressions prove the Agent is a consumer, never an owner:
- no raw write path, no cross-Project context leak, no sibling-revision leak;
- no credential tool/credential material anywhere in the Agent surface;
- chatting never persists into the scientific graph;
- the Agent surface can only ever create a Decision DRAFT.
"""

from __future__ import annotations

import json
from types import MappingProxyType
from typing import ClassVar
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select

from revolab import services
from revolab.agent import (
    AgentTurnRunner,
    build_context,
    build_tool_catalog,
    inspect_artifact,
)
from revolab.agent.skills import SkillCatalog
from revolab.capabilities import ArtifactHandle, CapabilityError, ExternalArtifactRef
from revolab.content_store import ContentStore
from revolab.domain.errors import AuthorizationError, ModelUnavailableError
from revolab.drivers import DriverContext, DriverRegistry
from revolab.enums import (
    AgentTerminationReason,
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
from revolab.testing.fake_model import ScriptedModelBackend
from revolab.tools.registry import build_default_registry
from revolab.tools.runtime import LocalToolRuntime


def _actor(session):
    return services.create_actor(session)


def _project(session, actor, name="Agent P"):
    return services.create_project(session, actor, name)


def _object(session, actor, project, name="X", obj_type="protein", payload=None):
    return services.create_object(session, actor, project.id, obj_type, name, payload=payload or {})


def _revision(session, actor, project, series_id, payload):
    return services.append_revision(session, actor, project.id, series_id, payload)


def _headers(actor_id: str) -> dict[str, str]:
    return {"X-Actor-Id": actor_id}


def _http_actor(client) -> str:
    return client.post("/api/actors").json()["actor_id"]


def _http_project(client, actor_id: str, name: str = "Agent P") -> dict:
    return client.post("/api/projects", json={"name": name}, headers=_headers(actor_id)).json()


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


class _ArtifactCapability:
    provider_key = "credartifact"
    kind = CapabilityKind.ARTIFACT_RESOLUTION

    def __init__(self, expected_secret: str | None = None) -> None:
        self._expected_secret = expected_secret

    def resolve(self, artifact: ExternalArtifactRef, credentials: object) -> ArtifactHandle:
        # Prove the credential lease was materialized for the CALLING actor and
        # stayed inside the driver transport: the resolver sees it, but the
        # inspect result must never reproduce it.
        if self._expected_secret is not None:
            assert getattr(credentials, "get")("api_key") == self._expected_secret
        return ArtifactHandle(
            authority="credartifact",
            native_id=artifact.native_id,
            version_id="",
            content_type="text/plain",
            size=None,
            checksum=None,
            data=b"external-artifact-body",
        )

    def preview(
        self,
        artifact: ExternalArtifactRef,
        credentials: object,
        *,
        offset: int = 0,
        limit: int,
    ) -> ArtifactHandle:
        if self._expected_secret is not None:
            assert getattr(credentials, "get")("api_key") == self._expected_secret
        return ArtifactHandle(
            authority="credartifact",
            native_id=artifact.native_id,
            version_id="",
            content_type="text/plain",
            size=len(b"external-artifact-body"),
            checksum=None,
            data=b"external-artifact-body"[offset : offset + limit],
        )


class _CredentialedArtifactDriver:
    name = "credartifact"
    display_name = "Credentialed Artifact Provider"
    description = "synthetic artifact-resolution driver requiring a credential"
    required_credential_kinds = ("api_key",)
    authorities = ("credartifact",)

    def __init__(self, expected_secret: str | None = None) -> None:
        self.capabilities: dict[CapabilityKind, object] = {
            CapabilityKind.ARTIFACT_RESOLUTION: _ArtifactCapability(expected_secret)
        }

    def start(self, context: DriverContext) -> None:
        pass

    def stop(self) -> None:
        pass

    def probe_health(self) -> ProviderRuntimeHealth:
        return ProviderRuntimeHealth.READY


class _NoPreviewCapability:
    provider_key = "nopreview"
    kind = CapabilityKind.ARTIFACT_RESOLUTION

    def resolve(self, artifact: ExternalArtifactRef, credentials: object) -> ArtifactHandle:
        return ArtifactHandle(
            authority="nopreview",
            native_id=artifact.native_id,
            version_id="",
            content_type="text/plain",
            size=None,
            checksum=None,
            data=b"full-body",
        )


class _NoPreviewArtifactDriver:
    name = "nopreview"
    display_name = "No-Preview Artifact Provider"
    description = "synthetic artifact-resolution driver WITHOUT bounded preview"
    required_credential_kinds = ()
    authorities = ("nopreview",)

    def __init__(self) -> None:
        self.capabilities: dict[CapabilityKind, object] = {
            CapabilityKind.ARTIFACT_RESOLUTION: _NoPreviewCapability()
        }

    def start(self, context: DriverContext) -> None:
        pass

    def stop(self) -> None:
        pass

    def probe_health(self) -> ProviderRuntimeHealth:
        return ProviderRuntimeHealth.READY


# ---------------------------------------------------------------------------
# SkillCatalog runtime root
# ---------------------------------------------------------------------------


def test_skill_catalog_uses_configured_root(tmp_path):
    root = tmp_path / "skills"
    (root / "project-context").mkdir(parents=True)
    (root / "project-context" / "SKILL.md").write_text(
        "---\nname: project-context\nversion: 0.1.0\ndescription: context skill\n---\n# procedural body\n",
        encoding="utf-8",
    )
    ref = SkillCatalog(root=root).load("project-context")
    assert ref.id == "project-context"
    assert ref.version == "0.1.0"


def test_skill_catalog_fails_closed_on_missing_root(tmp_path):
    with pytest.raises(FileNotFoundError):
        SkillCatalog(root=tmp_path / "missing").load("project-context")


def test_skill_catalog_default_root_honors_env(tmp_path, monkeypatch):
    from revolab.config import get_settings

    root = tmp_path / "skills"
    (root / "project-context").mkdir(parents=True)
    (root / "project-context" / "SKILL.md").write_text(
        "---\nname: project-context\nversion: 0.1.0\ndescription: env skill\n---\n# env\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    monkeypatch.setenv("REVOLAB_SKILLS_ROOT", str(root))
    try:
        assert SkillCatalog().load("project-context").id == "project-context"
    finally:
        get_settings.cache_clear()
        monkeypatch.delenv("REVOLAB_SKILLS_ROOT", raising=False)


def test_skill_catalog_fails_closed_when_dev_root_missing(tmp_path, monkeypatch):
    import revolab.agent.skills as skills_module

    monkeypatch.setattr(skills_module, "_DEV_SKILLS_ROOT", tmp_path / "missing-dev-root")
    with pytest.raises(FileNotFoundError):
        SkillCatalog().load("project-context")


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
    assert decisions["table.describe"].available is True


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


def test_revision_only_selection_does_not_expand_siblings(session):
    actor = _actor(session)
    project = _project(session, actor)
    series = _object(session, actor, project, "V")
    _revision(session, actor, project, series, {"chain": "A1"})
    target = _revision(session, actor, project, series, {"chain": "A2"})

    context = build_context(
        session,
        actor,
        project.id,
        _registry(),
        ContextSelectionCreate(revision_ids=[target.revision_id]),
    )
    assert {ref.series_id for ref in context.series} == {series}
    # Only the explicitly selected revision; the initial revision and the other
    # sibling stay out (declarative selection, never silent expansion).
    assert {ref.revision_id for ref in context.revisions} == {target.revision_id}


def test_graph_depth_does_not_leak_edges_beyond_boundary(session):
    actor = _actor(session)
    project = _project(session, actor)
    s1 = _object(session, actor, project, "S1")
    s2 = _object(session, actor, project, "S2")
    s3 = _object(session, actor, project, "S3")
    services.add_variant_of(session, actor, project.id, s1, s2)
    services.add_variant_of(session, actor, project.id, s2, s3)

    context = build_context(
        session,
        actor,
        project.id,
        _registry(),
        ContextSelectionCreate(series_ids=[s1], graph_depth=1),
    )
    pairs = {(edge.source_id, edge.target_id) for edge in context.relations}
    assert (s1, s2) in pairs
    assert (s2, s3) not in pairs  # S3 is beyond the requested depth neighborhood


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
    assert result.preview_size == 8
    assert result.truncated is True
    assert result.binary is False
    assert result.authority == "revolab"
    # Faithful head slice: preview is exactly the first `preview_limit` bytes,
    # never a fabricated summary.
    assert result.preview == (b"ACGT" * 100)[:8].decode("utf-8")


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


def test_internal_inspect_uses_bounded_read_not_full_get(session, tmp_path, monkeypatch):
    actor = _actor(session)
    project = _project(session, actor)
    content_store = ContentStore(tmp_path)
    artifact = services.create_internal_artifact(
        session, actor, project.id, content_store, b"A" * 10_000, content_type="text/plain"
    )

    def full_get(handle: str) -> bytes:
        raise AssertionError("inspect_artifact must not materialize the whole internal artifact")

    monkeypatch.setattr(content_store, "get", full_get)
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
    assert result.preview == "AAAAAAAA"
    assert result.preview_size == 8


def test_inspect_artifact_fails_closed_without_preview_capability(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    registry = _registry(_NoPreviewArtifactDriver())
    artifact = services.create_artifact_reference(
        session, actor, project.id, "nopreview", "a1", content_type="text/plain"
    )
    with pytest.raises(CapabilityError):
        inspect_artifact(
            session,
            registry,
            InMemorySecretStore(),
            ContentStore(tmp_path),
            actor,
            project.id,
            artifact.artifact_id,
        )


def test_external_artifact_inspect_never_leaks_credential(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    sentinel = "SENTINEL-external-inspect-credential"
    registry = _registry(_CredentialedArtifactDriver(expected_secret=sentinel))
    store = InMemorySecretStore()

    services.provision_credential(session, store, registry, actor, "credartifact", "api_key", sentinel)
    artifact = services.create_artifact_reference(
        session, actor, project.id, "credartifact", "a1", content_type="text/plain"
    )

    result = inspect_artifact(
        session,
        registry,
        store,
        ContentStore(tmp_path),
        actor,
        project.id,
        artifact.artifact_id,
        preview_limit=64,
    )
    assert result.authority == "credartifact"
    assert result.preview == b"external-artifact-body"[:64].decode("utf-8")
    # The credential WAS materialized for resolution (the fake resolver asserts
    # it), yet no serialized field of the inspect result reproduces it.
    assert sentinel not in result.preview
    assert sentinel not in result.model_dump_json()


# ---------------------------------------------------------------------------
# ToolCatalog projection
# ---------------------------------------------------------------------------


def _tool_ids(catalog) -> set[str]:
    return {tool.id for tool in catalog.tools}


DOMAIN_TOOL_IDS = frozenset(
    {
        "artifact.inspect",
        "table.describe",
        "table.select",
        "plot.xy",
        "evidence.create",
        "decision.record_draft",
        "decision.commit",
    }
)


def test_domain_tools_authority_matrix(session):
    owner = _actor(session)
    project = _project(session, owner)
    catalog = build_tool_catalog(session, owner, project.id, _registry())
    by_id = {tool.id: tool for tool in catalog.tools}

    # The closed local-tool set is exactly the Phase-7 minimal slice — pin it so
    # a raw-write/credential/membership tool can never slip in while tests pass.
    assert _tool_ids(catalog) == DOMAIN_TOOL_IDS

    assert by_id["artifact.inspect"].autonomy is AgentToolAutonomy.AUTOMATIC
    assert by_id["table.describe"].autonomy is AgentToolAutonomy.AUTOMATIC
    assert by_id["table.select"].autonomy is AgentToolAutonomy.AUTOMATIC
    assert by_id["plot.xy"].autonomy is AgentToolAutonomy.AUTOMATIC
    assert by_id["evidence.create"].autonomy is AgentToolAutonomy.POLICY
    assert by_id["decision.record_draft"].autonomy is AgentToolAutonomy.POLICY
    assert by_id["decision.commit"].autonomy is AgentToolAutonomy.EXPLICIT_ACTION

    # Credentials, secret store, raw-write and sharing changes are NEVER tools.
    for tool in catalog.tools:
        for banned in ("credential", "secret", "share", "membership", "sql", "http"):
            assert banned not in tool.id
            assert banned not in repr(tool.input_schema)


def _assert_no_writes(statements: list[str], label: str) -> None:
    assert statements, f"expected {label} to issue read statements"
    write_prefixes = ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "TRUNCATE", "MERGE")
    for statement in statements:
        token = statement.lstrip().upper()
        assert not token.startswith(write_prefixes), f"{label} issued a write: {statement}"


def test_agent_read_surfaces_are_read_only(session):
    actor = _actor(session)
    project = _project(session, actor)
    series = _object(session, actor, project, "ReadOnly")
    engine = session.get_bind()
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        build_tool_catalog(session, actor, project.id, _registry())
        _assert_no_writes(statements, "tool catalog")
        statements.clear()
        build_context(
            session, actor, project.id, _registry(), ContextSelectionCreate(series_ids=[series])
        )
        _assert_no_writes(statements, "context builder")
    finally:
        event.remove(engine, "before_cursor_execute", capture)


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


def test_other_actor_credential_is_never_projected(session):
    owner = _actor(session)
    other = services.create_actor(session)
    project = _project(session, owner)
    services.add_membership(session, owner, project.id, other, Role.MEMBER.value)
    registry = _registry(_CredentialDriver())
    store = InMemorySecretStore()
    sentinel = "SENTINEL-other-actor-credential"

    services.provision_credential(session, store, registry, owner, "credprov", "api_key", sentinel)

    # The owner sees the capability as an executable tool; another member sees
    # CREDENTIAL_MISSING and therefore NO executable tool for the same provider.
    owner_tools = build_tool_catalog(session, owner, project.id, registry)
    assert any(tool.provider_key == "credprov" for tool in owner_tools.tools)

    other_tools = build_tool_catalog(session, other, project.id, registry)
    assert not any(tool.provider_key == "credprov" for tool in other_tools.tools)

    # The serialized provider-capability summary for the other actor never
    # contains the owner's secret value or any secret-material field.
    context = build_context(
        session,
        other,
        project.id,
        registry,
        ContextSelectionCreate(include_provider_capabilities=True),
    )
    serialized = json.dumps(
        [entry.model_dump() for entry in context.provider_capabilities], sort_keys=True
    )
    assert sentinel not in serialized
    assert "secret_ref" not in serialized
    credprov = next(entry for entry in context.provider_capabilities if entry.key == "credprov")
    assert {presence.kind: presence.present for presence in credprov.credential_presence} == {
        "api_key": False
    }


def test_viewer_never_sees_action_provider_tools(session):
    owner = _actor(session)
    viewer = services.create_actor(session)
    project = _project(session, owner)
    services.add_membership(session, owner, project.id, viewer, Role.VIEWER.value)
    registry = _registry(FakeComputeDriver())

    owner_tools = build_tool_catalog(session, owner, project.id, registry)
    assert any(tool.id.startswith("fakecompute.compute.") for tool in owner_tools.tools)

    viewer_tools = build_tool_catalog(session, viewer, project.id, registry)
    # Compute is an ACTION capability: policy requires owner/member, so a viewer
    # never sees any executable compute tool (the pure-read artifact.resolve tool
    # may still be projected through the separate ARTIFACT_RESOLUTION capability).
    assert not any(tool.id.startswith("fakecompute.compute.") for tool in viewer_tools.tools)


# ---------------------------------------------------------------------------
# Bounded Project Agent loop (Phase 8)
# ---------------------------------------------------------------------------


def _runner(session, actor, project, registry, store, tmp_path, *, model=None, bounds=None):
    local_registry = build_default_registry()
    return AgentTurnRunner(
        model if model is not None else ScriptedModelBackend(),
        LocalToolRuntime(local_registry),
        registry,
        store,
        ContentStore(tmp_path),
        bounds,
        local_registry=local_registry,
    )


def _draft_turn(steps):
    return ScriptedModelBackend(steps=steps)


def test_agent_loop_record_draft_stays_draft_then_explicit_commit(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    series = _object(session, actor, project, "T5alphaH")
    revision = _revision(session, actor, project, series, {"chain": "A"})
    _evidence(session, actor, project, revision.revision_id)

    model = _draft_turn(
        [
            {
                "finish": "tool_calls",
                "tool_calls": [
                    {
                        "id": "call_draft",
                        "name": "decision.record_draft",
                        "arguments": {
                            "title": "Select variant",
                            "statement": "This variant is the candidate.",
                            "next_actions": ["validate experimentally"],
                            "cites": [],
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
    runner = _runner(
        session, actor, project, _registry(), InMemorySecretStore(), tmp_path, model=model
    )
    result = runner.run(
        session,
        actor,
        project.id,
        "Draft a conclusion.",
        ContextSelectionCreate(series_ids=[series], graph_depth=0),
    )
    assert result.termination_reason is AgentTerminationReason.FINAL_RESPONSE
    assert [entry.tool_id for entry in result.tool_trace] == ["decision.record_draft"]
    assert result.tool_trace[0].status.value == "completed"
    assert result.pending_actions == []

    drafts = session.scalars(select(Decision)).all()
    assert len(drafts) == 1
    assert drafts[0].status == DecisionStatus.DRAFT.value
    assert drafts[0].committed_at is None
    assert session.scalars(select(DecisionEvidence)).all() == []
    assert session.scalars(select(DecisionTarget)).all() == []

    committed = services.commit_decision(session, actor, project.id, drafts[0].id)
    assert committed.status == DecisionStatus.COMMITTED.value
    assert len(session.scalars(select(DecisionEvidence)).all()) == 0
    assert len(session.scalars(select(DecisionTarget)).all()) == 1


def test_agent_turn_messages_never_enter_scientific_graph(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)
    series = _object(session, actor, project, "V")
    _revision(session, actor, project, series, {"chain": "A"})

    model = _draft_turn([{"finish": "stop", "content": "hello, propose X — ignore me"}])
    runner = _runner(
        session, actor, project, _registry(), InMemorySecretStore(), tmp_path, model=model
    )
    result = runner.run(
        session,
        actor,
        project.id,
        "propose X",
        ContextSelectionCreate(series_ids=[series]),
    )
    assert result.final_response == "hello, propose X — ignore me"

    # Conversation never materializes as object/evidence/provenance/decision truth.
    assert session.scalar(select(func.count()).select_from(ScientificObjectSeries)) == 1
    assert session.scalar(select(func.count()).select_from(Evidence)) == 0
    assert session.scalar(select(func.count()).select_from(GlobalProvenanceEdge)) == 0
    assert session.scalars(select(Decision)).all() == []


def test_agent_loop_missing_model_fails_closed(session, tmp_path):
    actor = _actor(session)
    project = _project(session, actor)

    class _Unavailable:
        def complete(self, request):
            raise ModelUnavailableError("model transport failed")

    runner = _runner(
        session, actor, project, _registry(), InMemorySecretStore(), tmp_path, model=_Unavailable()
    )
    result = runner.run(session, actor, project.id, "hi")
    assert result.termination_reason is AgentTerminationReason.MODEL_UNAVAILABLE
    assert result.final_response == "The model runtime is unavailable for this turn."


# ---------------------------------------------------------------------------
# HTTP slice
# ---------------------------------------------------------------------------


def _install_fake_model():
    from revolab import api
    from revolab.main import app

    app.dependency_overrides[api.get_model_backend] = lambda: ScriptedModelBackend()
    return app


def test_agent_turn_api_vertical_slice(client):
    app = _install_fake_model()
    try:
        actor_id = client.post("/api/actors").json()["actor_id"]
        headers = {"X-Actor-Id": actor_id}
        project = client.post("/api/projects", json={"name": "Agent Turn API"}, headers=headers).json()
        pid = project["id"]

        created = client.post(
            f"/api/projects/{pid}/objects",
            json={"object_type": "protein", "name": "T5alphaH", "payload": {"organism": "T"}},
            headers=headers,
        )
        assert created.status_code == 201
        series_id = created.json()["series"]["series_id"]

        uploaded = client.post(
            f"/api/projects/{pid}/artifacts",
            files={"file": ("table.csv", b"x,y\n1,2\n3,4\n", "text/csv")},
            headers=headers,
        )
        assert uploaded.status_code == 201
        artifact_id = uploaded.json()["resource_id"]

        turn = client.post(
            f"/api/projects/{pid}/agent/turns",
            json={
                "message": "Describe this table and draft a conclusion based on it.",
                "selection": {"series_ids": [series_id], "artifact_ids": [artifact_id]},
            },
            headers=headers,
        )
        assert turn.status_code == 200, turn.text
        body = turn.json()
        assert body["termination_reason"] == "final_response"
        tool_ids = [entry["tool_id"] for entry in body["tool_trace"]]
        assert tool_ids == ["table.describe", "decision.record_draft"]
        assert all(entry["status"] == "completed" for entry in body["tool_trace"])
        assert body["pending_actions"] == []

        decisions = client.get(f"/api/projects/{pid}/decisions", headers=headers).json()
        assert len(decisions) == 1
        assert decisions[0]["status"] == "draft"
        decision_id = decisions[0]["id"]

        committed = client.post(
            f"/api/projects/{pid}/decisions/{decision_id}/commit", headers=headers
        )
        assert committed.status_code == 200
        assert committed.json()["status"] == "committed"
    finally:
        app.dependency_overrides.clear()


def test_agent_tools_api_projects_available_provider_capabilities(client):
    from revolab import api
    from revolab.main import app

    actor_id = _http_actor(client)
    pid = _http_project(client, actor_id, "Agent Tools API")["id"]
    registry = _registry(FakeComputeDriver())
    app.dependency_overrides[api.get_driver_registry] = lambda: registry
    try:
        tools = client.get(f"/api/projects/{pid}/agent/tools", headers=_headers(actor_id))
        assert tools.status_code == 200
        tool_ids = {tool["id"] for tool in tools.json()["tools"]}
        assert "fakecompute.compute.submit" in tool_ids
        assert "fakecompute.artifact.resolve" in tool_ids
    finally:
        app.dependency_overrides.clear()


def test_inspect_artifact_api_returns_bounded_preview(client):
    actor_id = _http_actor(client)
    pid = _http_project(client, actor_id, "Inspect API")["id"]
    files = {"file": ("x.txt", b"ACGT" * 64, "text/plain")}
    uploaded = client.post(
        f"/api/projects/{pid}/artifacts", headers=_headers(actor_id), files=files
    )
    assert uploaded.status_code == 201
    artifact_id = uploaded.json()["resource_id"]

    inspected = client.get(
        f"/api/projects/{pid}/artifacts/{artifact_id}/inspect",
        headers=_headers(actor_id),
        params={"preview_limit": 8},
    )
    assert inspected.status_code == 200
    body = inspected.json()
    assert body["preview"] == "ACGTACGT"
    assert body["preview_size"] == 8
    assert body["truncated"] is True
    assert body["binary"] is False


def test_agent_http_viewer_is_read_only(client):
    app = _install_fake_model()
    try:
        owner_id = _http_actor(client)
        viewer_id = _http_actor(client)
        pid = _http_project(client, owner_id, "Agent Viewer")["id"]

        added = client.post(
            f"/api/projects/{pid}/members",
            json={"actor_id": viewer_id, "role": "viewer"},
            headers=_headers(owner_id),
        )
        assert added.status_code == 201

        # Reads succeed; mutation tools are reported unavailable; the mutation
        # path itself fails closed (403).
        context = client.post(f"/api/projects/{pid}/context", json={}, headers=_headers(viewer_id))
        assert context.status_code == 200

        tools = client.get(f"/api/projects/{pid}/agent/tools", headers=_headers(viewer_id))
        assert tools.status_code == 200
        by_id = {tool["id"]: tool for tool in tools.json()["tools"]}
        assert by_id["decision.record_draft"]["available"] is False
        assert by_id["decision.commit"]["available"] is False
        assert by_id["evidence.create"]["available"] is False
        assert by_id["table.describe"]["available"] is True

        # A policy tool requested by the model never executes for a viewer.
        from revolab import api

        app.dependency_overrides[api.get_model_backend] = lambda: ScriptedModelBackend(
            steps=[
                {
                    "finish": "tool_calls",
                    "tool_calls": [
                        {
                            "id": "call_draft",
                            "name": "decision.record_draft",
                            "arguments": {"title": "X", "statement": "not allowed"},
                        }
                    ],
                },
                {"finish": "stop", "content": "stopped"},
            ]
        )
        turn = client.post(
            f"/api/projects/{pid}/agent/turns",
            json={"message": "record a draft"},
            headers=_headers(viewer_id),
        )
        assert turn.status_code == 200
        body = turn.json()
        assert body["termination_reason"] == "final_response"
        assert body["tool_trace"][0]["tool_id"] == "decision.record_draft"
        assert body["tool_trace"][0]["status"] == "failed"
        assert client.get(f"/api/projects/{pid}/decisions", headers=_headers(viewer_id)).json() == []

        # The existing authorized commit endpoint also fails closed for a viewer.
        draft = client.post(
            f"/api/projects/{pid}/decisions",
            json={"title": "Owner draft", "statement": "draft owned by the project owner"},
            headers=_headers(owner_id),
        )
        assert draft.status_code == 201
        draft_id = draft.json()["id"]
        viewer_commit = client.post(
            f"/api/projects/{pid}/decisions/{draft_id}/commit", headers=_headers(viewer_id)
        )
        assert viewer_commit.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_agent_turn_without_model_config_fails_closed_http(client):
    actor_id = _http_actor(client)
    pid = _http_project(client, actor_id, "No Model")["id"]
    response = client.post(
        f"/api/projects/{pid}/agent/turns", json={"message": "hi"}, headers=_headers(actor_id)
    )
    assert response.status_code == 503
    assert "no model runtime configured" in response.json()["detail"]


def test_model_backend_shutdown_closes_transport_and_is_idempotent():
    from revolab import api

    class _TrackedBackend:
        def __init__(self) -> None:
            self.closed = False

        def complete(self, request):
            raise AssertionError("unused in lifecycle test")

        def close(self) -> None:
            self.closed = True

    tracked = _TrackedBackend()
    api._model_backend_instance = tracked
    api._model_backend_initialized = True

    api.close_model_backend()
    assert tracked.closed is True
    assert api._model_backend_initialized is False
    assert api._model_backend_instance is None

    # Closing again with no instance is a no-op (shutdown may run more than once).
    api.close_model_backend()
    assert api._model_backend_initialized is False
