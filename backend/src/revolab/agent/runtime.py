"""Bounded Project Agent loop (Phase 8).

The executable boundary:

```text
Human message
    -> ContextBuilder (fresh bounded ProjectContext)
    -> trusted skill bodies (bounded)
    -> canonical ToolCatalog (Agent-facing tools only)
    -> prompt assembly (trusted instructions <> untrusted project data)
    -> ModelBackend
    -> typed tool-call validation (tool id, availability, arguments,
       autonomy, execution_class, side-effect class)
    -> LocalToolRuntime  OR  PendingAction (explicit_action)  OR  fail-closed
    -> bounded ToolResult back to the model
    -> final conversational response
```

The Agent is a consumer, never an owner: every durable change goes through the
SAME typed `LocalToolRuntime` the human workspace uses; explicit actions are never
executed here, and `never_agent` operations are not in the catalog at all.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from revolab.agent.builder import build_context
from revolab.agent.model_backend import (
    ChatMessage,
    ModelBackend,
    ModelToolCall,
    ToolSpec,
)
from revolab.agent.prompt import build_model_request, serialize_context
from revolab.agent.skills import load_skill_bodies
from revolab.content_store import ContentStore
from revolab.domain.errors import DomainError, ModelUnavailableError
from revolab.drivers import DriverRegistry
from revolab.enums import (
    AgentTerminationReason,
    AgentToolAutonomy,
    AgentToolCallStatus,
    ToolExecutionClass,
    ToolSideEffectClass,
)
from revolab.schemas import (
    AgentTurnBudgetRead,
    AgentTurnRead,
    ComputeSubmissionCreate,
    ContextSelectionCreate,
    DecisionCommitCreate,
    PendingActionRead,
    ToolCallTraceRead,
    ToolInvocationCreate,
)
from revolab.secret_store import SecretStore
from revolab.tools.catalog import build_tool_catalog
from revolab.tools.registry import LocalToolRegistry
from revolab.tools.runtime import LocalToolRuntime
from revolab.tools.types import InvocationContext

# Remote explicit-action tool ids whose arguments are canonical Pydantic models.
# This maps a tool-id SUFFIX (provider key is dynamic) to the ONE canonical input
# model already used by the typed compute endpoint — never a hand-copied schema.
_REMOTE_EXPLICIT_INPUT_MODELS: dict[str, type[BaseModel]] = {
    ".compute.submit": ComputeSubmissionCreate,
}

_LOCAL_EXPLICIT_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "decision.commit": DecisionCommitCreate,
}


@dataclass(frozen=True)
class AgentLoopBounds:
    """Conservative concrete ceilings for one Agent turn (TODO.md section 7).

    The model request timeout is enforced by the concrete ModelBackend adapter
    (httpx timeout), configured from the same server settings as
    `_agent_bounds()`; it is not re-implemented in the loop.
    """

    max_model_turns: int = 8
    max_tool_calls: int = 16
    max_tool_calls_per_turn: int = 4
    max_context_chars: int = 60_000
    max_history_messages: int = 20
    max_history_chars: int = 20_000
    max_skill_count: int = 4
    max_skill_bytes: int = 20_000
    max_tool_result_chars: int = 12_000
    total_turn_duration_seconds: float = 300.0


def _tool_descriptor_tools(catalog_tools: list[dict[str, Any]]) -> tuple[ToolSpec, ...]:
    """Project the Agent-facing tool surface. Only local tools and remote
    explicit-action tools are offered: remote automatic/policy reads are surfaced
    in the human catalog but are never executed by the Agent loop, so advertising
    them would waste model turns on tools that can only be refused."""
    specs: list[ToolSpec] = []
    for tool in catalog_tools:
        if (
            tool["execution_class"] == ToolExecutionClass.REMOTE.value
            and tool["autonomy"] != AgentToolAutonomy.EXPLICIT_ACTION.value
        ):
            continue
        specs.append(
            ToolSpec(
                name=tool["id"],
                description=tool["description"],
                input_schema=tool["input_schema"],
            )
        )
    return tuple(specs)


def _catalog_by_id(catalog: Any) -> dict[str, Any]:
    return {tool.id: tool for tool in catalog.tools}


def _item_attr(item: Any, name: str) -> Any:
    """Read a transient-history field from either a Pydantic model or a plain
    mapping (direct runner callers may pass either)."""
    value = getattr(item, name, None)
    if value is not None:
        return value
    getter = getattr(item, "get", None)
    if callable(getter):
        return getter(name)
    return None


def _bounded_json(value: Any, limit: int) -> str:
    try:
        text = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = "<unserializable tool result>"
    if len(text) <= limit:
        return text
    return text[: max(limit - 60, 0)] + "\n...[tool result truncated by the turn budget]"


def _validate_pending_input(tool_id: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    """Validate a refused explicit-action tool call against its canonical input
    model so a PendingAction carries only validated, bounded argument data. Fail
    closed when the model forms invalid arguments."""
    model: type[BaseModel] | None = None
    if tool_id == "decision.commit":
        model = _LOCAL_EXPLICIT_INPUT_MODELS["decision.commit"]
    else:
        for suffix, candidate in _REMOTE_EXPLICIT_INPUT_MODELS.items():
            if tool_id.endswith(suffix):
                model = candidate
                break
    if model is None:
        return None
    try:
        parsed = model.model_validate(arguments)
        return parsed.model_dump(mode="json")
    except (PydanticValidationError, ValueError):
        return None


def _bounded_pending_arguments(validated: dict[str, Any], limit: int) -> dict[str, Any]:
    """Bound the pending-action argument payload without losing validated info for
    the common small case. Measure the FULL serialized size first: large dumps
    (e.g. compute submission params) become a bounded preview so the Agent-turn
    response stays non-secret and bounded."""
    try:
        full = json.dumps(validated, sort_keys=True, default=str)
    except (TypeError, ValueError):
        full = "{}"
    if len(full) <= limit:
        return validated
    return {"truncated": True, "preview": full[:limit]}


class AgentTurnRunner:
    """Run one bounded Agent turn. Stateless across turns (context + catalog are
    rebuilt fresh on every call), process-local only."""

    def __init__(
        self,
        model: ModelBackend,
        local_runtime: LocalToolRuntime,
        registry: DriverRegistry,
        secret_store: SecretStore,
        content_store: ContentStore,
        bounds: AgentLoopBounds | None = None,
        local_registry: LocalToolRegistry | None = None,
    ) -> None:
        self._model = model
        self._local_runtime = local_runtime
        self._registry = registry
        self._secret_store = secret_store
        self._content_store = content_store
        self._bounds = bounds or AgentLoopBounds()
        self._local_registry = local_registry

    def run(
        self,
        session: Session,
        actor_id: UUID,
        project_id: UUID,
        message: str,
        selection: ContextSelectionCreate | None = None,
        history: list[Any] | None = None,
    ) -> AgentTurnRead:
        started = time.monotonic()
        context = build_context(session, actor_id, project_id, self._registry, selection)
        catalog = build_tool_catalog(
            session, actor_id, project_id, self._registry, self._local_registry
        )
        by_id = _catalog_by_id(catalog)
        try:
            skills = load_skill_bodies(
                selection,
                max_count=self._bounds.max_skill_count,
                max_bytes=self._bounds.max_skill_bytes,
            )
        except FileNotFoundError as exc:
            # A missing skill root or an over-budget skill body is a deployment
            # misconfiguration, NOT a model failure. Fail closed with a sanitized,
            # typed 503 — never an unhandled raw FileNotFoundError.
            raise ModelUnavailableError("skill loading failed (bound or missing skill)") from exc
        tool_specs = _tool_descriptor_tools(
            [tool.model_dump(mode="json") for tool in catalog.tools]
        )

        # Prompt-level context char truncation is separate from the builder's
        # category caps: report it so a bound hit is visible in the turn budget.
        context_serialized = serialize_context(context)
        context_truncated = context.budget.truncated or len(context_serialized) > self._bounds.max_context_chars

        history_messages = self._bounded_history(history)
        transcript: list[ChatMessage] = []
        trace: list[ToolCallTraceRead] = []
        pending: list[PendingActionRead] = []
        model_turns = 0
        tool_calls_total = 0
        termination = AgentTerminationReason.FINAL_RESPONSE
        final_response: str | None = None

        invocation_ctx = InvocationContext(
            session=session,
            registry=self._registry,
            secret_store=self._secret_store,
            content_store=self._content_store,
            actor_id=actor_id,
            project_id=project_id,
        )

        while True:
            if time.monotonic() - started >= self._bounds.total_turn_duration_seconds:
                termination = AgentTerminationReason.TOTAL_DURATION
                final_response = self._bound_hit_message(termination)
                break
            if model_turns >= self._bounds.max_model_turns:
                termination = AgentTerminationReason.MAX_MODEL_TURNS
                final_response = self._bound_hit_message(termination)
                break

            request = build_model_request(
                history=history_messages + tuple(transcript),
                context=context,
                message=message,
                skills=skills,
                tools=tool_specs,
                bounds=self._bounds,
            )
            try:
                response = self._model.complete(request)
            except ModelUnavailableError:
                termination = AgentTerminationReason.MODEL_UNAVAILABLE
                final_response = "The model runtime is unavailable for this turn."
                break
            model_turns += 1

            if not response.tool_calls:
                final_response = response.content or ""
                termination = AgentTerminationReason.FINAL_RESPONSE
                break

            # The model request itself is bounded by the adapter timeout; the
            # total-turn deadline is rechecked AFTER the model returns and again
            # BEFORE executing each tool, so side effects cannot slip past the
            # configured turn duration.
            if time.monotonic() - started >= self._bounds.total_turn_duration_seconds:
                termination = AgentTerminationReason.TOTAL_DURATION
                final_response = self._bound_hit_message(termination)
                break

            # Budget the per-turn tool-call fan-out before executing anything.
            calls = response.tool_calls
            overflow = max(len(calls) - self._bounds.max_tool_calls_per_turn, 0)
            handled_calls = calls[: self._bounds.max_tool_calls_per_turn]

            assistant_message = ChatMessage(
                role="assistant",
                content=response.content,
                tool_calls=handled_calls,
            )
            transcript.append(assistant_message)

            for call in handled_calls:
                if time.monotonic() - started >= self._bounds.total_turn_duration_seconds:
                    termination = AgentTerminationReason.TOTAL_DURATION
                    final_response = self._bound_hit_message(termination)
                    break
                if tool_calls_total >= self._bounds.max_tool_calls:
                    termination = AgentTerminationReason.MAX_TOOL_CALLS
                    final_response = self._bound_hit_message(termination)
                    break
                tool_calls_total += 1
                entry, pending_action, result_text = self._handle_tool_call(
                    call, by_id, invocation_ctx
                )
                trace.append(entry)
                if pending_action is not None:
                    pending.append(pending_action)
                content = (
                    result_text
                    if result_text is not None
                    else _bounded_json(
                        {"status": entry.status.value, "error": entry.error},
                        self._bounds.max_tool_result_chars,
                    )
                )
                transcript.append(
                    ChatMessage(role="tool", content=content, tool_call_id=call.id, name=call.name)
                )
            for _ in range(overflow):
                trace.append(
                    ToolCallTraceRead(
                        tool_id="<skipped>",
                        status=AgentToolCallStatus.SKIPPED,
                        error="tool calls per model turn exceeded the configured ceiling",
                    )
                )
            if termination is not AgentTerminationReason.FINAL_RESPONSE:
                break
            # Otherwise loop: feed the bounded tool results back to the model.

        budget = AgentTurnBudgetRead(
            model_turns=model_turns,
            max_model_turns=self._bounds.max_model_turns,
            tool_calls=tool_calls_total,
            max_tool_calls=self._bounds.max_tool_calls,
            max_tool_calls_per_turn=self._bounds.max_tool_calls_per_turn,
            history_messages=len(history_messages),
            max_history_messages=self._bounds.max_history_messages,
            max_history_chars=self._bounds.max_history_chars,
            skills_loaded=len(skills),
            max_skills=self._bounds.max_skill_count,
            max_skill_bytes=self._bounds.max_skill_bytes,
            max_context_chars=self._bounds.max_context_chars,
            max_tool_result_chars=self._bounds.max_tool_result_chars,
            total_turn_duration_seconds=self._bounds.total_turn_duration_seconds,
            context_truncated=context_truncated,
        )
        return AgentTurnRead(
            project_id=project_id,
            termination_reason=termination,
            final_response=final_response,
            tool_trace=trace,
            pending_actions=pending,
            budget=budget,
        )

    def _bounded_history(self, history: list[Any] | None) -> tuple[ChatMessage, ...]:
        if not history:
            return ()
        messages: list[ChatMessage] = []
        used = 0
        for item in history[-self._bounds.max_history_messages :]:
            role = _item_attr(item, "role")
            content = _item_attr(item, "content")
            if role not in {"user", "assistant"} or not isinstance(content, str):
                continue
            if used + len(content) > self._bounds.max_history_chars:
                break
            used += len(content)
            messages.append(ChatMessage(role=role, content=content))
        return tuple(messages)

    def _bound_hit_message(self, termination: AgentTerminationReason) -> str:
        return (
            "The Agent turn ended because the runtime reached a configured ceiling "
            f"({termination.value}). No further tools were executed."
        )

    def _handle_tool_call(
        self,
        call: ModelToolCall,
        by_id: dict[str, Any],
        ctx: InvocationContext,
    ) -> tuple[ToolCallTraceRead, PendingActionRead | None, str | None]:
        """Validate and dispatch ONE model-emitted tool call. The model cannot
        bypass the canonical runtime: unknown/remote/never_agent/explicit tools
        all fail closed or become PendingActions."""
        descriptor = by_id.get(call.name)
        if descriptor is None:
            return (
                ToolCallTraceRead(
                    tool_id=call.name,
                    status=AgentToolCallStatus.FAILED,
                    error="unknown tool",
                ),
                None,
                None,
            )

        if not descriptor.available:
            return (
                ToolCallTraceRead(
                    tool_id=call.name,
                    status=AgentToolCallStatus.FAILED,
                    error=descriptor.availability_reason or "tool unavailable",
                ),
                None,
                None,
            )

        if call.arguments is None:
            return (
                ToolCallTraceRead(
                    tool_id=call.name,
                    status=AgentToolCallStatus.FAILED,
                    error="malformed arguments (expected a JSON object)",
                ),
                None,
                None,
            )

        # Explicit actions are PROPOSED, never executed by the Agent loop.
        if (
            descriptor.autonomy is AgentToolAutonomy.EXPLICIT_ACTION
            or descriptor.side_effect_class is ToolSideEffectClass.EXTERNAL_ACTION
        ):
            validated = _validate_pending_input(call.name, call.arguments)
            if validated is None:
                return (
                    ToolCallTraceRead(
                        tool_id=call.name,
                        status=AgentToolCallStatus.FAILED,
                        error="explicit action arguments are invalid",
                    ),
                    None,
                    None,
                )
            pending_action = PendingActionRead(
                tool_id=call.name,
                autonomy=descriptor.autonomy,
                summary=f"The model proposed {call.name}; it was NOT executed.",
                arguments=_bounded_pending_arguments(validated, self._bounds.max_tool_result_chars),
                reason="explicit actions require an authorized human action",
            )
            return (
                ToolCallTraceRead(
                    tool_id=call.name,
                    status=AgentToolCallStatus.PENDING,
                    pending_action=pending_action,
                ),
                pending_action,
                None,
            )

        # Remote (provider) tools are never executed through the closed local
        # runtime. A remote automatic/policy tool exits the Agent loop here as
        # typed refusal; the external execution surface remains the human one.
        if descriptor.execution_class is ToolExecutionClass.REMOTE:
            return (
                ToolCallTraceRead(
                    tool_id=call.name,
                    status=AgentToolCallStatus.FAILED,
                    error="remote provider tools are not executed by the Agent runtime",
                ),
                None,
                None,
            )

        try:
            result = self._local_runtime.invoke(
                ctx,
                ToolInvocationCreate(
                    tool_id=call.name, input=call.arguments, persist=False
                ),
            )
        except DomainError as exc:
            return (
                ToolCallTraceRead(
                    tool_id=call.name, status=AgentToolCallStatus.FAILED, error=str(exc)
                ),
                None,
                None,
            )
        except Exception as exc:  # pragma: no cover - defense-in-depth only
            return (
                ToolCallTraceRead(
                    tool_id=call.name,
                    status=AgentToolCallStatus.FAILED,
                    error="tool execution failed",
                ),
                None,
                f"tool execution failed: {type(exc).__name__}",
            )

        result_text = _bounded_json(result.model_dump(mode="json"), self._bounds.max_tool_result_chars)
        return (
            ToolCallTraceRead(tool_id=call.name, status=AgentToolCallStatus.COMPLETED, result=result),
            None,
            result_text,
        )


__all__ = ["AgentLoopBounds", "AgentTurnRunner"]
