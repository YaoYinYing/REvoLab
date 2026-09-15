"""Deterministic scripted ModelBackend for tests and the browser vertical slice.

This fake lives at the EXTERNAL model boundary: it receives the same neutral
`ModelRequest` a real model receives (trusted system instructions, bounded
history, the clearly-delimited untrusted project-data block, tool specs) and
returns tool calls / final messages that drive the REAL Agent loop. It exercises
the real ContextBuilder, skill loading, ToolCatalog, tool-call validation,
LocalToolRuntime, authority logic, and the Agent-turn API — the boundaries the
fake must NOT replace.

The default script implements the Phase-8 vertical slice over a tabular artifact:

    1. `table.describe`   (automatic, real local tool)
    2. `decision.record_draft` (policy, real typed domain DRAFT)
    3. a final conversational response

Resource ids are read from the untrusted project-data block the real prompt
assembler produced, so the fake never invents identifiers.
"""

from __future__ import annotations

import json
from typing import Any

from revolab.agent.model_backend import (
    ModelRequest,
    ModelResponse,
    ModelToolCall,
)
from revolab.agent.prompt import _DATA_CLOSE, _DATA_OPEN
from revolab.testing.fake_compute import FAKE_PROVIDER_KEY


def _extract_context(request: ModelRequest) -> dict[str, Any]:
    for message in request.messages:
        if message.role != "user" or not message.content:
            continue
        text = message.content
        start = text.find(_DATA_OPEN)
        end = text.find(_DATA_CLOSE)
        if start == -1 or end == -1 or end <= start:
            continue
        try:
            parsed = json.loads(text[start + len(_DATA_OPEN) : end])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _first_artifact_id(context: dict[str, Any]) -> str | None:
    for reference in context.get("references") or []:
        if reference.get("resource_kind") == "artifact_reference":
            value = reference.get("resource_id")
            return value if isinstance(value, str) else None
    return None


def _first_series_id(context: dict[str, Any]) -> str | None:
    for series in context.get("series") or []:
        value = series.get("series_id")
        return value if isinstance(value, str) else None
    return None


def _first_revision_id(context: dict[str, Any]) -> str | None:
    for revision in context.get("revisions") or []:
        value = revision.get("revision_id")
        return value if isinstance(value, str) else None
    return None


# A deterministic message-level trigger so the Phase-11 browser slice does not
# depend on the process-wide scripted turn counter (several browser specs share
# one backend process). Server-owned, test-only.
_COMPUTE_PROPOSAL_TRIGGER = "propose a compute submission"


def _latest_user_instruction(request: ModelRequest) -> str:
    """The most recent plain user message — never the delimited data block and
    never the bounded-history preamble. The fake must not read authority from
    project data, only the human's own instruction."""
    for message in reversed(request.messages):
        if message.role != "user" or not message.content:
            continue
        if _DATA_OPEN in message.content or _DATA_CLOSE in message.content:
            continue
        if message.content.startswith("The following is bounded transient"):
            continue
        return message.content
    return ""


def _first_note(context: dict[str, Any]) -> dict[str, Any] | None:
    for note in context.get("notes") or []:
        if isinstance(note, dict):
            return note
    return None


