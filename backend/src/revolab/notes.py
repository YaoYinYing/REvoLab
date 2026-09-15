"""Phase 10 — Project Notebook: Project-shared working documents.

This module is the **application-level Notebook service** (the same layer as
`revolab.services`), not a Core domain leaf: it owns Project-scoped Note /
Revision / Mention persistence and composes the owning domains' **public
contracts** for cross-domain mention composition rather than reaching into
`Evidence`/`Decision` ORM internals. It therefore adds no Project -> Evidence /
Knowledge edge to the accepted nine-domain Core DAG.

The canonical ladder this module implements:

    Conversation   private Actor x Project working memory
        |  explicit human capture
    Project Note   Project-shared working knowledge (editable/versioned)
        |  explicit scientific interpretation
    Evidence       typed scientific claim
        |  explicit Decision commit
    Decision       committed Project knowledge

A Note is NOT a ScientificObject, a GlobalResourceRegistry entry, an Evidence
claim, a Decision, a provenance node, or Agent memory. It never enters the
scientific graph and it never becomes Project truth by being written. The
Project is its namespace/lifecycle owner; archiving is non-destructive.

Revision semantics: `ProjectNoteRevision` rows are immutable and the latest
revision is DERIVED from the max `revision_seq` (no mutable current pointer).
Appending a revision carries the base sequence the client edited; a stale base
fails closed with a typed `ConflictError` (409) instead of overwriting another
member's work.

Atomicity: every fallible mention validation/authorization runs BEFORE any
durable mutation, so a rejected command leaves no flushed Note, Revision, or
Mention row behind (a caller that catches the typed error and later commits an
unrelated operation on the same Session cannot persist a ghost).

Mention semantics on edit: an OMITTED `mentions` inherits the previous
revision's mention identities (preserving already-authorized references even if a
target later becomes unavailable); an explicit `[]` clears them; an explicit
non-empty list replaces them after normal current-Project authorization.

PostgreSQL row locking orders concurrent appends; the `(note_id, revision_seq)`
uniqueness constraint is the backend-independent backstop. SQLite ignores
`FOR UPDATE`, so it additionally uses a bounded process-level per-note lock and
never claims cross-process semantics.
"""

from __future__ import annotations

import threading
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from revolab import queries
from revolab.domain import knowledge as knowledge_domain
from revolab.domain import provenance as provenance_domain
from revolab.domain.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from revolab.domain.identity import mutation_capable_membership, readable_membership
from revolab.enums import ResourceKind
from revolab.models import (
    NoteMention,
    ProjectNote,
    ProjectNoteRevision,
)
from revolab.schemas import (
    MAX_NOTE_BODY_CHARS,
    MAX_NOTE_MENTIONS_PER_REVISION,
    MAX_NOTE_TITLE_CHARS,
    NoteDetailRead,
    NoteMentionCreate,
    NoteMentionRead,
    NoteRead,
    NoteRefRead,
    NoteRevisionRead,
)

DEFAULT_NOTES_PAGE_LIMIT = 50
DEFAULT_REVISIONS_PAGE_LIMIT = 100
# Defense-in-depth hard ceilings for a non-HTTP caller: the wire schema already
# bounds these, but the resolver must not trust its inputs.
MAX_NOTES_SELECTION = 50
MAX_NOTE_SELECTION_CHARS = MAX_NOTE_BODY_CHARS

# Process-level per-note mutation locks for the SQLite substrate. PostgreSQL uses
# the note row lock (`SELECT ... FOR UPDATE`), which orders appends AND archive
# against each other across processes. SQLite silently ignores `FOR UPDATE`, so
# without this lock a concurrent append could read `archived_at == NULL`, have an
# archive commit, and then insert a revision on an archived note. SQLite remains a
# single-process dev/test substrate; this restores the same ordering for one
# process and never claims cross-process semantics.
_sqlite_note_locks: weakref.WeakValueDictionary[UUID, threading.Lock] = (
    weakref.WeakValueDictionary()
)
_sqlite_note_locks_guard = threading.Lock()
NOTE_MUTATION_LOCK_TIMEOUT_SECONDS = 30.0


def _is_sqlite(session: Session) -> bool:
    return session.get_bind().dialect.name == "sqlite"


