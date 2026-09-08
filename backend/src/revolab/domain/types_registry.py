"""Core-owned ScientificObject type registry.

The typed scientific payload hangs on the revision, is discriminated by one
Core-owned `object_type`, and is validated against a per-type Pydantic schema —
never an unvalidated JSON blob. Physical representation (ADR-0015) is a
schema-versioned JSONB column populated only through these validators.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from revolab.domain.errors import ValidationError
from revolab.enums import ObjectType

SCHEMA_VERSION = 1


class ProteinPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organism: str | None = None
    source_sequence_ref: str | None = None
    chain: str | None = None


class SequencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str | None = None
    sequence: str | None = None


class StructurePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: float | None = None
    method: str | None = None
    pdb_id: str | None = None
    coordinates_ref: str | None = None
    ligand_ref: str | None = None


class VariantPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_allele: str | None = None
    alt_allele: str | None = None
    position: int | None = None
    effect: str | None = None


class LigandPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    smiles: str
    source: str | None = None


class ComplexPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    components: list[str] = Field(default_factory=list)


class DatasetPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modality: str | None = None
    format: str | None = None


class AssayPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    readout: str | None = None
    method: str | None = None


class ConstructPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backbone_ref: str | None = None
    mutations: str | None = None


class OtherPayload(BaseModel):
    """Explicit fallback for genuinely untyped things — still constrained to
    JSON primitives, never a dumping ground for nested business objects."""

    model_config = ConfigDict(extra="forbid")

    data: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


OBJECT_TYPE_REGISTRY: dict[ObjectType, type[BaseModel]] = {
    ObjectType.PROTEIN: ProteinPayload,
    ObjectType.SEQUENCE: SequencePayload,
    ObjectType.STRUCTURE: StructurePayload,
    ObjectType.VARIANT: VariantPayload,
    ObjectType.LIGAND: LigandPayload,
    ObjectType.COMPLEX: ComplexPayload,
    ObjectType.DATASET: DatasetPayload,
    ObjectType.ASSAY: AssayPayload,
    ObjectType.CONSTRUCT: ConstructPayload,
    ObjectType.OTHER: OtherPayload,
}


def validate_payload(object_type: ObjectType, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a raw payload against its per-type schema and return the cleaned
    value for persistence. Raises ValidationError on any unknown/wrong field."""
    model = OBJECT_TYPE_REGISTRY[object_type]
    try:
        return model.model_validate(payload).model_dump()
    except Exception as exc:  # pydantic.ValidationError (and friends)
        raise ValidationError(f"invalid {object_type.value} payload: {exc}") from exc
