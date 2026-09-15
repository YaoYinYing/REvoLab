"""Canonical explicit-action input models (Phase 11).

ONE place maps a Project Tool's `explicit_action` identity to the canonical
Pydantic input model that validates its arguments — for both the Agent proposal
boundary and the human execution boundary. The Agent loop classifies a proposed
tool call with it, and execution revalidates the persisted arguments against the
CURRENT model resolved here, so a schema change takes effect immediately and no
hand-copied duplicate schema exists.

This module is a leaf: it imports only `revolab.schemas` (canonical wire models)
and Pydantic, so it can be consumed by the Agent runtime, the Tool Harness and the
application action service without an import cycle or provider vocabulary leaking
into Core. A LOCAL tool's model lives on its registered `LocalToolSpec`; only
REMOTE (provider-projected) explicit actions need a suffix mapping here, because
the provider key is dynamic (`{provider_key}.compute.submit`).
"""

from __future__ import annotations

from pydantic import BaseModel

from revolab.schemas import ComputeSubmissionCreate, DecisionCommitCreate

# Local explicit actions: stable tool id -> canonical input model. The registry
# spec is authoritative for the *descriptor*; this map is authoritative for the
# proposal/execution validation of a specific stable id.
LOCAL_EXPLICIT_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "decision.commit": DecisionCommitCreate,
}

# Remote explicit actions: tool-id SUFFIX -> canonical input model. The provider
# key prefix is dynamic, so only the capability-suffix is stable.
REMOTE_EXPLICIT_INPUT_MODEL_SUFFIXES: dict[str, type[BaseModel]] = {
    ".compute.submit": ComputeSubmissionCreate,
}

# Bound on the persisted executable argument payload. The complete validated
# payload must fit; an over-bound action fails closed rather than being stored
# partially (a truncated preview is not executable state).
MAX_ACTION_ARGUMENT_CHARS = 20_000


def explicit_action_input_model(tool_id: str) -> type[BaseModel] | None:
    """The canonical input model for a tool id, or None when the id is not a
    known explicit action (in which case the boundary must fail closed)."""
    local = LOCAL_EXPLICIT_INPUT_MODELS.get(tool_id)
    if local is not None:
        return local
    for suffix, model in REMOTE_EXPLICIT_INPUT_MODEL_SUFFIXES.items():
        if tool_id.endswith(suffix):
            return model
    return None


__all__ = [
    "LOCAL_EXPLICIT_INPUT_MODELS",
    "MAX_ACTION_ARGUMENT_CHARS",
    "REMOTE_EXPLICIT_INPUT_MODEL_SUFFIXES",
    "explicit_action_input_model",
]
