"""Canonical explicit-action input models (Phase 11).

Every `explicit_action` Project Tool has exactly ONE authoritative input model,
and each boundary resolves it from the owner of that fact — never from a
hand-maintained copy:

- a **local** explicit action resolves it from its registered
  `LocalToolSpec.input_model` (`LocalToolRegistry.explicit_action_input_model`),
  the same model the closed `LocalToolRuntime` validates against;
- a **remote** (provider-projected) explicit action has no registry spec, so its
  model is mapped here by the stable capability SUFFIX (the provider key prefix is
  dynamic provider data). This module is the ONE place that linkage exists.

Both the Agent proposal boundary and the human execution boundary call the same
two owners, so a schema change takes effect immediately on both sides and no
hand-copied duplicate schema exists. The module is a leaf: it imports only
`revolab.schemas` (canonical wire models) and Pydantic, so it can be consumed by
the Agent runtime, the Tool Harness and the application action service without an
import cycle or provider vocabulary leaking into Core.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from revolab.schemas import ComputeSubmissionCreate

# The one task-submission capability suffix Core recognizes as an external compute
# action. The provider key prefix is taken from the live catalog descriptor, never
# parsed out of the tool id.
COMPUTE_SUBMIT_SUFFIX = ".compute.submit"

# Remote explicit actions: tool-id SUFFIX -> canonical input model.
REMOTE_EXPLICIT_INPUT_MODEL_SUFFIXES: dict[str, type[BaseModel]] = {
    COMPUTE_SUBMIT_SUFFIX: ComputeSubmissionCreate,
}

# Bound on the persisted executable argument payload. The complete validated
# payload must fit; an over-bound action fails closed rather than being stored
# partially (a truncated preview is not executable state).
MAX_ACTION_ARGUMENT_CHARS = 20_000


def remote_explicit_action_input_model(tool_id: str) -> type[BaseModel] | None:
    """The canonical input model for a REMOTE explicit-action tool id, or None
    when the id is not a known remote explicit action (in which case the boundary
    must fail closed). A local explicit action resolves its model from its
    registered `LocalToolSpec` instead."""
    for suffix, model in REMOTE_EXPLICIT_INPUT_MODEL_SUFFIXES.items():
        if tool_id.endswith(suffix):
            return model
    return None


def explicit_arguments_match_provider(
    provider_key: str | None, arguments: Mapping[str, Any]
) -> bool:
    """Whether a validated explicit-action payload names the SAME provider the Tool
    id resolves to.

    A remote explicit action carries provider identity twice — in the canonical
    `tool_id` (the authority execution uses) and in the canonical input model's
    `provider_key` field (what a human reads on the authorization surface). They
    must agree: otherwise a human would authorize the operation under a false
    description of the external side effect. A local action has no provider identity
    (`provider_key is None`) and is exempt.
    """
    if provider_key is None:
        return True
    declared = arguments.get("provider_key")
    return declared is None or declared == provider_key


__all__ = [
    "COMPUTE_SUBMIT_SUFFIX",
    "MAX_ACTION_ARGUMENT_CHARS",
    "REMOTE_EXPLICIT_INPUT_MODEL_SUFFIXES",
    "explicit_arguments_match_provider",
    "remote_explicit_action_input_model",
]
