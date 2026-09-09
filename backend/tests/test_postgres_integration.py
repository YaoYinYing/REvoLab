"""PostgreSQL acceptance for Phase 3 (migration + credential/availability slice).

Runs only when `REVOLAB_TEST_DATABASE_URL` points at a migrated PostgreSQL
database (CI runs `alembic upgrade head && alembic check` first, then this file).
The default SQLite in-memory fixtures in `conftest.py` are deliberately not used
here: PostgreSQL semantics are architecture truth (TODO.md section 10).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.orm import Session

from revolab import services
from revolab.domain.provider import build_credential_lease, capability_availability
from revolab.enums import CapabilityAvailability, ProviderRuntimeHealth
from revolab.secret_store import InMemorySecretStore

DATABASE_URL = os.environ.get("REVOLAB_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not DATABASE_URL.startswith("postgresql"),
    reason="requires REVOLAB_TEST_DATABASE_URL pointing at a migrated PostgreSQL database",
)

SENTINEL = "REVOLAB_SENTINEL_0f9e2a7c4b6d8e1f"


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
    actor = services.create_actor(pg_session)
    project = services.create_project(pg_session, actor, "PG Acceptance")

    availability = capability_availability(
        pg_session,
        actor,
        project.id,
        provider_key="fakeprov",
        health=ProviderRuntimeHealth.READY,
        required_kinds=("api_key",),
    )
    assert availability is CapabilityAvailability.CREDENTIAL_MISSING

    binding = services.provision_credential(
        pg_session, store, actor, "fakeprov", "api_key", SENTINEL
    )
    assert binding.secret_ref != SENTINEL
    assert SENTINEL not in binding.secret_ref

    lease = build_credential_lease(pg_session, store, actor, "fakeprov", ("api_key",))
    assert lease.get("api_key") == SENTINEL

    availability = capability_availability(
        pg_session,
        actor,
        project.id,
        provider_key="fakeprov",
        health=ProviderRuntimeHealth.READY,
        required_kinds=("api_key",),
    )
    assert availability is CapabilityAvailability.AVAILABLE

    services.revoke_credential(pg_session, store, actor, "fakeprov", "api_key")
    availability = capability_availability(
        pg_session,
        actor,
        project.id,
        provider_key="fakeprov",
        health=ProviderRuntimeHealth.READY,
        required_kinds=("api_key",),
    )
    assert availability is CapabilityAvailability.CREDENTIAL_MISSING
