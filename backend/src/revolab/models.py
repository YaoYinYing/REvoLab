from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from revolab.db import Base


class ObjectType(StrEnum):
    PROTEIN = "protein"
    SEQUENCE = "sequence"
    STRUCTURE = "structure"
    VARIANT = "variant"
    LIGAND = "ligand"
    COMPLEX = "complex"
    DATASET = "dataset"
    ASSAY = "assay"
    CONSTRUCT = "construct"
    OTHER = "other"


class EvidenceType(StrEnum):
    RUN = "run"
    ARTIFACT = "artifact"
    LITERATURE = "literature"
    EXPERIMENTAL = "experimental"
    NOTE = "note"


class RelationPolarity(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    DESCRIBES = "describes"
    DERIVED_FROM = "derived_from"
    VARIANT_OF = "variant_of"
    RELATED_TO = "related_to"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    objects: Mapped[list[ScientificObject]] = relationship(back_populates="project", cascade="all, delete-orphan")
    relations: Mapped[list[Relation]] = relationship(back_populates="project", cascade="all, delete-orphan")
    evidence: Mapped[list[Evidence]] = relationship(back_populates="project", cascade="all, delete-orphan")
    decisions: Mapped[list[Decision]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ScientificObject(Base):
    __tablename__ = "scientific_objects"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    object_type: Mapped[str] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("scientific_objects.id", ondelete="CASCADE"), index=True
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    project: Mapped[Project] = relationship(back_populates="objects")
    parent: Mapped[ScientificObject | None] = relationship(
        remote_side="ScientificObject.id", back_populates="children"
    )
    children: Mapped[list[ScientificObject]] = relationship(back_populates="parent", cascade="all, delete-orphan")


class Relation(Base):
    __tablename__ = "relations"
    __table_args__ = (UniqueConstraint("project_id", "source_id", "target_id", "relation_type"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("scientific_objects.id", ondelete="CASCADE"))
    target_id: Mapped[UUID] = mapped_column(ForeignKey("scientific_objects.id", ondelete="CASCADE"))
    relation_type: Mapped[str] = mapped_column(String(50))
    rationale: Mapped[str | None] = mapped_column(Text)
    project: Mapped[Project] = relationship(back_populates="relations")


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    evidence_type: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(String(100))
    external_id: Mapped[str | None] = mapped_column(String(300))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    project: Mapped[Project] = relationship(back_populates="evidence")


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    statement: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="open")
    next_actions: Mapped[list[str]] = mapped_column(JSON, default=list)
    project: Mapped[Project] = relationship(back_populates="decisions")


class DecisionEvidence(Base):
    __tablename__ = "decision_evidence"
    __table_args__ = (UniqueConstraint("decision_id", "evidence_id"),)

    decision_id: Mapped[UUID] = mapped_column(ForeignKey("decisions.id", ondelete="CASCADE"), primary_key=True)
    evidence_id: Mapped[UUID] = mapped_column(ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True)