@contextmanager
def _note_mutation_lock(session: Session, note_id: UUID) -> Iterator[None]:
    """Serialize one note's append/archive on SQLite; on PostgreSQL the row lock
    taken inside `_mutable_note(..., with_for_update=True)` is the ordering
    primitive. A bounded wait yields a typed retryable 409."""
    if not _is_sqlite(session):
        yield
        return
    with _sqlite_note_locks_guard:
        lock = _sqlite_note_locks.setdefault(note_id, threading.Lock())
    if not lock.acquire(timeout=NOTE_MUTATION_LOCK_TIMEOUT_SECONDS):
        raise ConflictError("another note edit is already in progress; retry")
    try:
        yield
    finally:
        lock.release()


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Authorization / resolution
# ---------------------------------------------------------------------------


def _readable_note(
    session: Session, actor_id: UUID, project_id: UUID, note_id: UUID
) -> ProjectNote:
    """Resolve a Note readable by this Actor.

    The readability check runs BEFORE the note lookup, so a non-member (or a
    tombstoned Project) gets one uniform 403 whether or not the note exists —
    never an existence oracle. A note in another Project is a 404."""
    readable_membership(session, actor_id, project_id)
    note = session.get(ProjectNote, note_id)
    if note is None or note.project_id != project_id:
        raise NotFoundError("note not found")
    return note


def _mutable_note(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    note_id: UUID,
    *,
    with_for_update: bool = False,
) -> ProjectNote:
    """Resolve a Note this Actor may mutate (owner/member). Viewers and
    non-members fail closed before any note is disclosed."""
    mutation_capable_membership(session, actor_id, project_id)
    statement = select(ProjectNote).where(ProjectNote.id == note_id)
    if with_for_update and session.get_bind().dialect.name != "sqlite":
        statement = statement.with_for_update()
    note = session.scalar(statement)
    if note is None or note.project_id != project_id:
        raise NotFoundError("note not found")
    return note


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


def _check_title(title: str) -> None:
    if not title or len(title) > MAX_NOTE_TITLE_CHARS:
        raise ValidationError("note title is empty or exceeds the maximum length")


def _check_body(body: str) -> None:
    if not body or len(body) > MAX_NOTE_BODY_CHARS:
        raise ValidationError("note body is empty or exceeds the maximum length")


def _check_mention_count(mentions: list[NoteMentionCreate]) -> None:
    if len(mentions) > MAX_NOTE_MENTIONS_PER_REVISION:
        raise ValidationError("note revision exceeds the maximum number of mentions")


@dataclass(frozen=True)
class _MentionSpec:
    """A fully validated mention target, ready to persist against a revision."""

    resource_id: UUID | None = None
    resource_kind: str | None = None
    evidence_id: UUID | None = None
    decision_id: UUID | None = None


# ---------------------------------------------------------------------------
# Mentions
# ---------------------------------------------------------------------------


def _resolve_mentions(
    session: Session, project_id: UUID, mentions: list[NoteMentionCreate]
) -> list[_MentionSpec]:
    """Validate EVERY explicitly supplied mention through the current Project
    read lens and return persistable specs.

    This is pure validation: it performs no durable mutation, so it runs before
    the Note/Revision rows are created and a typed failure leaves nothing flushed.
    Cross-domain targets are validated through the owning domains' public
    contracts, never their ORM internals. Knowing a UUID is not authority: a
    hidden/archived/foreign/unknown target raises one uniform 403 (no existence
    oracle)."""
    if not mentions:
        return []
    visible = queries.visible_resources(session, project_id)
    specs: list[_MentionSpec] = []
    for mention in mentions:
        if mention.resource_id is not None:
            kind = visible.get(mention.resource_id)
            if kind is None or not queries.resource_mention_active(
                session, mention.resource_id, kind
            ):
                raise AuthorizationError("mentioned resource is not visible in this project")
            specs.append(_MentionSpec(resource_id=mention.resource_id, resource_kind=kind.value))
        elif mention.evidence_id is not None:
            target = provenance_domain.evidence_mention_target(
                session, project_id, mention.evidence_id
            )
            if target is None or not target.active:
                raise AuthorizationError("mentioned evidence is not visible in this project")
            specs.append(_MentionSpec(evidence_id=target.evidence_id))
        else:
            if mention.decision_id is None:  # schema/CHECK guarantee exactly one target
                raise ValidationError("note mention must name exactly one target")
            decision_target = knowledge_domain.decision_mention_target(
                session, project_id, mention.decision_id
            )
            if decision_target is None or not decision_target.active:
                raise AuthorizationError("mentioned decision is not visible in this project")
            specs.append(_MentionSpec(decision_id=decision_target.decision_id))
    return specs


