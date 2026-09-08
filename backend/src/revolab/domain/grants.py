"""Core shared authority primitive: the MutationGrant value object.

This dataclass is deliberately in its own neutral module so domain commands can
accept it without importing the Identity module (the DAG stays honest: SO/EP/KD
consume the pre-validated grant; Identity derives it at the command boundary).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class MutationGrant:
    """A pre-validated authority token issued at the command boundary."""

    resource_id: UUID
    steward_project_id: UUID
    actor_id: UUID
    role: str
    purpose: str
