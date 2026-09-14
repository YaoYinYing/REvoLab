"""Phase 9 — persistent, Actor x Project scoped Project conversations.

A conversation is durable WORKING MEMORY, never Project truth, Agent authority,
RAG, or a scientific data model. The invariant:

    Persist transcript identity and bounded conversational continuity;
    never persist authority or a stale ProjectContext snapshot.

Every turn still rebuilds ProjectContext, the ToolCatalog, skill selection, and
authorization/capability availability from current canonical state. The
persisted transcript supplies only bounded conversational continuity to the
model and nothing else.

`AgentTurnRunner` remains the reusable execution implementation: this module
wraps it with persistence orchestration (load server-owned history -> run ->
persist bounded user/assistant rows), it never forks it.
"""

from __future__ import annotations

import threading
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from revolab.agent.model_backend import ChatMessage
from revolab.agent.runtime import AgentTurnRunner
from revolab.domain.errors import NotFoundError, ValidationError
from revolab.domain.identity import readable_membership
from revolab.enums import AgentTerminationReason, ConversationRole
from revolab.models import ConversationMessage, ProjectConversation
from revolab.schemas import (
    ContextSelectionCreate,
    ConversationDetailRead,
    ConversationMessageRead,
    ConversationRead,
    ConversationToolTraceSummaryRead,
    ConversationTurnRead,
)

# Bounded persistence limits (TODO.md section 13). The model only ever receives
# the server-selected bounded suffix; these bounds cap what is DURABLY written.
MAX_CONVERSATION_TITLE_CHARS = 200
MAX_CONVERSATION_MESSAGE_CHARS = 8000
MAX_TRACE_SUMMARY_ENTRIES = 64
MAX_TRACE_SUMMARY_FIELD_CHARS = 500
DEFAULT_CONVERSATION_TITLE = "New conversation"

# Process-level per-conversation execution locks for the SQLite substrate.
# `SELECT ... FOR UPDATE` is silently ignored by SQLite, so the row lock alone
# cannot order concurrent turns there; this lock restores the same guarantee for
# a single process. A weak-value map avoids unbounded growth, and a lock is only
# collected when uncontended (a waiting thread holds a strong reference).
_sqlite_turn_locks: weakref.WeakValueDictionary[UUID, threading.Lock] = (
    weakref.WeakValueDictionary()
)
_sqlite_turn_locks_guard = threading.Lock()


def _is_sqlite(session: Session) -> bool:
    return session.get_bind().dialect.name == "sqlite"


@contextmanager
def _turn_execution_lock(session: Session, conversation_id: UUID) -> Iterator[None]:
    """Serialize one conversation's turn on SQLite; on PostgreSQL the row lock
    taken inside `_execute_turn` is the ordering primitive."""
    if not _is_sqlite(session):
        yield
        return
    with _sqlite_turn_locks_guard:
        lock = _sqlite_turn_locks.setdefault(conversation_id, threading.Lock())
    with lock:
        yield


def _now() -> datetime:
    return datetime.now(UTC)


