"""Pydantic request/response models for the project-scoped API surface.

FastAPI owns the canonical domain schemas/enums (ADR-0014). No enum or contract
is duplicated by hand into the frontend or into project skills.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, model_validator

from revolab.enums import (
    CREDENTIAL_KIND_PATTERN,
    PROVIDER_KEY_PATTERN,
    CapabilityAvailability,
    CapabilityKind,
    CitedAs,
    Confidence,
    DecisionStatus,
    EvidenceKind,
    EvidenceRole,
    EvidenceTargetKind,
    ObjectType,
    Polarity,
    ProviderRuntimeHealth,
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


# ---------------------------------------------------------------------------
# Provider Catalog + Credential management (Phase 3)
# ---------------------------------------------------------------------------


class CredentialPresenceRead(BaseModel):
    kind: str  # provider-declared free vocabulary, never a Core enum
    present: bool


class ProviderCapabilityRead(BaseModel):
    """One realized capability of a provider, with the calling Actor's derived
    availability for that specific capability (the policy component of the
    availability formula is operation-specific, so availability lives here)."""

    kind: CapabilityKind
    availability: CapabilityAvailability


class ProviderRead(BaseModel):
    """One non-secret Provider Catalog entry as seen by the calling Actor in a
    Project. Never contains secret material, `secret_ref`, another Actor's
    bindings, host env, or credential-store implementation detail."""

    key: str
    name: str
    description: str | None = None
    required_credential_kinds: list[str] = Field(default_factory=list)
    health: ProviderRuntimeHealth
    credential_presence: list[CredentialPresenceRead] = Field(default_factory=list)
    capabilities: list[ProviderCapabilityRead] = Field(default_factory=list)


class CredentialProvision(BaseModel):
    provider_key: str = Field(pattern=PROVIDER_KEY_PATTERN)
    kind: str = Field(pattern=CREDENTIAL_KIND_PATTERN)
    secret_value: SecretStr  # write-only: never echoed, never serialized back


class CredentialReplace(BaseModel):
    secret_value: SecretStr


class CredentialBindingRead(BaseModel):
    """Presence/status only — no `secret_ref`, no secret material."""

    provider_key: str
    kind: str


# ---------------------------------------------------------------------------
# Compute (Phase 4) — provider-neutral request/response models
# ---------------------------------------------------------------------------


class ComputeTaskKindRead(BaseModel):
    """One discoverable task kind. All fields are provider data."""

    kind_id: str
    display_name: str
    description: str | None = None
    category: str | None = None


class ComputeInputSpecRead(BaseModel):
    label: str | None = None
    required: bool = False
    multiple: bool = False
    max_files: int | None = None
    accepted_extensions: list[str] = Field(default_factory=list)


class ComputeTaskKindSchemaRead(BaseModel):
    """Schema-as-data for one task kind (parameters + input contract)."""

    kind_id: str
    display_name: str
    description: str | None = None
    parameter_schema: dict[str, Any]
    input_spec: ComputeInputSpecRead


class ComputeInputCreate(BaseModel):
    kind: ResourceKind
    resource_id: UUID
    role: str | None = None

    @model_validator(mode="after")
    def _legal_input_kind(self) -> ComputeInputCreate:
        if self.kind not in {
            ResourceKind.SCIENTIFIC_OBJECT_REVISION,
            ResourceKind.ARTIFACT_REFERENCE,
        }:
            raise ValueError("compute input kind must be a revision or an artifact reference")
        return self


class ComputeSubmissionCreate(BaseModel):
    provider_key: str = Field(pattern=PROVIDER_KEY_PATTERN)
    task_kind: str = Field(min_length=1, max_length=300)
    inputs: list[ComputeInputCreate] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


class ComputeSubmissionRead(BaseModel):
    run_resource_id: UUID
    authority: str
    native_id: str
    task_type: str | None = None
    consumed_edges: list[UUID] = Field(default_factory=list)


class ComputeRunStatusRead(BaseModel):
    """Live, on-demand run state. `available=False` is the honest representation
    of a provider that is currently unreachable; the stored RunReference is
    never invalidated by a transient outage."""

    run_resource_id: UUID
    authority: str
    native_id: str
    available: bool
    status: str | None = None  # provider status string as opaque data
    detail: str | None = None


class ComputeArtifactRead(BaseModel):
    resource_id: UUID
    authority: str
    native_id: str
    content_type: str | None = None
    size: int | None = None
    checksum: str | None = None
    version_id: str | None = None
    revoked_at: datetime | None = None
