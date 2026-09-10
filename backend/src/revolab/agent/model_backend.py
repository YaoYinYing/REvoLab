"""ModelBackend — the smallest model abstraction the Agent runtime needs.

Phase 8 introduces exactly one configured concrete adapter per process and NO
generalized LLM-provider framework (TODO.md section 4). The model backend is
Agent runtime infrastructure, not a scientific Provider/Capability: it never
enters Core's domain models, the ToolCatalog, or the OpenAPI wire contract other
than through the typed Agent-turn response.

The external boundary is `ModelBackend.complete`. Everything below this line is
transport detail; everything above it speaks `ModelRequest` / `ModelResponse`
value objects. Secrets never appear in these value objects' reprs, in error
messages, or in logs.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

import httpx

from revolab.domain.errors import ModelUnavailableError

_MessageRole = Literal["system", "user", "assistant", "tool"]

# OpenAI-compatible function names: letters, digits, underscores, hyphens, <= 64
# characters. Catalog tool ids (e.g. `decision.record_draft`, `{provider}.compute.*`)
# may contain dots and exceed this, so the adapter maps canonical ids to safe wire
# names and reverse-maps emitted calls back to the canonical id before the loop
# ever sees them.
_SAFE_FUNCTION_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")


@dataclass(frozen=True)
class ModelToolCall:
    """One tool call emitted by the model. `arguments` is `None` when the
    provider's argument string is not a single JSON object (fail-closed by the
    loop, never executed)."""

    id: str
    name: str
    arguments_raw: str
    arguments: dict[str, Any] | None


@dataclass(frozen=True)
class ChatMessage:
    """Neutral message shape. `tool_calls` rides on `assistant` messages and
    `tool_call_id`/`name` ride on `tool` messages; the OpenAI adapter maps these
    to the provider wire format. Content is always a plain string, never an
    untrusted object graph."""

    role: _MessageRole
    content: str | None = None
    tool_calls: tuple[ModelToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    """A model-facing tool description derived from the canonical ToolDescriptorRead
    (name + description + input JSON Schema only). No secrets, no raw write tools."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ModelRequest:
    """A bounded request to the external model. `messages` is the single
    assembled prompt (server system instructions + bounded transient history +
    untrusted project data as one clearly-delimited user message + tool results)."""

    system: str
    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolSpec, ...]


@dataclass(frozen=True)
class ModelResponse:
    """A model turn: either a final assistant message (`finish="stop"`) or one or
    more tool calls (`finish="tool_calls"`)."""

    finish: Literal["stop", "tool_calls"]
    content: str | None = None
    tool_calls: tuple[ModelToolCall, ...] = field(default_factory=tuple)


class ModelBackend(Protocol):
    """The external model boundary. Implementations translate neutral
    `ModelRequest`/`ModelResponse` values to/from one provider wire format."""

    def complete(self, request: ModelRequest) -> ModelResponse: ...


def _parse_arguments(raw: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _chat_completions_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _safe_function_name(tool_id: str) -> str:
    """Map a canonical tool id to an OpenAI-safe function name (<= 64 chars).
    A truncated id keeps a short digest of the original so two long ids never
    silently collide."""
    base = _SAFE_FUNCTION_NAME_RE.sub("_", tool_id)
    if len(base) <= 64:
        return base
    digest = hashlib.sha256(tool_id.encode("utf-8")).hexdigest()[:8]
    return f"{base[:55]}_{digest}"


def _tool_name_map(tools: tuple[ToolSpec, ...]) -> tuple[dict[str, str], dict[str, str]]:
    """Return (canonical->safe, safe->canonical); fails closed on collision."""
    forward: dict[str, str] = {}
    reverse: dict[str, str] = {}
    for tool in tools:
        safe = _safe_function_name(tool.name)
        if safe in reverse and reverse[safe] != tool.name:
            raise ModelUnavailableError("tool function names are not representable")
        forward[tool.name] = safe
        reverse[safe] = tool.name
    return forward, reverse


class OpenAICompatModelBackend:
    """One OpenAI-compatible chat/tool-call transport over `httpx`.

    `endpoint` is the base URL (e.g. `https://api.openai.com/v1`) or the full
    `/chat/completions` URL. The optional `api_key` is presented as a bearer
    token. Failures map to `ModelUnavailableError` and never echo provider
    response bodies, credentials, or stack traces."""

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not endpoint or not model:
            raise ModelUnavailableError("model endpoint and model name are required")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(timeout=timeout_seconds, headers=headers)
        self._url = _chat_completions_url(endpoint)
        host = urlsplit(self._url).netloc
        if not host:
            raise ModelUnavailableError("model endpoint is not a valid URL")
        self._model = model

    def close(self) -> None:
        self._client.close()

    def complete(self, request: ModelRequest) -> ModelResponse:
        forward, reverse = _tool_name_map(request.tools)
        messages: list[dict[str, Any]] = [{"role": "system", "content": request.system}]
        for msg in request.messages:
            item: dict[str, Any] = {"role": msg.role}
            item["content"] = msg.content
            if msg.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": forward.get(call.name, _safe_function_name(call.name)),
                            "arguments": call.arguments_raw or "{}",
                        },
                    }
                    for call in msg.tool_calls
                ]
            if msg.role == "tool":
                item["tool_call_id"] = msg.tool_call_id
                if msg.name:
                    item["name"] = forward.get(msg.name, _safe_function_name(msg.name))
            messages.append(item)

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "tool_choice": "auto",
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": forward[tool.name],
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in request.tools
            ]
        else:
            payload.pop("tool_choice", None)
        try:
            response = self._client.post(self._url, json=payload)
        except httpx.TimeoutException as exc:
            raise ModelUnavailableError("model request timed out") from exc
        except httpx.HTTPError as exc:
            raise ModelUnavailableError("model transport failed") from exc

        if response.status_code != 200:
            raise ModelUnavailableError(f"model returned HTTP {response.status_code}")

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ModelUnavailableError("model returned a malformed response") from exc

        if not isinstance(data, dict):
            raise ModelUnavailableError("model returned a malformed response")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelUnavailableError("model returned a malformed response")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ModelUnavailableError("model returned a malformed response")
        message = choice.get("message") or {}
        if not isinstance(message, dict):
            raise ModelUnavailableError("model returned a malformed response")

        parsed_calls: list[ModelToolCall] = []
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                raise ModelUnavailableError("model returned a malformed response")
            function = call.get("function") or {}
            if not isinstance(function, dict):
                raise ModelUnavailableError("model returned a malformed response")
            wire_name = function.get("name") or ""
            canonical = reverse.get(wire_name, wire_name)
            raw_arguments = function.get("arguments") or "{}"
            if not isinstance(raw_arguments, str):
                raw_arguments = json.dumps(raw_arguments, default=str)
            parsed_calls.append(
                ModelToolCall(
                    id=(call.get("id") or ""),
                    name=canonical,
                    arguments_raw=raw_arguments,
                    arguments=_parse_arguments(raw_arguments),
                )
            )

        tool_calls = tuple(parsed_calls)
        finish: Literal["stop", "tool_calls"] = "tool_calls" if tool_calls else "stop"
        return ModelResponse(
            finish=finish,
            content=message.get("content") if isinstance(message.get("content"), str) else None,
            tool_calls=tool_calls,
        )


__all__ = [
    "ChatMessage",
    "ModelBackend",
    "ModelRequest",
    "ModelResponse",
    "ModelToolCall",
    "OpenAICompatModelBackend",
    "ToolSpec",
]