def _bounded(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value[:limit] if len(value) <= limit else value[: max(limit - 3, 0)] + "..."


def _owned_conversation(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    conversation_id: UUID,
    *,
    with_for_update: bool = False,
) -> ProjectConversation:
    """Resolve a conversation the current Actor owns, proving at the same time
    that the Actor can currently read an active Project.

    Fails closed: a non-member (or an inactive Project) raises 403 before the
    conversation is even looked up; a guessed UUID or a conversation owned by
    another member raises 404 — never an existence oracle. `with_for_update`
    takes the per-conversation row lock so the whole load->run->persist section
    serializes for turn execution."""
    readable_membership(session, actor_id, project_id)
    conversation = session.get(
        ProjectConversation, conversation_id, with_for_update=with_for_update
    )
    if (
        conversation is None
        or conversation.project_id != project_id
        or conversation.actor_id != actor_id
    ):
        raise NotFoundError("conversation not found")
    return conversation


def create_conversation(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    title: str | None = None,
) -> ConversationRead:
    readable_membership(session, actor_id, project_id)
    if title is not None and len(title) > MAX_CONVERSATION_TITLE_CHARS:
        raise ValidationError("conversation title exceeds the maximum length")
    conversation = ProjectConversation(
        project_id=project_id,
        actor_id=actor_id,
        title=title or DEFAULT_CONVERSATION_TITLE,
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return _conversation_read(conversation)


def list_conversations(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    *,
    include_archived: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[ConversationRead]:
    readable_membership(session, actor_id, project_id)
    statement = select(ProjectConversation).where(
        ProjectConversation.project_id == project_id,
        ProjectConversation.actor_id == actor_id,
    )
    if not include_archived:
        statement = statement.where(ProjectConversation.archived_at.is_(None))
    statement = (
        statement.order_by(ProjectConversation.updated_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = session.scalars(statement)
    return [_conversation_read(row) for row in rows]


def get_conversation(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    conversation_id: UUID,
    *,
    limit: int = 100,
    offset: int = 0,
    latest: bool = False,
) -> ConversationDetailRead:
    conversation = _owned_conversation(session, actor_id, project_id, conversation_id)
    total = session.scalar(
        select(func.count())
        .select_from(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
    )
    total = total or 0
    effective_offset = max(0, total - limit) if latest else offset
    rows = session.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.seq.asc())
        .offset(effective_offset)
        .limit(limit)
    )
    return ConversationDetailRead(
        **_conversation_read(conversation).model_dump(),
        messages=[_message_read(row) for row in rows],
        total_messages=total,
    )


def patch_conversation(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    conversation_id: UUID,
    *,
    title: str | None = None,
    archive: bool | None = None,
) -> ConversationRead:
    if title is None and archive is None:
        raise ValidationError("no conversation fields to update")
    if title is not None and len(title) > MAX_CONVERSATION_TITLE_CHARS:
        raise ValidationError("conversation title exceeds the maximum length")
    conversation = _owned_conversation(session, actor_id, project_id, conversation_id)
    if title is not None:
        conversation.title = title
    if archive is True and conversation.archived_at is None:
        conversation.archived_at = _now()
    elif archive is False and conversation.archived_at is not None:
        conversation.archived_at = None
    conversation.updated_at = _now()
    session.commit()
    session.refresh(conversation)
    return _conversation_read(conversation)


def run_conversation_turn(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    conversation_id: UUID,
    runner: AgentTurnRunner,
    *,
    message: str,
    selection: ContextSelectionCreate | None = None,
    history_limit: int = 20,
) -> ConversationTurnRead:
    """One durable turn: serialize per conversation, load server-owned bounded
    history, run the canonical AgentTurnRunner, then persist the bounded
    user/assistant transcript in ONE transaction. The client never supplies
    historical assistant messages; authority is still resolved by the runner from
    the one canonical ToolCatalog and current Project state.

    Serialization: PostgreSQL takes the conversation row lock (`SELECT ... FOR
    UPDATE`), which orders concurrent turns across processes. SQLite ignores
    `FOR UPDATE`, so on that substrate an explicit process-level execution lock
    per conversation provides the same ordering (SQLite is a single-process
    dev/test substrate; PostgreSQL remains the concurrency truth)."""
    if len(message) > MAX_CONVERSATION_MESSAGE_CHARS:
        raise ValidationError("message exceeds the maximum conversation message length")

    with _turn_execution_lock(session, conversation_id):
        return _execute_turn(
            session,
            actor_id,
            project_id,
            conversation_id,
            runner,
            message=message,
            selection=selection,
            history_limit=history_limit,
        )


def _execute_turn(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    conversation_id: UUID,
    runner: AgentTurnRunner,
    *,
    message: str,
    selection: ContextSelectionCreate | None = None,
    history_limit: int = 20,
) -> ConversationTurnRead:
    # Row lock FIRST: the model loop may be slow, and conversational continuity
    # requires that two concurrent turns on one conversation serialize their
    # read->compute->write as a unit.
    conversation = _owned_conversation(
        session, actor_id, project_id, conversation_id, with_for_update=True
    )
    if conversation.archived_at is not None:
        raise ValidationError("archived conversation cannot accept new turns")

    # Bounded suffix fetched in SQL (newest-first, then restored to ascending
    # order); the runner still applies the char bound. Never load the whole
    # transcript into memory for the model.
    suffix = session.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.seq.desc())
        .limit(max(history_limit, 1))
    )
    history = [
        ChatMessage(
            role=cast(Literal["user", "assistant"], row.role),
            content=row.content,
        )
        for row in reversed(list(suffix))
        if row.role in {ConversationRole.USER.value, ConversationRole.ASSISTANT.value}
    ]

    result = runner.run(session, actor_id, project_id, message, selection, history)

    last_seq = session.scalar(
        select(func.coalesce(func.max(ConversationMessage.seq), 0)).where(
            ConversationMessage.conversation_id == conversation_id
        )
    )
    user_row = ConversationMessage(
        conversation_id=conversation_id,
        seq=(last_seq or 0) + 1,
        role=ConversationRole.USER.value,
        content=message,
    )
    session.add(user_row)

    assistant_row: ConversationMessage | None = None
    final_response = (result.final_response or "").strip()
    if final_response:
        assistant_row = ConversationMessage(
            conversation_id=conversation_id,
            seq=(last_seq or 0) + 2,
            role=ConversationRole.ASSISTANT.value,
            content=_bounded_assistant_content(final_response),
            termination_reason=result.termination_reason.value,
            tool_trace_summary=_summarize_trace(result.tool_trace),
        )
        session.add(assistant_row)

    conversation.updated_at = _now()
    session.add(conversation)
    session.commit()

    session.refresh(user_row)
    user_read = _message_read(user_row)
    assistant_read = _message_read(assistant_row) if assistant_row is not None else None
    return ConversationTurnRead(
        conversation_id=conversation_id,
        turn=result,
        user_message=user_read,
        assistant_message=assistant_read,
    )


def _bounded_assistant_content(content: str) -> str:
    """Assistant output is server-owned text; bound it before durable write with
    an explicit truncation marker (never an unreported silent cut)."""
    if len(content) <= MAX_CONVERSATION_MESSAGE_CHARS:
        return content
    return content[: MAX_CONVERSATION_MESSAGE_CHARS - 20] + "\n...[truncated]"


def _summarize_trace(trace: list[Any]) -> list[dict[str, Any]]:
    """Reduce the live (non-secret) tool trace to a bounded inert summary for
    durable display after reload. Full ToolResult payloads and PendingAction
    arguments are deliberately NOT persisted (TODO.md section 4)."""
    summaries: list[dict[str, Any]] = []
    for entry in trace[:MAX_TRACE_SUMMARY_ENTRIES]:
        status = getattr(entry, "status", None)
        status_value = getattr(status, "value", None) or str(status or "")
        pending = getattr(entry, "pending_action", None)
        summaries.append(
            {
                "tool_id": getattr(entry, "tool_id"),
                "status": status_value,
                "error": _bounded(getattr(entry, "error", None), MAX_TRACE_SUMMARY_FIELD_CHARS),
                "pending_tool_id": getattr(pending, "tool_id", None) if pending else None,
                "pending_summary": _bounded(getattr(pending, "summary", None), MAX_TRACE_SUMMARY_FIELD_CHARS)
                if pending
                else None,
                "pending_reason": _bounded(getattr(pending, "reason", None), MAX_TRACE_SUMMARY_FIELD_CHARS)
                if pending
                else None,
            }
        )
    return summaries


def _conversation_read(conversation: ProjectConversation) -> ConversationRead:
    return ConversationRead(
        id=conversation.id,
        project_id=conversation.project_id,
        actor_id=conversation.actor_id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        archived_at=conversation.archived_at,
    )


def _message_read(row: ConversationMessage) -> ConversationMessageRead:
    tool_trace = [
        ConversationToolTraceSummaryRead(**item)
        for item in (row.tool_trace_summary or [])
    ]
    return ConversationMessageRead(
        id=row.id,
        conversation_id=row.conversation_id,
        seq=row.seq,
        role=ConversationRole(row.role),
        content=row.content,
        termination_reason=(
            AgentTerminationReason(row.termination_reason) if row.termination_reason else None
        ),
        tool_trace=tool_trace,
        created_at=row.created_at,
    )


__all__ = [
    "create_conversation",
    "get_conversation",
    "list_conversations",
    "patch_conversation",
    "run_conversation_turn",
]