class ScriptedModelBackend:
    """A deterministic ModelBackend. `steps` is an optional list of explicit
    turn dicts; when exhausted (or absent) the default Phase-8 slice runs."""

    def __init__(self, steps: list[dict[str, Any]] | None = None) -> None:
        self._steps = steps
        self.request_count = 0
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        context = _extract_context(request)
        instruction = _latest_user_instruction(request).lower()
        if self._steps is None and _COMPUTE_PROPOSAL_TRIGGER in instruction:
            # A deterministic, counter-INDEPENDENT scripted path (the Phase-11
            # browser slice). It deliberately does not advance the default
            # script's turn counter: several spec files share one backend process,
            # and shifting that counter would change the Phase-8 slice's script.
            return self._compute_proposal_turn(request, context)
        index = self.request_count
        self.request_count += 1
        if self._steps is not None and index < len(self._steps):
            return self._emit(self._steps[index], context)
        return self._default_turn(index, context)

    def _compute_proposal_turn(
        self, request: ModelRequest, context: dict[str, Any]
    ) -> ModelResponse:
        """Propose ONE explicit compute submission per user message.

        The Agent loop may call the model again within the same turn (to let it
        narrate the refused action); a proposal is emitted only while the turn has
        no assistant tool-call yet, so exactly one durable Action Request is
        produced per instruction.
        """
        already_proposed = any(
            message.role == "assistant" and message.tool_calls for message in request.messages
        )
        revision_id = _first_revision_id(context)
        if already_proposed or revision_id is None:
            return ModelResponse(
                finish="stop",
                content=(
                    "I proposed the compute submission as an explicit action. It was NOT "
                    "executed: a human must authorize it."
                ),
            )
        arguments = {
            "provider_key": FAKE_PROVIDER_KEY,
            "task_kind": "tabular",
            "inputs": [
                {
                    "kind": "scientific_object_revision",
                    "resource_id": revision_id,
                    "role": None,
                }
            ],
            "params": {"rows": 4},
        }
        return ModelResponse(
            finish="tool_calls",
            tool_calls=(
                ModelToolCall(
                    id="call_compute_submit",
                    name=f"{FAKE_PROVIDER_KEY}.compute.submit",
                    arguments_raw=json.dumps(arguments),
                    arguments=arguments,
                ),
            ),
        )

    def _default_turn(self, index: int, context: dict[str, Any]) -> ModelResponse:
        artifact_id = _first_artifact_id(context)
        if artifact_id is None:
            # A Note-only selection is a self-contained Phase-10 turn: answer
            # deterministically from the untrusted context instead of depending on
            # the process-wide scripted turn counter (several browser specs share
            # one backend process).
            note = _first_note(context)
            if note is not None:
                return ModelResponse(
                    finish="stop",
                    content=(
                        f'I read the selected Project note "{note.get("title")}" '
                        f'(revision {note.get("revision_seq")}). It is shared working '
                        "knowledge, not project truth."
                    ),
                )
            return ModelResponse(finish="stop", content="No artifact was selected in context.")
        if index == 0:
            return ModelResponse(
                finish="tool_calls",
                tool_calls=(
                    ModelToolCall(
                        id="call_table_describe",
                        name="table.describe",
                        arguments_raw=json.dumps({"artifact_id": artifact_id}),
                        arguments={"artifact_id": artifact_id},
                    ),
                ),
            )
        if index == 1:
            series_id = _first_series_id(context)
            selects = (
                [{"target_id": series_id, "target_kind": "scientific_object_series"}]
                if series_id
                else []
            )
            draft = {
                "title": "Draft conclusion from table analysis",
                "statement": "The selected table supports a preliminary conclusion recorded as a draft.",
                "next_actions": ["validate experimentally"],
                "cites": [],
                "selects": selects,
            }
            return ModelResponse(
                finish="tool_calls",
                tool_calls=(
                    ModelToolCall(
                        id="call_record_draft",
                        name="decision.record_draft",
                        arguments_raw=json.dumps(draft),
                        arguments=draft,
                    ),
                ),
            )
        return ModelResponse(
            finish="stop",
            content=(
                "I described the table and recorded a Decision DRAFT. "
                "It is not committed project truth until an authorized actor commits it."
            ),
        )

    def _emit(self, step: dict[str, Any], context: dict[str, Any]) -> ModelResponse:
        if step.get("finish") == "tool_calls":
            calls = step.get("tool_calls") or []
            return ModelResponse(
                finish="tool_calls",
                tool_calls=tuple(
                    ModelToolCall(
                        id=call.get("id", f"call_{i}"),
                        name=call.get("name", ""),
                        arguments_raw=json.dumps(call.get("arguments", {})),
                        arguments=call.get("arguments", {}),
                    )
                    for i, call in enumerate(calls)
                ),
            )
        return ModelResponse(finish="stop", content=step.get("content") or "")


__all__ = ["ScriptedModelBackend"]