def _inherit_mentions(session: Session, revision: ProjectNoteRevision) -> list[_MentionSpec]:
    """Copy the mention IDENTITIES of a previous revision onto a new one.

    Inherited mentions were already authorized when they were created and are
    deliberately NOT re-validated against the current lens: a target that later
    becomes unavailable stays a historical contextual reference (resolved=false)
    rather than silently disappearing from the note."""
    rows = session.scalars(
        select(NoteMention)
        .where(NoteMention.revision_id == revision.revision_id)
        .order_by(NoteMention.ordinal.asc())
    )
    return [
        _MentionSpec(
            resource_id=row.target_resource_id,
            resource_kind=row.target_kind,
            evidence_id=row.target_evidence_id,
            decision_id=row.target_decision_id,
        )
        for row in rows
    ]


def _write_mention_rows(
    session: Session, revision: ProjectNoteRevision, specs: list[_MentionSpec]
) -> None:
    """Persist already-validated mention specs against an immutable revision."""
    for ordinal, spec in enumerate(specs):
        session.add(
            NoteMention(
                revision_id=revision.revision_id,
                ordinal=ordinal,
                target_resource_id=spec.resource_id,
                target_kind=spec.resource_kind,
                target_evidence_id=spec.evidence_id,
                target_decision_id=spec.decision_id,
            )
        )


def _check_mention_specs_count(specs: list[_MentionSpec]) -> None:
    if len(specs) > MAX_NOTE_MENTIONS_PER_REVISION:
        raise ValidationError("note revision exceeds the maximum number of mentions")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def create_note(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    *,
    title: str,
    body: str,
    mentions: list[NoteMentionCreate] | None = None,
) -> NoteDetailRead:
    """Create a Project Note with its first immutable revision (seq 1).

    Atomic: title/body bounds, membership, and every mention target are validated
    BEFORE any row is added or flushed, so a rejected command leaves no ghost
    Note/Revision/Mention behind."""
    _check_title(title)
    _check_body(body)
    mention_payload = list(mentions or [])
    _check_mention_count(mention_payload)
    mutation_capable_membership(session, actor_id, project_id)
    specs = _resolve_mentions(session, project_id, mention_payload)
    _check_mention_specs_count(specs)

    note = ProjectNote(project_id=project_id, created_by_actor_id=actor_id, title=title)
    session.add(note)
    session.flush()
    revision = ProjectNoteRevision(
        note_id=note.id,
        revision_seq=1,
        body=body,
        created_by_actor_id=actor_id,
    )
    session.add(revision)
    session.flush()
    _write_mention_rows(session, revision, specs)
    session.commit()
    session.refresh(note)
    session.refresh(revision)
    return _note_detail(session, project_id, note, revision)


