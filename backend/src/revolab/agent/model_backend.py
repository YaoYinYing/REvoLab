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

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

import httpx

from revolab.domain.errors import ModelUnavailableError

_MessageRole = Literal["system", "user", "assistant", "tool"]


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
                            "name": call.name,
                            "arguments": call.arguments_raw or "{}",
                        },
                    }
                    for call in msg.tool_calls
                ]
            if msg.role == "tool":
                item["tool_call_id"] = msg.tool_call_id
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
                        "name": tool.name,
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

        choice = data["choices"][0]
        message = choice.get("message") or {}
        tool_calls = tuple(
            ModelToolCall(
                id=(call.get("id") or ""),
                name=(call.get("function") or {}).get("name") or "",
                arguments_raw=(call.get("function") or {}).get("arguments") or "{}",
                arguments=_parse_arguments((call.get("function") or {}).get("arguments") or "{}"),
            )
            for call in (message.get("tool_calls") or [])
        )
        finish: Literal["stop", "tool_calls"] = "tool_calls" if tool_calls else "stop"
        return ModelResponse(
            finish=finish,
            content=message.get("content"),
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
