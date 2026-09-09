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

import pytest
from sqlalchemy import Engine, create_engine, inspect, select
from sqlalchemy.orm import Session

from revolab import services
from revolab.domain.provider import (
    build_credential_lease,
    capability_availability,
    credentials_present,
)
from revolab.drivers import Capability, DriverContext, DriverRegistry
from revolab.enums import CapabilityAvailability, CapabilityKind, ProviderRuntimeHealth
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
