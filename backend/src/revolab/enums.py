"""Core-owned closed vocabulary (single source of truth for domain enums).

These enums are the canonical wire values for REvoLab scientific semantics.
They are consumed by the models, the typed domain services, and the generated
API contracts. Nothing in Core may duplicate them (see ADR-0007/0010/0014).
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    MEMBER = "member"
    VIEWER = "viewer"


class ProjectVisibility(StrEnum):
    PRIVATE = "private"
    SHARED_WITH_MEMBERS = "shared_with_members"


class ResourceKind(StrEnum):
    """Global-resource kind labels (singular; concrete tables are plural)."""

    SCIENTIFIC_OBJECT_SERIES = "scientific_object_series"
    SCIENTIFIC_OBJECT_REVISION = "scientific_object_revision"
    RUN_REFERENCE = "run_reference"
    SESSION_REFERENCE = "session_reference"
    ARTIFACT_REFERENCE = "artifact_reference"
    LITERATURE_REFERENCE = "literature_reference"
    EXTERNAL_REFERENCE = "external_reference"


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


class RelationType(StrEnum):
    """Canonical closed relation enum (SCIENTIFIC_GRAPH.md); #1-7 are global
    provenance edges, #8-10 are project knowledge edges. `generated_by` is a
    derived traversal and is deliberately absent."""

    VARIANT_OF = "variant_of"  # 1
    DERIVED_FROM = "derived_from"  # 2
    REPRESENTS = "represents"  # 3
    EVALUATES = "evaluates"  # 4
    CONSUMED_AS_INPUT_BY = "consumed_as_input_by"  # 5
    PRODUCED = "produced"  # 6
    IMPORTED_AS = "imported_as"  # 7
    SELECTS = "selects"  # 8
    SUPERSEDES = "supersedes"  # 9
    CITES = "cites"  # 10


class EvidenceKind(StrEnum):
    EXPERIMENTAL = "experimental"
    LITERATURE = "literature"
    COMPUTATION = "computation"
    OBSERVATION = "observation"
    NOTE = "note"


class EvidenceRole(StrEnum):
    PRIMARY_SUPPORT = "primary_support"
    CORROBORATING = "corroborating"
    BACKGROUND = "background"
    METHODOLOGY = "methodology"
    NEGATIVE_RESULT = "negative_result"
    HYPOTHESIS = "hypothesis"


class Polarity(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    NEUTRAL = "neutral"


class Confidence(StrEnum):
    LOW = "low"
    MED = "med"
    HIGH = "high"


class CitedAs(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class DecisionStatus(StrEnum):
    DRAFT = "draft"
    COMMITTED = "committed"


class EvidenceTargetKind(StrEnum):
    SCIENTIFIC_OBJECT_REVISION = "scientific_object_revision"
    DECISION = "decision"
    EVIDENCE = "evidence"


# Legal Evidence source kinds (a subset of the registry kinds): the frozen source
# set is run/session/artifact/literature/external reference or a revision.
LEGAL_EVIDENCE_SOURCE_KINDS = frozenset(
    {
        ResourceKind.RUN_REFERENCE,
        ResourceKind.SESSION_REFERENCE,
        ResourceKind.ARTIFACT_REFERENCE,
        ResourceKind.LITERATURE_REFERENCE,
        ResourceKind.EXTERNAL_REFERENCE,
        ResourceKind.SCIENTIFIC_OBJECT_REVISION,
    }
)
