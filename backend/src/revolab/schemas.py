"""Pydantic request/response models for the project-scoped API surface.

FastAPI owns the canonical domain schemas/enums (ADR-0014). No enum or contract
is duplicated by hand into the frontend or into project skills.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from revolab.enums import (
    CitedAs,
    Confidence,
    DecisionStatus,
    EvidenceKind,
    EvidenceRole,
    EvidenceTargetKind,
    ObjectType,
    Polarity,
    RelationType,
    ResourceKind,
    Role,
)

# ---------------------------------------------------------------------------
# Identity / Project
# ---------------------------------------------------------------------------


class ActorRead(BaseModel):
    actor_id: UUID


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class ProjectRead(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    visibility: str
    created_at: datetime
    deleted_at: datetime | None = None


class MembershipCreate(BaseModel):
    actor_id: UUID
    role: Role


class MembershipRead(BaseModel):
    project_id: UUID
    actor_id: UUID
    role: Role


# ---------------------------------------------------------------------------
# Scientific objects
# ---------------------------------------------------------------------------


class ObjectCreate(BaseModel):
    object_type: ObjectType
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    folder: str | None = None


class RevisionCreate(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


class SeriesPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class ImportCreate(BaseModel):
    source_kind: ResourceKind
    source_id: UUID
    payload: dict[str, Any] = Field(default_factory=dict)


class ExternalIdentityAttach(BaseModel):
    authority: str = Field(min_length=1, max_length=100)
    native_id: str = Field(min_length=1, max_length=300)
    qualifier: str = Field(default="identity", max_length=100)
    kind: str | None = None
    is_canonical: bool = False


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


class RunReferenceCreate(BaseModel):
    authority: str = Field(min_length=1, max_length=100)
    native_id: str = Field(min_length=1, max_length=300)
    task_type: str | None = None
    input_parameter_digest: str | None = None


class SessionReferenceCreate(BaseModel):
    authority: str = Field(min_length=1, max_length=100)
    native_id: str = Field(min_length=1, max_length=300)


class ArtifactReferenceCreate(BaseModel):
    authority: str = Field(min_length=1, max_length=100)
    native_id: str = Field(min_length=1, max_length=300)
    content_type: str | None = None
    size: int | None = None
    checksum: str | None = None
    version_id: str | None = None


class LiteratureReferenceCreate(BaseModel):
    authority: str = Field(min_length=1, max_length=100)
    native_id: str = Field(min_length=1, max_length=300)
    title: str | None = None


class ExternalReferenceCreate(BaseModel):
    authority: str = Field(min_length=1, max_length=100)
    native_id: str = Field(min_length=1, max_length=300)
    kind: str | None = None
    checksum: str | None = None
    cache_metadata: dict[str, Any] | None = None


class ReferenceRead(BaseModel):
    resource_id: UUID
    resource_kind: str
    authority: str
    native_id: str
    checksum: str | None = None
    size: int | None = None
    content_type: str | None = None
    version_id: str | None = None
    task_type: str | None = None
    title: str | None = None
    created_at: datetime | None = None
    revoked_at: datetime | None = None


# ---------------------------------------------------------------------------
# Edges (typed operations; no generic writer)
# ---------------------------------------------------------------------------


class SeriesEdgeCreate(BaseModel):
    source_series_id: UUID
    target_series_id: UUID


class RevisionEdgeCreate(BaseModel):
    source_revision_id: UUID
    target_revision_id: UUID


class InputConsumedCreate(BaseModel):
    source_id: UUID
    target_id: UUID


class ProducedCreate(BaseModel):
    source_id: UUID
    artifact_id: UUID


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


class EvidenceCreate(BaseModel):
    kind: EvidenceKind
    role: EvidenceRole = EvidenceRole.PRIMARY_SUPPORT
    label: str | None = None
    interpretation: str | None = None
    polarity: Polarity = Polarity.NEUTRAL
    confidence: Confidence | None = None
    confidence_source: str | None = None
    scope: str | None = None
    source_kind: ResourceKind | None = None
    source_id: UUID | None = None
    target_kind: EvidenceTargetKind
    target_id: UUID

    @model_validator(mode="after")
    def source_pairing(self) -> EvidenceCreate:
        if (self.source_id is None) != (self.source_kind is None):
            raise ValueError("source_id and source_kind must be supplied together")
        return self


class EvidencePatch(BaseModel):
    role: EvidenceRole | None = None
    label: str | None = None
    interpretation: str | None = None
    polarity: Polarity | None = None
    confidence: Confidence | None = None
    confidence_source: str | None = None
    scope: str | None = None

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> EvidencePatch:
        # `role` and `polarity` are non-nullable columns; omission is allowed,
        # an explicit null is illegal and must not reach the domain service.
        for field in ("role", "polarity"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


class CitationCreate(BaseModel):
    evidence_id: UUID
    cited_as: CitedAs = CitedAs.SUPPORTS


class SelectTargetCreate(BaseModel):
    target_id: UUID
    target_kind: ResourceKind


class DecisionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1)
    next_actions: list[str] = Field(default_factory=list)
    cites: list[CitationCreate] = Field(default_factory=list)
    selects: list[SelectTargetCreate] = Field(default_factory=list)


class DecisionPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    statement: str | None = Field(default=None, min_length=1)
    next_actions: list[str] | None = None
    cites: list[CitationCreate] | None = None
    selects: list[SelectTargetCreate] | None = None


class SupersedeCreate(BaseModel):
    superseding_decision_id: UUID


# ---------------------------------------------------------------------------
# Shared response envelope
# ---------------------------------------------------------------------------


class SeriesRead(BaseModel):
    series_id: UUID
    object_type: ObjectType
    name: str
    description: str | None = None
    created_at: datetime | None = None
    archived_at: datetime | None = None


class RevisionRead(BaseModel):
    revision_id: UUID
    series_id: UUID
    revision_seq: int
    object_type: ObjectType
    schema_version: int
    payload: dict[str, Any]
    checksum: str
    created_at: datetime | None = None


class EdgeRead(BaseModel):
    """A single global provenance edge as returned inside the object-detail
    aggregate. Edges are read-only: there is no generic writer."""

    edge_id: UUID
    relation_type: RelationType
    source_id: UUID
    source_kind: ResourceKind
    target_id: UUID
    target_kind: ResourceKind


class ObjectDetailProvenance(BaseModel):
    inbound: list[EdgeRead]
    outbound: list[EdgeRead]


class ObjectSummaryRead(BaseModel):
    """Collection-list projection of a visible Series (summary mode)."""

    series_id: UUID
    object_type: ObjectType
    name: str
    description: str | None = None
    created_at: datetime | None = None
    archived_at: datetime | None = None
    latest_revision: RevisionRead | None = None


class ObjectDetailRead(BaseModel):
    """The first-class object-detail aggregate (Workspace IA single contract)."""

    series: SeriesRead
    visible_revisions: list[RevisionRead]
    latest_revision_seq: int | None = None
    provenance: ObjectDetailProvenance
    evidence: list[EvidenceRead]
    decisions: list[DecisionRead]


class EvidenceRead(BaseModel):
    id: UUID
    project_id: UUID
    kind: EvidenceKind
    role: EvidenceRole
    label: str | None = None
    interpretation: str | None = None
    polarity: Polarity
    confidence: Confidence | None = None
    confidence_source: str | None = None
    scope: str | None = None
    source_kind: ResourceKind | None = None
    source_id: UUID | None = None
    target_kind: EvidenceTargetKind
    target_id: UUID
    created_at: datetime | None = None
    frozen: bool = False


class DecisionRead(BaseModel):
    id: UUID
    project_id: UUID
    title: str
    statement: str
    status: DecisionStatus
    next_actions: list[str]
    cites: list[dict[str, Any]]
    selects: list[dict[str, Any]]
    superseded: bool = False
    superseded_by: UUID | None = None
    created_at: datetime | None = None
    committed_at: datetime | None = None
