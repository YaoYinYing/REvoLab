"""REvoLab Phase-1 relational model.

Implements the accepted architecture (docs/architecture/* and ADRs 0008-0014):
global-resource identity + project-scoped membership/link/stewardship, typed
ScientificObject Series/Revision, reference identity cards, the frozen edge
matrix (#1-7 global provenance, #8-10 project knowledge), Evidence and Decision
with the draft/commit promotion boundary, and the GlobalResourceRegistry
referential spine.

Ownership/lifecycle are frozen in the ADRs. The physical shape of the typed
payload (one validated, schema-versioned JSONB column) and of edges (one table
per frozen ownership class, registry-FK endpoints) is the Phase-1 spike outcome
recorded in ADR-0015.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from revolab.db import Base, JSONType
from revolab.enums import (
    ActionRequestStatus,
    AgentTerminationReason,
    AgentToolAutonomy,
    CitedAs,
    Confidence,
    ConversationRole,
    DecisionStatus,
    EvidenceKind,
    EvidenceRole,
    ObjectType,
    Polarity,
    ProjectVisibility,
    RelationType,
    ResourceKind,
    Role,
    ToolExecutionClass,
    ToolSideEffectClass,
)


def _enum(enum_cls: type[Any], name: str, *, create_constraint: bool = False) -> Enum:
    """A cross-backend enum column type.

    `native_enum=False` keeps SQLite and PostgreSQL rendering identical (VARCHAR)
    so `alembic check` reports no drift on either backend. Enforcement is
    Python-side by default (Pydantic + `validate_strings=True`); set
    `create_constraint=True` for columns that must also carry a database CHECK
    constraint (the bounded durable-working-memory columns added in Phase 9 do).
    """
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=create_constraint,
        validate_strings=True,
        values_callable=lambda e: [member.value for member in e],
    )


# Reusable singleton enum types (one metadata type per concept).
_actor_role = _enum(Role, "project_role")
_visibility = _enum(ProjectVisibility, "project_visibility")
_resource_kind = _enum(ResourceKind, "resource_kind")
_object_type = _enum(ObjectType, "object_type")
_relation_type = _enum(RelationType, "relation_type")
_evidence_kind = _enum(EvidenceKind, "evidence_kind")
_evidence_role = _enum(EvidenceRole, "evidence_role")
_polarity = _enum(Polarity, "evidence_polarity")
_confidence = _enum(Confidence, "evidence_confidence")
_cited_as = _enum(CitedAs, "cited_as")
_decision_status = _enum(DecisionStatus, "decision_status")
_agent_termination = _enum(AgentTerminationReason, "agent_termination_reason", create_constraint=True)
_conversation_role = _enum(ConversationRole, "conversation_role", create_constraint=True)
_agent_tool_autonomy = _enum(AgentToolAutonomy, "agent_tool_autonomy", create_constraint=True)
_tool_execution_class = _enum(ToolExecutionClass, "tool_execution_class", create_constraint=True)
_tool_side_effect_class = _enum(ToolSideEffectClass, "tool_side_effect_class", create_constraint=True)
_action_request_status = _enum(ActionRequestStatus, "action_request_status", create_constraint=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Actor(Base, TimestampMixin):
    """The durable human/agent principal: an opaque UUID, independent of any
    auth provider (never a username or path)."""

    __tablename__ = "actors"

    actor_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)


class Project(Base, TimestampMixin):
    """The durable workspace boundary: namespace + membership scope, NOT an
    object owner. Deletion is a tombstone (deleted_at), never a hard delete."""

    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(
        _visibility, nullable=False, default=ProjectVisibility.PRIVATE.value
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectMembership(Base, TimestampMixin):
    """Actor(role) within a Project — the single security/authorization unit."""

    __tablename__ = "project_memberships"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("actors.actor_id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(_actor_role, nullable=False)


class GlobalResourceRegistry(Base, TimestampMixin):
    """Thin referential identity spine for every global resource row.

    The concrete global table's primary key is BOTH its own PK and an FK to
    `resource_id`. This gives ProjectResourceLink and edge endpoints a single
    non-polymorphic FK target. `resource_kind` records the concrete subtype; the
    "exactly one concrete row of that kind exists" side is a domain-service
    invariant, not a DB guarantee (integrity honesty, ADR-0008).
    """

    __tablename__ = "global_resource_registry"

    resource_id: Mapped[UUID] = mapped_column(primary_key=True)
    resource_kind: Mapped[str] = mapped_column(_resource_kind, nullable=False)


class ResourceStewardship(Base, TimestampMixin):
    """Which steward Project (if any) may MUTATE a global resource.

    Visibility (ProjectResourceLink) is NOT stewardship. A NULL steward_project
    means the resource is frozen until a transfer re-assigns it.
    """

    __tablename__ = "resource_stewardships"

    resource_id: Mapped[UUID] = mapped_column(
        ForeignKey("global_resource_registry.resource_id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    steward_project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProjectResourceLink(Base, TimestampMixin):
    """Project-owned context link: "this Project's context includes this global
    resource" (the read/visibility lens) + project-local folder placement and
    the optional preferred-revision pin. No per-resource role, no resource_kind
    (both would be denormalized second truths)."""

    __tablename__ = "project_resource_links"
    __table_args__ = (UniqueConstraint("project_id", "resource_id", name="uq_link_project_resource"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource_id: Mapped[UUID] = mapped_column(
        ForeignKey("global_resource_registry.resource_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    folder: Mapped[str | None] = mapped_column(String(500))
    annotation: Mapped[str | None] = mapped_column(String(1000))
    preferred_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("scientific_object_revision.revision_id", ondelete="SET NULL"), nullable=True
    )


class ScientificObjectSeries(Base, TimestampMixin):
    """Global conceptual identity of one scientific thing (series_id only — no
    current_revision_id, no organization/folder column, no version column)."""

    __tablename__ = "scientific_object_series"

    series_id: Mapped[UUID] = mapped_column(
        ForeignKey("global_resource_registry.resource_id", ondelete="CASCADE"),
        primary_key=True,
        default=uuid4,
    )
    object_type: Mapped[str] = mapped_column(_object_type, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("actors.actor_id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScientificObjectRevision(Base, TimestampMixin):
    """One immutable content version. The typed payload is a validated,
    schema-versioned JSONB blob (validated by the Core type registry). Revisions
    are INSERT-only."""

    __tablename__ = "scientific_object_revision"
    __table_args__ = (UniqueConstraint("series_id", "revision_seq", name="uq_revision_series_seq"),)

    revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("global_resource_registry.resource_id", ondelete="CASCADE"),
        primary_key=True,
        default=uuid4,
    )
    series_id: Mapped[UUID] = mapped_column(
        ForeignKey("scientific_object_series.series_id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    object_type: Mapped[str] = mapped_column(_object_type, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("actors.actor_id"))


class ScientificObjectAlias(Base, TimestampMixin):
    """Search-oriented, mutable, non-unique aliases bound to a series."""

    __tablename__ = "scientific_object_aliases"
    __table_args__ = (UniqueConstraint("series_id", "alias", name="uq_alias_series_value"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    series_id: Mapped[UUID] = mapped_column(
        ForeignKey("scientific_object_series.series_id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(String(300), nullable=False)


class ExternalIdentity(Base, TimestampMixin):
    """The single external-identity registry: (authority, native_id) is the
    durable external identity — never a resolver/provider, never a path."""

    __tablename__ = "external_identities"
    __table_args__ = (UniqueConstraint("authority", "native_id", name="uq_external_identity_authority_native"),)

    external_identity_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    authority: Mapped[str] = mapped_column(String(100), nullable=False)
    native_id: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[str | None] = mapped_column(String(50))


class ScientificObjectExternalIdentity(Base):
    """Global assertion mapping a series to an external identity (one series per
    (external_identity, qualifier)). Not project opinion."""

    __tablename__ = "scientific_object_external_identities"

    external_identity_id: Mapped[UUID] = mapped_column(
        ForeignKey("external_identities.external_identity_id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    qualifier: Mapped[str] = mapped_column(String(100), primary_key=True, default="identity")
    series_id: Mapped[UUID] = mapped_column(
        ForeignKey("scientific_object_series.series_id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RunReference(Base, TimestampMixin):
    """Immutable identity card for an external execution. Inputs/outputs live on
    the consumed_as_input_by / produced edges — never as columns here."""

    __tablename__ = "run_references"
    __table_args__ = (UniqueConstraint("authority", "native_id", name="uq_run_authority_native"),)

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("global_resource_registry.resource_id", ondelete="CASCADE"),
        primary_key=True,
        default=uuid4,
    )
    authority: Mapped[str] = mapped_column(String(100), nullable=False)
    native_id: Mapped[str] = mapped_column(String(300), nullable=False)
    task_type: Mapped[str | None] = mapped_column(String(100))
    input_parameter_digest: Mapped[str | None] = mapped_column(String(64))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SessionReference(Base, TimestampMixin):
    """Immutable identity card for an external interactive session."""

    __tablename__ = "session_references"
    __table_args__ = (UniqueConstraint("authority", "native_id", name="uq_session_authority_native"),)

    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("global_resource_registry.resource_id", ondelete="CASCADE"),
        primary_key=True,
        default=uuid4,
    )
    authority: Mapped[str] = mapped_column(String(100), nullable=False)
    native_id: Mapped[str] = mapped_column(String(300), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ArtifactReference(Base, TimestampMixin):
    """Immutable identity card for an external output (or an internally stored
    byte artifact with authority=revolab resolved via ContentStore). The checksum
    proves byte identity; origin is a provenance assertion, not the checksum."""

    __tablename__ = "artifact_references"
    __table_args__ = (
        UniqueConstraint("authority", "native_id", "version_id", name="uq_artifact_identity"),
    )

    artifact_id: Mapped[UUID] = mapped_column(
        ForeignKey("global_resource_registry.resource_id", ondelete="CASCADE"),
        primary_key=True,
        default=uuid4,
    )
    authority: Mapped[str] = mapped_column(String(100), nullable=False)
    native_id: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(200))
    size: Mapped[int | None] = mapped_column(Integer)
    checksum: Mapped[str | None] = mapped_column(String(64))
    version_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LiteratureReference(Base, TimestampMixin):
    """Immutable citation to an external publication."""

    __tablename__ = "literature_references"
    __table_args__ = (
        UniqueConstraint("authority", "native_id", name="uq_literature_authority_native"),
    )

    literature_id: Mapped[UUID] = mapped_column(
        ForeignKey("global_resource_registry.resource_id", ondelete="CASCADE"),
        primary_key=True,
        default=uuid4,
    )
    authority: Mapped[str] = mapped_column(String(100), nullable=False)
    native_id: Mapped[str] = mapped_column(String(300), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))


class ExternalReference(Base, TimestampMixin):
    """Resolver/cache metadata over an ExternalIdentity (never a re-stored
    authority/native_id, never a snapshot copy of the external payload)."""

    __tablename__ = "external_references"

    external_reference_id: Mapped[UUID] = mapped_column(
        ForeignKey("global_resource_registry.resource_id", ondelete="CASCADE"),
        primary_key=True,
        default=uuid4,
    )
    external_identity_id: Mapped[UUID] = mapped_column(
        ForeignKey("external_identities.external_identity_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    checksum: Mapped[str | None] = mapped_column(String(64))
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cache_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONType)


class GlobalProvenanceEdge(Base, TimestampMixin):
    """Global provenance edges #1-7. Owned by Evidence/Provenance, no
    project_id, never archived by a Project. Immutable; a wrong edge is corrected
    by a superseding edge (superseded_by). Endpoint kind validity (per
    relation_type) is a domain-service invariant; registry FKs guarantee each
    endpoint is a real global resource."""

    __tablename__ = "global_provenance_edges"
    __table_args__ = (CheckConstraint("source_id <> target_id", name="ck_global_edge_not_self"),)

    edge_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    relation_type: Mapped[str] = mapped_column(_relation_type, nullable=False)
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("global_resource_registry.resource_id"), nullable=False, index=True
    )
    source_kind: Mapped[str] = mapped_column(_resource_kind, nullable=False)
    target_id: Mapped[UUID] = mapped_column(
        ForeignKey("global_resource_registry.resource_id"), nullable=False, index=True
    )
    target_kind: Mapped[str] = mapped_column(_resource_kind, nullable=False)
    superseded_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("global_provenance_edges.edge_id"), nullable=True
    )
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("actors.actor_id"))


class Evidence(Base, TimestampMixin):
    """Project-scoped interpreted claim: source (0..1 reference/observation) +
    exactly one target (revision/decision/evidence) + interpretive fields.
    source/target are immutable; interpretive fields freeze once a COMMITTED
    Decision cites it or another Evidence targets it (derived, not stored)."""

    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            "(CASE WHEN target_revision_id IS NOT NULL THEN 1 ELSE 0 END"
            " + CASE WHEN target_decision_id IS NOT NULL THEN 1 ELSE 0 END"
            " + CASE WHEN target_evidence_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="ck_evidence_exactly_one_target",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(_evidence_kind, nullable=False)
    role: Mapped[str] = mapped_column(
        _evidence_role, nullable=False, default=EvidenceRole.PRIMARY_SUPPORT.value
    )
    label: Mapped[str | None] = mapped_column(String(200))
    interpretation: Mapped[str | None] = mapped_column(Text)
    polarity: Mapped[str] = mapped_column(_polarity, nullable=False, default=Polarity.NEUTRAL.value)
    confidence: Mapped[str | None] = mapped_column(_confidence)
    confidence_source: Mapped[str | None] = mapped_column(String(200))
    scope: Mapped[str | None] = mapped_column(String(500))
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_resource_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("global_resource_registry.resource_id"), nullable=True
    )
    source_kind: Mapped[str | None] = mapped_column(_resource_kind)
    target_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("global_resource_registry.resource_id"), nullable=True
    )
    target_decision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("decisions.id", ondelete="CASCADE"), nullable=True
    )
    target_evidence_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evidence.id", ondelete="CASCADE"), nullable=True
    )
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("actors.actor_id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Decision(Base, TimestampMixin):
    """Project-scoped conclusion with the draft -> committed promotion boundary.
    Status is exactly draft|committed (+ derived superseded). Draft cites/selects
    are mutable working state; commit materializes the immutable
    DecisionEvidence / DecisionTarget knowledge edges."""

    __tablename__ = "decisions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        _decision_status, nullable=False, default=DecisionStatus.DRAFT.value
    )
    next_actions: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    draft_cites: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, nullable=False, default=list)
    draft_selects: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, nullable=False, default=list)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("actors.actor_id"))
    committed_by: Mapped[UUID | None] = mapped_column(ForeignKey("actors.actor_id"))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DecisionEvidence(Base, TimestampMixin):
    """Project knowledge edge #10 (cites), with per-citation cited_as. Composite
    primary key; materialized only by the commit operation."""

    __tablename__ = "decision_evidence"

    decision_id: Mapped[UUID] = mapped_column(
        ForeignKey("decisions.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    evidence_id: Mapped[UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    cited_as: Mapped[str] = mapped_column(_cited_as, nullable=False)


class DecisionTarget(Base, TimestampMixin):
    """Project knowledge edge #8 (selects): a Decision targets a Series or a
    Revision, explicitly typed. Materialized only by commit."""

    __tablename__ = "decision_targets"
    __table_args__ = (UniqueConstraint("decision_id", "target_id", name="uq_decision_target_once"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    decision_id: Mapped[UUID] = mapped_column(
        ForeignKey("decisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_id: Mapped[UUID] = mapped_column(
        ForeignKey("global_resource_registry.resource_id"), nullable=False, index=True
    )
    target_kind: Mapped[str] = mapped_column(_resource_kind, nullable=False)


class DecisionSupersedes(Base, TimestampMixin):
    """Project knowledge edge #9 (supersedes): an immutable 0..1-out /
    0..1-in link between two Decisions in one Project."""

    __tablename__ = "decision_supersedes"
    __table_args__ = (
        UniqueConstraint("decision_id", name="uq_supersedes_out"),
        UniqueConstraint("superseded_decision_id", name="uq_supersedes_in"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision_id: Mapped[UUID] = mapped_column(
        ForeignKey("decisions.id", ondelete="CASCADE"), nullable=False
    )
    superseded_decision_id: Mapped[UUID] = mapped_column(
        ForeignKey("decisions.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("actors.actor_id"))


class ExternalProviderCredentialBinding(Base, TimestampMixin):
    """Actor-scoped (no project_id) non-secret credential binding.

    Records that this Actor holds a credential of `kind` for `provider_key`;
    the secret material lives at the opaque `secret_ref` in the Secret store —
    never in this row. `kind` is provider-declared free vocabulary, NOT a Core
    enum (ADR-0012). Revocation is a hard delete; rotation is an in-place
    `secret_ref` update (COLLABORATION_IDENTITY lifecycle).
    """

    __tablename__ = "external_provider_credential_bindings"
    __table_args__ = (
        UniqueConstraint(
            "actor_id",
            "provider_key",
            "kind",
            name="uq_credential_binding_actor_provider_kind",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("actors.actor_id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_key: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        # Never emit `secret_ref` into logs: it is opaque but must not leave the
        # durable binding column.
        return (
            f"<ExternalProviderCredentialBinding id={self.id!r} "
            f"actor_id={self.actor_id!r} provider_key={self.provider_key!r} kind={self.kind!r}>"
        )


class ToolInvocation(Base, TimestampMixin):
    """Project-scoped durable record of ONE Local Tool invocation that persisted a
    derived result (TODO.md sections 17/18: reproducibility, project activity,
    agent observability). Explicitly NOT a Run model: `RunReference` is the
    external REvoCompute execution identity card; `ToolInvocation` is the
    REvoLab-local operation record. `parameters` is the validated, bounded tool
    input (already schema-checked); secrets never enter it."""

    __tablename__ = "tool_invocations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    tool_version: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("actors.actor_id"))
    input_resource_ids: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    result_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    result_resource_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("global_resource_registry.resource_id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False)


class ProjectConversation(Base, TimestampMixin):
    """Actor x Project scoped durable working memory (Phase 9). A conversation is
    NOT a ScientificObject, a provenance node, or a GlobalResourceRegistry entry:
    it never enters the scientific graph and never becomes Project truth.

    Access must always verify the current Actor owns the conversation AND can
    currently read the Project AND the Project is active. A guessed UUID never
    grants access. Lifecycle is create / read / rename / archive only — no
    cross-user sharing semantics in this phase."""

    __tablename__ = "project_conversations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("actors.actor_id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationMessage(Base, TimestampMixin):
    """One durable bounded transcript entry: `role` is exactly user|assistant.
    `content` is conversational working memory — ALWAYS untrusted, never system
    authority. `termination_reason` and `tool_trace_summary` belong to an
    assistant message: the summary is a bounded INERT snapshot of the turn's
    tool-call outcomes (tool id + status + bounded error/refusal), never raw
    ToolResult payloads and never a second conversation truth.

    Persist only bounded user-visible state. Never: system prompt, trusted skill
    bodies, raw model/provider request/response, credentials, CredentialLease,
    ProjectContext/ToolCatalog serialization, or hidden reasoning."""

    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_conversation_message_seq"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("project_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(_conversation_role, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    termination_reason: Mapped[str | None] = mapped_column(_agent_termination, nullable=True)
    tool_trace_summary: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType)


class ActionRequest(Base, TimestampMixin):
    """Durable operational intent for ONE Agent-proposed explicit action (Phase 11).

    Persist intent, never authority (ADR-0017). This row records WHAT an Actor
    proposed — canonical `tool_id` plus the complete, schema-validated, bounded
    argument payload — so the proposal survives a reload and later human review.
    It is NOT permission to execute: it stores no authorization decision, no
    membership result, no provider-health snapshot, no capability availability, no
    credential material and no `secret_ref`, and execution re-derives all of that
    from current canonical state.

    It is also not a ScientificObject, Evidence, Decision, ProjectResourceLink,
    GlobalResourceRegistry entry, conversation message, Agent memory, ToolResult,
    or provider execution truth: it never enters the scientific graph and never
    duplicates REvoCompute's mutable run state. `result_run_id` / `result_decision_id`
    are the canonical RESULT references the action produced, when one exists
    (`result_decision_id` is the Decision produced by the sole local explicit
    action, `decision.commit`; a future local explicit action with a different
    result kind needs its own typed reference rather than a renamed FK).

    Ownership is the Phase-9 conversation lens: one owning Actor inside one Project.
    Another member of the same Project can never read, execute, or reject it, and a
    guessed UUID is not an existence oracle. The proposal-time autonomy /
    execution_class / side_effect_class columns are presentation metadata recorded
    for truthful display after reload; they are never consulted as authority.
    """

    __tablename__ = "action_requests"
    __table_args__ = (
        Index("ix_action_requests_actor_project", "actor_id", "project_id"),
        # A terminal SUCCESS always names the canonical result it produced: the
        # reference is the only way the action becomes observable, and a
        # `succeeded` row with no reference would be a state-machine lie. The
        # status value is interpolated from the enum, never re-typed.
        CheckConstraint(
            f"status <> '{ActionRequestStatus.SUCCEEDED.value}'"
            " OR result_run_id IS NOT NULL OR result_decision_id IS NOT NULL",
            name="ck_action_succeeded_has_result",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("actors.actor_id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Descriptive provenance of the proposal (which conversation produced it).
    # Never an authority grant: the owning Actor remains the access boundary.
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("project_conversations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    tool_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    autonomy: Mapped[str] = mapped_column(_agent_tool_autonomy, nullable=False)
    execution_class: Mapped[str] = mapped_column(_tool_execution_class, nullable=False)
    side_effect_class: Mapped[str] = mapped_column(_tool_side_effect_class, nullable=False)
    # The COMPLETE validated canonical input (never a truncated preview, never a
    # secret). Size-bounded at proposal time; an over-bound payload fails closed.
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    arguments_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        _action_request_status, nullable=False, default=ActionRequestStatus.PENDING.value
    )
    # Bounded, sanitized, non-secret explanation of a non-pending outcome.
    status_reason: Mapped[str | None] = mapped_column(String(500))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("global_resource_registry.resource_id", ondelete="SET NULL"), nullable=True
    )
    result_decision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("decisions.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProjectNote(Base, TimestampMixin):
    """Project-scoped shared working document (Phase 10).

    A Note is Project-shared working knowledge for the readable members of ONE
    Project. It is NOT a ScientificObject, a GlobalResourceRegistry entry, an
    Evidence claim, a Decision, a provenance node, or Agent memory, and it never
    enters the scientific graph. The Project is its namespace and lifecycle
    owner; archiving is non-destructive. The creating Actor is audit metadata,
    never exclusive ownership — there are no per-note ACLs.

    The latest revision is derived from `ProjectNoteRevision.revision_seq` (the
    max sequence); there is deliberately no mutable `current_revision_id`."""

    __tablename__ = "project_notes"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("actors.actor_id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectNoteRevision(Base, TimestampMixin):
    """One immutable Note body version. INSERT-only: editing a Note appends a new
    revision, it never rewrites an existing one. `body` is bounded Markdown/plain
    structured text and is ALWAYS untrusted Project data (never a block-editor
    ontology, never active content)."""

    __tablename__ = "project_note_revisions"
    __table_args__ = (
        UniqueConstraint("note_id", "revision_seq", name="uq_note_revision_seq"),
    )

    revision_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    note_id: Mapped[UUID] = mapped_column(
        ForeignKey("project_notes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("actors.actor_id"), nullable=False, index=True
    )


class NoteMention(Base, TimestampMixin):
    """A typed contextual reference from ONE Note revision to an existing
    Project-visible entity. It means only "this working document refers to this
    entity": it is NOT a RelationType, carries no scientific semantics
    (supports/derived_from/selects), and creates no provenance edge.

    Mirrors the frozen Evidence endpoint pattern: a global-registry resource is
    named by `target_resource_id` (+ its registry `target_kind`), while the
    Project-scoped Evidence/Decision targets use their own FKs. Exactly one
    target is set. Mentions belong to an immutable revision, so a target that
    later becomes unlinked/archived leaves an unresolved historical mention
    rather than rewriting the note."""

    __tablename__ = "note_mentions"
    __table_args__ = (
        UniqueConstraint("revision_id", "ordinal", name="uq_note_mention_ordinal"),
        CheckConstraint(
            "(CASE WHEN target_resource_id IS NOT NULL THEN 1 ELSE 0 END"
            " + CASE WHEN target_evidence_id IS NOT NULL THEN 1 ELSE 0 END"
            " + CASE WHEN target_decision_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="ck_note_mention_exactly_one_target",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("project_note_revisions.revision_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    target_resource_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("global_resource_registry.resource_id"), nullable=True, index=True
    )
    target_kind: Mapped[str | None] = mapped_column(_resource_kind)
    target_evidence_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evidence.id"), nullable=True, index=True
    )
    target_decision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("decisions.id"), nullable=True, index=True
    )
