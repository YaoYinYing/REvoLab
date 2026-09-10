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


class ScriptedModelBackend:
    """A deterministic ModelBackend. `steps` is an optional list of explicit
    turn dicts; when exhausted (or absent) the default Phase-8 slice runs."""

    def __init__(self, steps: list[dict[str, Any]] | None = None) -> None:
        self._steps = steps
        self.request_count = 0
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        index = self.request_count
        self.request_count += 1
        context = _extract_context(request)
        if self._steps is not None and index < len(self._steps):
            return self._emit(self._steps[index], context)
        return self._default_turn(index, context)

    def _default_turn(self, index: int, context: dict[str, Any]) -> ModelResponse:
        if index == 0:
            artifact_id = _first_artifact_id(context)
            if artifact_id is None:
                return ModelResponse(finish="stop", content="No artifact was selected in context.")
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