def append_revision(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    note_id: UUID,
    *,
    base_revision_seq: int,
    body: str,
    mentions: list[NoteMentionCreate] | None = None,
) -> NoteRevisionRead:
    """Append one immutable revision at `base_revision_seq + 1`.

    `mentions` semantics: `None` (omitted) inherits the previous revision's
    mention identities, `[]` clears them, and a non-empty list replaces them after
    normal current-Project authorization. All mention validation happens before
    the revision row is added, so a rejected command leaves nothing flushed.

    A stale base (the server's latest no longer equals what the client edited)
    raises a typed 409 rather than silently overwriting another member's work."""
    _check_body(body)
    if mentions is not None:
        _check_mention_count(list(mentions))

    with _note_mutation_lock(session, note_id):
        note = _mutable_note(session, actor_id, project_id, note_id, with_for_update=True)
        if note.archived_at is not None:
            raise ValidationError("archived note cannot accept new revisions")

        latest_seq = _latest_revision_seq(session, note_id)
        if latest_seq is None:
            raise NotFoundError("note has no revision")
        if base_revision_seq != latest_seq:
            raise ConflictError(
                "note revision conflict: the note changed since it was edited"
            )
        previous = _latest_revision(session, note_id)
        if previous is None:
            raise NotFoundError("note has no revision")

        # Resolve mentions BEFORE any durable mutation. Omitted -> inherit,
        # [] -> clear, non-empty -> validate/replace.
        if mentions is None:
            specs = _inherit_mentions(session, previous)
        else:
            specs = _resolve_mentions(session, project_id, list(mentions))
        _check_mention_specs_count(specs)

        revision = ProjectNoteRevision(
            note_id=note_id,
            revision_seq=latest_seq + 1,
            body=body,
            created_by_actor_id=actor_id,
        )
        try:
            # SAVEPOINT, not `session.rollback()`: a uniqueness collision must
            # discard ONLY this command's own insert, never a composing caller's
            # pending work. The row lock above normally prevents the collision;
            # this is the backend-independent backstop (SQLite ignores FOR UPDATE).
            with session.begin_nested():
                session.add(revision)
                session.flush()
        except IntegrityError as exc:
            if revision in session:
                session.expunge(revision)
            if not _is_revision_uniqueness_conflict(exc):
                raise
            raise ConflictError(
                "note revision conflict: the note changed since it was edited"
            ) from exc
        _write_mention_rows(session, revision, specs)
        note.updated_at = _now()
        session.add(note)
        session.commit()
        session.refresh(revision)
        return _revision_read(session, project_id, revision)


def _is_revision_uniqueness_conflict(exc: IntegrityError) -> bool:
    """Narrow, backend-aware detection of the note-revision table's ONE unique
    constraint. Unrelated integrity failures (FK, actor, mention) are re-raised
    untouched instead of being mis-reported as a stale-edit 409."""
    orig = exc.orig
    diagnostics = getattr(orig, "diag", None)
    if diagnostics is not None:  # PostgreSQL psycopg
        return bool(diagnostics.constraint_name == "uq_note_revision_seq")
    message = str(orig)
    return (
        "UNIQUE constraint failed" in message
        and "project_note_revisions" in message
    )


def patch_note(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    note_id: UUID,
    *,
    title: str | None = None,
    archive: bool | None = None,
) -> NoteRead:
    """Rename and/or archive a Note. Body changes are a new revision, never a
    patch of immutable content."""
    if title is None and archive is None:
        raise ValidationError("no note fields to update")
    if title is not None:
        _check_title(title)
    with _note_mutation_lock(session, note_id):
        note = _mutable_note(session, actor_id, project_id, note_id, with_for_update=True)
        if title is not None:
            note.title = title
        if archive is True and note.archived_at is None:
            note.archived_at = _now()
        elif archive is False and note.archived_at is not None:
            note.archived_at = None
        note.updated_at = _now()
        session.add(note)
        session.commit()
        session.refresh(note)
        return _note_read(session, note)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def list_notes(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    *,
    include_archived: bool = False,
    limit: int = DEFAULT_NOTES_PAGE_LIMIT,
    offset: int = 0,
) -> list[NoteRead]:
    """All Notes shared with the readable members of this Project."""
    readable_membership(session, actor_id, project_id)
    statement = select(ProjectNote).where(ProjectNote.project_id == project_id)
    if not include_archived:
        statement = statement.where(ProjectNote.archived_at.is_(None))
    statement = (
        statement.order_by(ProjectNote.updated_at.desc(), ProjectNote.id.desc())
        .offset(offset)
        .limit(limit)
    )
    notes = list(session.scalars(statement))
    return [_note_read(session, note) for note in notes]


def get_note(
    session: Session, actor_id: UUID, project_id: UUID, note_id: UUID
) -> NoteDetailRead:
    note = _readable_note(session, actor_id, project_id, note_id)
    latest = _latest_revision(session, note_id)
    return _note_detail(session, project_id, note, latest)


def list_revisions(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    note_id: UUID,
    *,
    limit: int = DEFAULT_REVISIONS_PAGE_LIMIT,
    offset: int = 0,
) -> list[NoteRevisionRead]:
    """The immutable revision history, oldest first."""
    _readable_note(session, actor_id, project_id, note_id)
    revisions = session.scalars(
        select(ProjectNoteRevision)
        .where(ProjectNoteRevision.note_id == note_id)
        .order_by(ProjectNoteRevision.revision_seq.asc())
        .offset(offset)
        .limit(limit)
    )
    return [_revision_read(session, project_id, revision) for revision in revisions]


