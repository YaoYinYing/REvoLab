"""Phase 11 — Durable explicit-action requests and human-authorized execution.

The authority gap this closes (ADR-0017, `docs/architecture/AGENT_ACTION_HANDOFF.md`):

```text
Agent -> typed explicit-action proposal -> durable Action Request
      -> explicit human execute/reject -> current-truth revalidation
      -> canonical Tool/domain/provider path -> Run / Decision reference
```

The invariant is **persist intent, never authority**:

- This module records WHAT an Actor proposed (canonical tool id + the complete,
  schema-validated, bounded argument payload) so the proposal survives a reload.
- It stores no authorization decision, membership result, provider-health
  snapshot, capability availability, credential material or `secret_ref`.
- Execution is a separate explicit human/API action that rebuilds all relevant
  current truth (membership, role, ToolCatalog, autonomy, input schema, resource
  visibility, provider availability, credentials, project policy) before any side
  effect, then crosses the boundary exactly once through the SAME canonical path
  the human workspace already uses.

Action Request is durable OPERATIONAL INTENT: it is not a ScientificObject,
Evidence, Decision, ProjectResourceLink, GlobalResourceRegistry entry, conversation
message, Agent memory, ToolResult, or provider execution truth. Ownership is the
Phase-9 conversation lens: one owning Actor inside one Project.

Lifecycle: `pending -> executing -> {succeeded|failed|ambiguous}`, or
`pending -> rejected`. A pre-claim revalidation refusal leaves the action `pending`
(no side effect happened and no claim is consumed, so the human may retry once the
precondition returns); once the one-shot claim is taken, every outcome is terminal.
`ambiguous` is never auto-retried.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from revolab import services
from revolab.agent.conversations import owned_conversation
from revolab.capabilities import CapabilityError, InputBinding, RunHandle
from revolab.content_store import ContentStore
from revolab.domain import persistence, provenance
from revolab.domain.errors import ConflictError, DomainError, NotFoundError, ValidationError
from revolab.domain.identity import mutation_capable_membership, readable_membership
from revolab.drivers import DriverRegistry
from revolab.enums import (
    ActionRequestStatus,
    AgentToolAutonomy,
    CapabilityErrorKind,
    CapabilityKind,
    ToolExecutionClass,
    ToolSideEffectClass,
)
from revolab.models import ActionRequest
from revolab.schemas import ComputeSubmissionCreate, ToolInvocationCreate, ToolResultRead
from revolab.secret_store import SecretStore
from revolab.tools.catalog import build_tool_catalog
from revolab.tools.explicit_actions import (
    COMPUTE_SUBMIT_SUFFIX,
    MAX_ACTION_ARGUMENT_CHARS,
    explicit_arguments_match_provider,
    remote_explicit_action_input_model,
)
from revolab.tools.registry import LocalToolRegistry
from revolab.tools.runtime import LocalToolRuntime
from revolab.tools.types import InvocationContext

MAX_STATUS_REASON_CHARS = 500

# Provider failure kinds that are a definite, pre-side-effect rejection when they
# arrive WITHOUT an upstream response (driver readiness/health/missing credential
# checks performed before the request is sent). With a response they mean the
# provider explicitly rejected the request.
_DEFINITE_PROVIDER_REJECTION_KINDS = frozenset(
    {
        CapabilityErrorKind.AUTH,
        CapabilityErrorKind.INVALID_PARAM,
        CapabilityErrorKind.NOT_FOUND,
    }
)


def _now() -> datetime:
    return datetime.now(UTC)


def _rowcount(result: Any) -> int:
    """`rowcount` of a conditional UPDATE: the cross-backend atomicity signal."""
    return int(getattr(result, "rowcount", 0))


def _digest(arguments: dict[str, Any]) -> str:
    """An UNKEYED content digest of the canonical argument payload.

    It detects accidental corruption / an out-of-band edit of the stored payload;
    it is deliberately NOT an authentication tag (anyone who can rewrite the row
    can recompute it). Authority is never derived from it — execution revalidates
    the payload against the CURRENT canonical schema."""
    payload = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _argument_size(arguments: dict[str, Any]) -> int:
    try:
        return len(json.dumps(arguments, sort_keys=True, default=str))
    except (TypeError, ValueError):
        return MAX_ACTION_ARGUMENT_CHARS + 1


def _bounded_reason(reason: str | None) -> str | None:
    """Status reasons are sanitized provider/domain messages by construction
    (`CapabilityError` never carries secrets or raw upstream bodies). Bound them
    so a durable row can never grow unbounded."""
    if reason is None:
        return None
    return reason[:MAX_STATUS_REASON_CHARS]


@dataclass(frozen=True)
class ActionExecution:
    """Everything one human execution may touch. All inputs are typed boundaries
    (session, driver registry, secret store, content store, closed tool registry)
    — never arbitrary filesystem/network handles."""

    session: Session
    registry: DriverRegistry
    secret_store: SecretStore
    content_store: ContentStore
    local_registry: LocalToolRegistry


# ---------------------------------------------------------------------------
# Proposal (Agent boundary)
# ---------------------------------------------------------------------------


def propose_action_request(
    session: Session,
    *,
    actor_id: UUID,
    project_id: UUID,
    conversation_id: UUID | None,
    tool_id: str,
    autonomy: AgentToolAutonomy,
    execution_class: ToolExecutionClass,
    side_effect_class: ToolSideEffectClass,
    arguments: dict[str, Any],
) -> ActionRequest:
    """Persist ONE validated explicit-action proposal.

    `arguments` must already be the complete canonical model dump validated against
    the tool's canonical input model (the Agent loop validates before calling).
    The row is flushed, never committed here: it belongs to the caller's turn
    transaction so the proposal and the transcript that describes it are atomic.

    The persistence boundary enforces its OWN size bound: a payload over
    `MAX_ACTION_ARGUMENT_CHARS` is refused rather than truncated, so a durable row
    can never hold incomplete executable state even if a caller forgets to check.
    """
    if _argument_size(arguments) > MAX_ACTION_ARGUMENT_CHARS:
        raise ValidationError("explicit action arguments exceed the durable action bound")
    action = ActionRequest(
        project_id=project_id,
        actor_id=actor_id,
        conversation_id=conversation_id,
        tool_id=tool_id,
        autonomy=autonomy.value,
        execution_class=execution_class.value,
        side_effect_class=side_effect_class.value,
        arguments=arguments,
        arguments_digest=_digest(arguments),
        status=ActionRequestStatus.PENDING.value,
    )
    session.add(action)
    session.flush()
    return action


# ---------------------------------------------------------------------------
# Read / list (owning Actor only)
# ---------------------------------------------------------------------------


def _owned_action_request(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    action_request_id: UUID,
) -> ActionRequest:
    """Resolve an Action Request the current Actor owns, proving at the same time
    that the Actor can currently read an active Project.

    Fails closed: a non-member (or an inactive Project) raises before the row is
    looked up; a guessed UUID or an action owned by another member raises 404 —
    never an existence oracle."""
    readable_membership(session, actor_id, project_id)
    action = session.get(ActionRequest, action_request_id)
    if (
        action is None
        or action.project_id != project_id
        or action.actor_id != actor_id
    ):
        raise NotFoundError("action request not found")
    return action


def get_action_request(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    action_request_id: UUID,
) -> ActionRequest:
    return _owned_action_request(session, actor_id, project_id, action_request_id)


def list_action_requests(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    conversation_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[ActionRequest]:
    """List this Actor's own Action Requests for one conversation they own."""
    owned_conversation(session, actor_id, project_id, conversation_id)
    rows = session.scalars(
        select(ActionRequest)
        .where(
            ActionRequest.project_id == project_id,
            ActionRequest.actor_id == actor_id,
            ActionRequest.conversation_id == conversation_id,
        )
        .order_by(ActionRequest.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(rows)


# ---------------------------------------------------------------------------
# Human rejection (terminal, no side effect)
# ---------------------------------------------------------------------------


def reject_action_request(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    action_request_id: UUID,
) -> ActionRequest:
    """Explicitly reject a pending Action Request. Rejection is terminal and has
    no scientific or external side effect."""
    action = _owned_action_request(session, actor_id, project_id, action_request_id)
    if action.status != ActionRequestStatus.PENDING.value:
        raise ConflictError(
            f"action request is {action.status!r}; only a pending action can be rejected"
        )
    result = session.execute(
        update(ActionRequest)
        .where(
            ActionRequest.id == action_request_id,
            ActionRequest.status == ActionRequestStatus.PENDING.value,
        )
        .values(
            status=ActionRequestStatus.REJECTED.value,
            status_reason="rejected by the owning actor",
            resolved_at=_now(),
        )
    )
    session.commit()
    if _rowcount(result) != 1:
        # Lost a race against another terminal transition: report the truth
        # instead of pretending the rejection won.
        raise ConflictError("action request is no longer pending")
    session.refresh(action)
    return action


# ---------------------------------------------------------------------------
# Human execution (current-truth revalidation + one-shot claim + canonical path)
# ---------------------------------------------------------------------------


def _current_descriptor(
    ctx: ActionExecution,
    actor_id: UUID,
    project_id: UUID,
    action: ActionRequest,
) -> Any:
    """Resolve the action's Tool from the CURRENT canonical ToolCatalog and prove
    it is still an explicit action. A removed tool, an unavailable provider
    capability, or a changed autonomy class fails closed BEFORE any side effect."""
    catalog = build_tool_catalog(
        ctx.session, actor_id, project_id, ctx.registry, ctx.local_registry
    )
    descriptor = next((tool for tool in catalog.tools if tool.id == action.tool_id), None)
    if descriptor is None:
        raise ConflictError(f"tool {action.tool_id!r} is no longer available in this project")
    if descriptor.autonomy is not AgentToolAutonomy.EXPLICIT_ACTION:
        raise ConflictError(
            f"tool {action.tool_id!r} is no longer an explicit action; refusing to execute"
        )
    return descriptor


def _revalidate_arguments(ctx: ActionExecution, action: ActionRequest) -> dict[str, Any]:
    """Revalidate the persisted payload against the CURRENT canonical input model,
    resolved from the same owner the proposal boundary used: a LOCAL explicit
    action from its registered `LocalToolSpec`, a REMOTE one from the ONE
    capability-suffix mapping. A schema change (or a payload that no longer
    validates) fails closed; old arguments are never silently reinterpreted."""
    if _digest(action.arguments) != action.arguments_digest:
        raise ConflictError(
            "stored action arguments no longer match their recorded digest"
        )
    model = ctx.local_registry.explicit_action_input_model(
        action.tool_id
    ) or remote_explicit_action_input_model(action.tool_id)
    if model is None:
        raise ConflictError(f"tool {action.tool_id!r} has no canonical input model")
    try:
        parsed = model.model_validate(action.arguments)
    except (PydanticValidationError, ValueError) as exc:
        raise ConflictError(
            "stored action arguments no longer validate against the current schema "
            f"for {action.tool_id!r}"
        ) from exc
    return dict(parsed.model_dump(mode="json"))


def _claim(session: Session, action_request_id: UUID) -> bool:
    """The durable, cross-backend one-shot claim.

    A single conditional UPDATE is the atomic primitive: exactly one concurrent
    execution can move `pending -> executing`. It is COMMITTED before any external
    call so the claim (not a process-memory flag) is the production truth."""
    result = session.execute(
        update(ActionRequest)
        .where(
            ActionRequest.id == action_request_id,
            ActionRequest.status == ActionRequestStatus.PENDING.value,
        )
        .values(status=ActionRequestStatus.EXECUTING.value, claimed_at=_now())
    )
    session.commit()
    return _rowcount(result) == 1


def _settle(
    session: Session,
    action_request_id: UUID,
    status: ActionRequestStatus,
    *,
    reason: str | None = None,
    run_resource_id: UUID | None = None,
    decision_id: UUID | None = None,
) -> None:
    """Write the terminal outcome of a claimed action.

    The one-shot claim is already durably committed, so a terminal write MUST land:
    leaving the row in `executing` while the external side effect happened would be
    a lie. The write therefore tolerates a session left in a failed transaction by
    the execution attempt — it rolls back first and retries once. Returns whether
    the conditional `executing -> <terminal>` transition actually won.
    """
    statement = (
        update(ActionRequest)
        .where(
            ActionRequest.id == action_request_id,
            ActionRequest.status == ActionRequestStatus.EXECUTING.value,
        )
        .values(
            status=status.value,
            status_reason=_bounded_reason(reason),
            resolved_at=_now(),
            result_run_id=run_resource_id,
            result_decision_id=decision_id,
        )
    )
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            result = session.execute(statement)
            if _rowcount(result) != 1:
                # Unreachable with the claim protocol (only the claim winner may
                # settle); a lost transition is surfaced rather than ignored so a
                # silently-unsettled action can never masquerade as done.
                session.rollback()
                raise ConflictError("action request terminal transition was lost")
            session.commit()
            return
        except SQLAlchemyError as exc:  # a failed transaction must not strand the row
            last_error = exc
            session.rollback()
    assert last_error is not None
    raise last_error


def classify_provider_failure(exc: BaseException) -> ActionRequestStatus:
    """Classify a failure that happened at/after the external boundary.

    Definite: the provider explicitly rejected the request (a 4xx response was
    received) or a pre-side-effect local provider check refused it (driver not
    READY, health, missing credential — no upstream response). The external side
    effect did not take place.

    Ambiguous: a transport failure, a 5xx/gateway response, an unexpected payload,
    or a 2xx without a task identity. The submission MAY have succeeded, so the
    action must never be auto-retried. REvoCompute exposes no client-usable
    idempotency key, so one is not invented.
    """
    if isinstance(exc, DomainError):
        # A local typed refusal (authorization/validation/visibility) raised at the
        # capability boundary happens before the request leaves the process.
        return ActionRequestStatus.FAILED
    if isinstance(exc, CapabilityError):
        if exc.kind in _DEFINITE_PROVIDER_REJECTION_KINDS:
            return ActionRequestStatus.FAILED
        if exc.kind is CapabilityErrorKind.PROVIDER_UNAVAILABLE and exc.upstream_status is None:
            return ActionRequestStatus.FAILED
    return ActionRequestStatus.AMBIGUOUS


def _execute_local(ctx: ActionExecution, action: ActionRequest, arguments: dict[str, Any]) -> UUID | None:
    """Local explicit action: the SAME closed LocalToolRuntime the human workspace
    uses. No second Decision-promotion implementation exists.

    The returned resource id is stored in `result_decision_id`, which is correct
    only while `decision.commit` is the ONLY local `explicit_action` tool (the
    Phase-11 concrete use case). Adding a second local explicit action whose result
    is not a Decision requires a new typed result reference — never a renamed FK
    (no speculative abstraction is added for a second use case that does not exist)."""
    runtime = LocalToolRuntime(ctx.local_registry)
    result: ToolResultRead = runtime.invoke(
        InvocationContext(
            session=ctx.session,
            registry=ctx.registry,
            secret_store=ctx.secret_store,
            content_store=ctx.content_store,
            actor_id=action.actor_id,
            project_id=action.project_id,
        ),
        ToolInvocationCreate(tool_id=action.tool_id, input=arguments, persist=False),
    )
    return result.resource_id


class _UnrecordedExternalSuccess(Exception):
    """The provider CONFIRMED a run, but the canonical RunReference could not be
    recorded locally in that attempt.

    Carries the confirmed provider handle and the input bindings so the canonical
    recording can be retried once on a healthy transaction — `record_compute_run`
    is get-or-create, so a retry after a partially committed attempt completes the
    missing links instead of duplicating the run. If it still cannot be recorded
    the action settles `ambiguous` with the provider identity retained for
    reconciliation (the ONLY place an external identity appears outside a
    RunReference, precisely because the canonical card could not be created)."""

    def __init__(
        self,
        message: str,
        *,
        handle: RunHandle,
        bindings: list[InputBinding],
    ) -> None:
        super().__init__(message)
        self.handle = handle
        self.bindings = bindings


def _record_confirmed_run(
    ctx: ActionExecution,
    action: ActionRequest,
    handle: RunHandle,
    bindings: list[InputBinding],
) -> UUID:
    """Create/reuse the canonical RunReference (+ input provenance) for a run the
    provider just confirmed. Raises `_UnrecordedExternalSuccess` on failure."""
    try:
        recorded = services.record_compute_run(
            ctx.session,
            action.actor_id,
            action.project_id,
            handle,
            inputs=[binding.resource_id for binding in bindings],
        )
    except Exception as exc:
        raise _UnrecordedExternalSuccess(
            str(exc), handle=handle, bindings=bindings
        ) from exc
    return cast(UUID, recorded["run_resource_id"])


@dataclass(frozen=True)
class _RecoveredRun:
    run_id: UUID | None
    reason: str | None = None


def _recover_confirmed_run(
    ctx: ActionExecution, action: ActionRequest, exc: _UnrecordedExternalSuccess
) -> _RecoveredRun:
    """Recover the canonical result reference for a CONFIRMED external run whose
    first recording attempt failed.

    1. Retry the canonical get-or-create recording once on a healthy transaction:
       the provider handle is authoritative, so a partially committed attempt is
       COMPLETED (missing links/edges) rather than duplicated.
    2. If that also fails, read the canonical identity back: the failed attempt may
       already have committed the RunReference (the canonical recording commits the
       identity card before its provenance edges). The read-back is the
       authoritative answer — never a reason string claiming nothing was recorded
       when something was.

    Returns the reference when it exists (with a bounded note when its input
    provenance could not be completed), or `run_id=None` when the canonical
    identity genuinely does not exist."""
    try:
        return _RecoveredRun(run_id=_record_confirmed_run(ctx, action, exc.handle, exc.bindings))
    except _UnrecordedExternalSuccess:
        ctx.session.rollback()
    except SQLAlchemyError:
        ctx.session.rollback()
    existing = provenance.find_run_reference(
        ctx.session, exc.handle.authority, exc.handle.native_id
    )
    if existing is None:
        return _RecoveredRun(run_id=None)
    return _RecoveredRun(
        run_id=existing.run_id,
        reason="canonical run reference recorded; input provenance could not be completed",
    )


def _execute_remote_compute(
    ctx: ActionExecution,
    action: ActionRequest,
    descriptor: Any,
    arguments: dict[str, Any],
) -> UUID:
    """Remote explicit action: the SAME canonical capability path the human compute
    endpoint uses (`services.compute_submit_handle` + `record_compute_run`).

    The provider handle is a CONFIRMED external side effect; only then is the
    canonical RunReference created/reused."""
    session = ctx.session
    provider_key = descriptor.provider_key
    if not provider_key or not action.tool_id.endswith(COMPUTE_SUBMIT_SUFFIX):
        raise ConflictError("remote explicit action is not a supported compute submission")
    if descriptor.capability_kind is not CapabilityKind.COMPUTE:
        raise ConflictError("provider capability is no longer compute")
    bindings = [
        InputBinding(kind=item.kind, resource_id=item.resource_id, role=item.role)
        for item in ComputeSubmissionCreate.model_validate(arguments).inputs
    ]
    handle = services.compute_submit_handle(
        session,
        ctx.registry,
        ctx.secret_store,
        ctx.content_store,
        action.actor_id,
        action.project_id,
        provider_key,
        arguments["task_kind"],
        bindings,
        arguments.get("params") or {},
    )
    return _record_confirmed_run(ctx, action, handle, bindings)


def _preflight_referenced_resources(
    ctx: ActionExecution,
    project_id: UUID,
    descriptor: Any,
    arguments: dict[str, Any],
) -> None:
    """Revalidate CURRENT visibility of every resource a remote action references,
    BEFORE the durable claim. A resource unlinked after the proposal fails closed
    with no side effect and leaves the action retryable once it is visible again.
    (The canonical capability path independently re-checks this at the boundary.)"""
    if descriptor.execution_class is not ToolExecutionClass.REMOTE:
        return
    if descriptor.capability_kind is not CapabilityKind.COMPUTE:
        return
    parsed = ComputeSubmissionCreate.model_validate(arguments)
    for item in parsed.inputs:
        persistence.require_visible(ctx.session, project_id, item.resource_id)


def execute_action_request(
    ctx: ActionExecution,
    actor_id: UUID,
    project_id: UUID,
    action_request_id: UUID,
) -> ActionRequest:
    """Execute ONE pending Action Request through the canonical path.

    1. Ownership + current Project readability (403 / 404, never an oracle).
    2. Preflight against CURRENT truth: pending state, current ToolCatalog and
       autonomy, current canonical input schema, current mutation-capable
       membership, current resource visibility, current provider/credential
       availability. Every refusal here leaves the action `pending` and performs
       NO side effect.
    3. Durable one-shot claim (`pending -> executing`, committed) so two
       simultaneous human clicks cannot submit twice.
    4. Canonical execution, then a terminal outcome.
    """
    session = ctx.session
    action = _owned_action_request(session, actor_id, project_id, action_request_id)
    if action.status != ActionRequestStatus.PENDING.value:
        raise ConflictError(
            f"action request is {action.status!r}; only a pending action can be executed"
        )

    # --- preflight: no side effect, no claim consumed ------------------------
    mutation_capable_membership(session, actor_id, project_id)
    descriptor = _current_descriptor(ctx, actor_id, project_id, action)
    arguments = _revalidate_arguments(ctx, action)
    if not explicit_arguments_match_provider(descriptor.provider_key, arguments):
        raise ConflictError(
            "stored action arguments name a provider other than the tool's; refusing to execute"
        )
    _preflight_referenced_resources(ctx, project_id, descriptor, arguments)

    # --- durable one-shot claim ----------------------------------------------
    if not _claim(session, action_request_id):
        raise ConflictError("action request is no longer pending")

    # --- canonical execution -------------------------------------------------
    # Transaction discipline: the canonical commands this path reuses own their own
    # commit (`commit_decision_row`, `_persist_run_reference_trusted`), so a SAVEPOINT
    # cannot wrap them (an inner commit closes the outer transaction). Instead every
    # failure path ROLLS BACK before writing the outcome, which discards any
    # uncommitted partial local write, and `_settle` tolerates a session left in a
    # failed transaction. A confirmed-but-unrecorded external run is retried through
    # the canonical get-or-create recording below.
    decision_id: UUID | None = None
    run_resource_id: UUID | None = None
    try:
        if descriptor.execution_class is ToolExecutionClass.LOCAL:
            decision_id = _execute_local(ctx, action, arguments)
        else:
            run_resource_id = _execute_remote_compute(ctx, action, descriptor, arguments)
    except _UnrecordedExternalSuccess as exc:
        # The external side effect is CONFIRMED (we hold the provider handle), but
        # the local canonical recording failed. Roll back first, then RECOVER the
        # canonical truth on a healthy transaction: retry the recording once, and
        # fall back to reading the reference back by its provider identity (the
        # failed attempt may already have committed it). The outcome is never
        # reported as "nothing recorded" when something was.
        session.rollback()
        recovered = _recover_confirmed_run(ctx, action, exc)
        if recovered.run_id is not None:
            _settle(
                session,
                action_request_id,
                ActionRequestStatus.SUCCEEDED,
                run_resource_id=recovered.run_id,
                reason=recovered.reason,
            )
        else:
            session.rollback()
            _settle(
                session,
                action_request_id,
                ActionRequestStatus.AMBIGUOUS,
                reason=(
                    "the provider accepted the submission "
                    f"({exc.handle.authority}/{exc.handle.native_id}) but no canonical "
                    f"run reference could be recorded: {exc}"
                ),
            )
    except (DomainError, CapabilityError) as exc:
        session.rollback()
        _settle(session, action_request_id, classify_provider_failure(exc), reason=str(exc))
    except Exception as exc:
        session.rollback()
        _settle(
            session,
            action_request_id,
            ActionRequestStatus.AMBIGUOUS,
            reason=f"unexpected execution failure: {type(exc).__name__}",
        )
    else:
        if descriptor.execution_class is ToolExecutionClass.LOCAL and decision_id is None:
            # The canonical local command reported success but named no canonical
            # result reference. The local side effect DID happen, but `succeeded`
            # requires a reference (durable CHECK), so ambiguity about the RESULT
            # identity is the only honest state.
            _settle(
                session,
                action_request_id,
                ActionRequestStatus.AMBIGUOUS,
                reason=(
                    "the local explicit action completed but produced no canonical "
                    "result reference"
                ),
            )
        elif descriptor.execution_class is ToolExecutionClass.REMOTE and run_resource_id is None:
            _settle(
                session,
                action_request_id,
                ActionRequestStatus.AMBIGUOUS,
                reason="the canonical run reference is unknown after a confirmed submission",
            )
        else:
            _settle(
                session,
                action_request_id,
                ActionRequestStatus.SUCCEEDED,
                run_resource_id=run_resource_id,
                decision_id=decision_id,
            )
    session.refresh(action)
    return action


__all__ = [
    "ActionExecution",
    "classify_provider_failure",
    "execute_action_request",
    "get_action_request",
    "list_action_requests",
    "propose_action_request",
    "reject_action_request",
]
