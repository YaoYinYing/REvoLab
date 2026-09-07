from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from revolab.models import EvidenceType, ObjectType, RelationPolarity


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class ProjectSummary(ProjectCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime


class ObjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    object_type: ObjectType
    description: str | None = None
    parent_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ObjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID
    name: str
    object_type: ObjectType
    description: str | None = None
    parent_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_model(cls, item: Any) -> ObjectRead:
        return cls(
            id=item.id,
            project_id=item.project_id,
            name=item.name,
            object_type=item.object_type,
            description=item.description,
            parent_id=item.parent_id,
            metadata=item.metadata_json,
        )


class RelationCreate(BaseModel):
    source_id: UUID
    target_id: UUID
    relation_type: RelationPolarity
    rationale: str | None = None

    @model_validator(mode="after")
    def distinct_objects(self) -> RelationCreate:
        if self.source_id == self.target_id:
            raise ValueError("a relation must connect two distinct scientific objects")
        return self


class RelationRead(RelationCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID


class EvidenceCreate(BaseModel):
    evidence_type: EvidenceType
    label: str = Field(min_length=1, max_length=200)
    summary: str | None = None
    provider: str | None = Field(default=None, min_length=1, max_length=100)
    external_id: str | None = Field(default=None, min_length=1, max_length=300)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_provider_reference(self) -> EvidenceCreate:
        if self.evidence_type in {EvidenceType.RUN, EvidenceType.ARTIFACT} and (
            self.provider is None or self.external_id is None
        ):
            raise ValueError("run and artifact evidence require provider and external_id")
        if (self.provider is None) != (self.external_id is None):
            raise ValueError("provider and external_id must be supplied together")
        return self


class EvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID
    evidence_type: EvidenceType
    label: str
    summary: str | None = None
    provider: str | None = None
    external_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_model(cls, item: Any) -> EvidenceRead:
        return cls(
            id=item.id,
            project_id=item.project_id,
            evidence_type=item.evidence_type,
            label=item.label,
            summary=item.summary,
            provider=item.provider,
            external_id=item.external_id,
            metadata=item.metadata_json,
        )


class DecisionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1)
    status: str = Field(default="open", min_length=1, max_length=40)
    next_actions: list[str] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)


class DecisionRead(DecisionCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID

    @classmethod
    def from_model(cls, item: Any, evidence_ids: list[UUID] | None = None) -> DecisionRead:
        return cls(
            id=item.id,
            project_id=item.project_id,
            title=item.title,
            statement=item.statement,
            status=item.status,
            next_actions=item.next_actions,
            evidence_ids=evidence_ids or [],
        )


class ProjectGraph(ProjectSummary):
    objects: list[ObjectRead] = Field(default_factory=list)
    relations: list[RelationRead] = Field(default_factory=list)
    evidence: list[EvidenceRead] = Field(default_factory=list)
    decisions: list[DecisionRead] = Field(default_factory=list)