# ---------------------------------------------------------------------------
# Mention read projection
# ---------------------------------------------------------------------------


def _mentions_read(
    session: Session, project_id: UUID, revision_id: UUID
) -> list[NoteMentionRead]:
    visible = queries.visible_resources(session, project_id)
    rows = session.scalars(
        select(NoteMention)
        .where(NoteMention.revision_id == revision_id)
        .order_by(NoteMention.ordinal.asc())
    )
    return [_mention_read(session, project_id, visible, row) for row in rows]


def _mention_read(
    session: Session,
    project_id: UUID,
    visible: dict[UUID, ResourceKind],
    row: NoteMention,
) -> NoteMentionRead:
    """Resolve the mention against the current read lens. An unavailable target
    yields `resolved=False` with a null label — the historical revision is never
    rewritten."""
    if row.target_resource_id is not None:
        kind = visible.get(row.target_resource_id)
        resolved = kind is not None and queries.resource_mention_active(
            session, row.target_resource_id, kind
        )
        label: str | None = None
        if resolved and kind is not None:
            label = _resource_label(session, row.target_resource_id, kind)
        return NoteMentionRead(
            mention_id=row.id,
            ordinal=row.ordinal,
            resource_kind=ResourceKind(row.target_kind) if row.target_kind else None,
            resource_id=row.target_resource_id,
            label=label,
            resolved=resolved,
        )
    if row.target_evidence_id is not None:
        evidence_target = provenance_domain.evidence_mention_target(
            session, project_id, row.target_evidence_id
        )
        return NoteMentionRead(
            mention_id=row.id,
            ordinal=row.ordinal,
            evidence_id=row.target_evidence_id,
            label=evidence_target.label if evidence_target is not None else None,
            resolved=bool(evidence_target is not None and evidence_target.active),
        )
    if row.target_decision_id is None:  # CHECK guarantees exactly one target
        raise NotFoundError("note mention has no target")
    decision_target = knowledge_domain.decision_mention_target(
        session, project_id, row.target_decision_id
    )
    return NoteMentionRead(
        mention_id=row.id,
        ordinal=row.ordinal,
        decision_id=row.target_decision_id,
        label=decision_target.title if decision_target is not None else None,
        resolved=bool(decision_target is not None and decision_target.active),
    )


def _resource_label(session: Session, resource_id: UUID, kind: ResourceKind) -> str | None:
    try:
        summary = queries.reference_summary(session, resource_id, kind)
    except (NotFoundError, LookupError):
        return None
    name = summary.get("name")
    if isinstance(name, str) and name:
        return name
    title = summary.get("title")
    if isinstance(title, str) and title:
        return title
    native_id = summary.get("native_id")
    if isinstance(native_id, str) and native_id:
        return native_id
    return kind.value


# ---------------------------------------------------------------------------
# Read projections
# ---------------------------------------------------------------------------


