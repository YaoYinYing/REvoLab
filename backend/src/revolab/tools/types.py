"""Neutral types for the Project Tool Harness (leaf module).

This module defines the invocation context and handler-outcome shapes shared by
the runtime, the local-tool handlers, and the catalog. It imports only neutral
leaves (SQLAlchemy session, driver registry, secret/content stores) so no
tools-package import cycles exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.orm import Session

from revolab.content_store import ContentStore
from revolab.drivers import DriverRegistry
from revolab.enums import ResourceKind, ToolResultKind
from revolab.secret_store import SecretStore


@dataclass(frozen=True)
class InvocationContext:
    """Everything one closed local-tool invocation may touch. All inputs are
    typed boundaries (session, driver registry, secret store, content store,
    actor/project identities) — never arbitrary filesystem/network handles."""

    session: Session
    registry: DriverRegistry
    secret_store: SecretStore
    content_store: ContentStore
    actor_id: UUID
    project_id: UUID
    # Whether typed domain mutations performed inside this invocation may commit
    # their own rows. The bounded Agent loop sets False so the whole turn is one
    # transaction and `run_conversation_turn` is the sole commit point (keeping a
    # per-conversation row lock held across the model run).
    commit: bool = True


@dataclass(frozen=True)
class DerivedArtifact:
    """Bytes a local analysis tool offers for optional durable persistence.

    Persistence is performed by the runtime through the typed
    ContentStore -> ArtifactReference path; the tool only produces bounded bytes.
    """

    data: bytes
    content_type: str
    name: str


@dataclass(frozen=True)
class HandlerOutput:
    """The typed outcome of one local-tool handler."""

    kind: ToolResultKind
    value: BaseModel | None = None
    resource_id: UUID | None = None
    resource_kind: ResourceKind | None = None
    persisted: bool = False
    derived: DerivedArtifact | None = None