def resolve_selected_notes(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    note_ids: list[UUID] | None,
    note_revision_ids: list[UUID] | None,
    *,
    max_notes: int,
    max_note_chars: int,
) -> tuple[list[NoteRefRead], bool]:
    """Resolve an explicit Note context selection into bounded untrusted refs.

    `note_ids` resolves each note's latest revision; `note_revision_ids` pins an
    exact immutable revision. Every id is validated against THIS Project (a note
    in another Project, or an unknown id, raises the same 403 — no existence
    oracle). Notes are deliberately NOT resolved through the global-resource
    visibility lens: a Note is not a GlobalResourceRegistry entry.

    The function authorizes itself against the active Project (readable
    membership) so the Note read lens is self-contained rather than relying on
    the caller's prior check.

    Returns the refs plus a `truncated` flag: exceeding `max_notes` or clipping a
    body to `max_note_chars` is reported explicitly, never silently."""
    readable_membership(session, actor_id, project_id)
    max_notes = max(0, min(max_notes, MAX_NOTES_SELECTION))
    max_note_chars = max(0, min(max_note_chars, MAX_NOTE_SELECTION_CHARS))
    seen: set[UUID] = set()

    def _build(note: ProjectNote, revision: ProjectNoteRevision) -> NoteRefRead:
        body = revision.body
        clipped = False
        if len(body) > max_note_chars:
            body = body[: max(max_note_chars - 20, 0)] + "\n...[truncated]"
            clipped = True
        return NoteRefRead(
            note_id=note.id,
            revision_id=revision.revision_id,
            revision_seq=revision.revision_seq,
            title=note.title,
            body=body,
            truncated=clipped,
            archived_at=note.archived_at,
        )

    def _resolve(note_id: UUID | None, revision_id: UUID | None) -> NoteRefRead | None:
        if revision_id is not None:
            revision = session.get(ProjectNoteRevision, revision_id)
            if revision is None:
                raise AuthorizationError("selected note revision is not visible in this project")
            note_id = revision.note_id
        if note_id is None:
            raise AuthorizationError("selected note is not visible in this project")
        note = session.get(ProjectNote, note_id)
        if note is None or note.project_id != project_id:
            raise AuthorizationError("selected note is not visible in this project")
        revision = (
            _latest_revision(session, note_id)
            if revision_id is None
            else session.get(ProjectNoteRevision, revision_id)
        )
        if revision is None:
            raise AuthorizationError("selected note has no revision")
        if revision.revision_id in seen:
            return None
        seen.add(revision.revision_id)
        return _build(note, revision)

    # Deterministic order: exact latest-of-note selections first, then pinned
    # historical revisions. An already-emitted revision is skipped once.
    candidates: list[tuple[UUID | None, UUID | None]] = [
        (note_id, None) for note_id in sorted(note_ids or [])
    ] + [(None, revision_id) for revision_id in sorted(note_revision_ids or [])]

    # Authorize/resolve EVERY explicitly supplied identity BEFORE applying
    # `max_notes`. Otherwise an invalid/foreign id sorted after the cap would
    # never be validated, and `max_notes=0` would bypass fail-closed validation
    # entirely.
    resolved: list[NoteRefRead] = []
    for note_id, revision_id in candidates:
        ref = _resolve(note_id, revision_id)
        if ref is not None:
            resolved.append(ref)

    refs = resolved[:max_notes]
    truncated = len(resolved) > max_notes or any(ref.truncated for ref in resolved)
    return refs, truncated


def _latest_revision(session: Session, note_id: UUID) -> ProjectNoteRevision | None:
    return session.scalar(
        select(ProjectNoteRevision)
        .where(ProjectNoteRevision.note_id == note_id)
        .order_by(ProjectNoteRevision.revision_seq.desc())
        .limit(1)
    )


def _latest_revision_seq(session: Session, note_id: UUID) -> int | None:
    return session.scalar(
        select(func.max(ProjectNoteRevision.revision_seq)).where(
            ProjectNoteRevision.note_id == note_id
        )
    )


def _revision_count(session: Session, note_id: UUID) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(ProjectNoteRevision)
            .where(ProjectNoteRevision.note_id == note_id)
        )
        or 0
    )


def _note_read(
    session: Session, note: ProjectNote, latest: ProjectNoteRevision | None = None
) -> NoteRead:
    if latest is None:
        latest = _latest_revision(session, note.id)
    return NoteRead(
        id=note.id,
        project_id=note.project_id,
        created_by_actor_id=note.created_by_actor_id,
        title=note.title,
        created_at=note.created_at,
        updated_at=note.updated_at,
        archived_at=note.archived_at,
        latest_revision_seq=latest.revision_seq if latest else 0,
        revision_count=_revision_count(session, note.id),
    )


def _note_detail(
    session: Session,
    project_id: UUID,
    note: ProjectNote,
    latest: ProjectNoteRevision | None,
) -> NoteDetailRead:
    base = _note_read(session, note, latest)
    return NoteDetailRead(
        **base.model_dump(),
        latest=_revision_read(session, project_id, latest) if latest is not None else None,
    )


def _revision_read(
    session: Session, project_id: UUID, revision: ProjectNoteRevision
) -> NoteRevisionRead:
    return NoteRevisionRead(
        revision_id=revision.revision_id,
        note_id=revision.note_id,
        revision_seq=revision.revision_seq,
        body=revision.body,
        created_by_actor_id=revision.created_by_actor_id,
        created_at=revision.created_at,
        mentions=_mentions_read(session, project_id, revision.revision_id),
    )


__all__ = [
    "append_revision",
    "create_note",
    "get_note",
    "list_notes",
    "list_revisions",
    "patch_note",
    "resolve_selected_notes",
]
