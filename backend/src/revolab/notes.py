"""Phase 10 — Project Notebook: Project-shared working documents.

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
member's work. PostgreSQL row locking orders concurrent appends; the
`(note_id, revision_seq)` uniqueness constraint is the backend-independent
backstop, so SQLite never silently claims stronger concurrency semantics than it
actually provides.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from revolab import queries
from revolab.domain.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from revolab.domain.identity import mutation_capable_membership, readable_membership
from revolab.enums import ResourceKind
from revolab.models import (
    Decision,
    Evidence,
    GlobalResourceRegistry,
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
    """Create a Project Note with its first immutable revision (seq 1)."""
    _check_title(title)
    _check_body(body)
    mention_payload = list(mentions or [])
    _check_mention_count(mention_payload)
    mutation_capable_membership(session, actor_id, project_id)

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
    _write_mentions(session, project_id, revision, mention_payload)
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

    A stale base (the server's latest no longer equals what the client edited)
    raises a typed 409 rather than silently overwriting another member's work."""
    _check_body(body)
    mention_payload = list(mentions or [])
    _check_mention_count(mention_payload)

    note = _mutable_note(session, actor_id, project_id, note_id, with_for_update=True)
    if note.archived_at is not None:
        raise ValidationError("archived note cannot accept new revisions")

    latest = _latest_revision_seq(session, note_id)
    if latest is None:
        raise NotFoundError("note has no revision")
    if base_revision_seq != latest:
        raise ConflictError(
            "note revision conflict: the note changed since it was edited"
        )

    revision = ProjectNoteRevision(
        note_id=note_id,
        revision_seq=latest + 1,
        body=body,
        created_by_actor_id=actor_id,
    )
    session.add(revision)
    try:
        session.flush()
    except IntegrityError as exc:
        # Backend-independent backstop: SQLite ignores SELECT ... FOR UPDATE, so
        # the uniqueness constraint is what actually prevents a lost update there.
        session.rollback()
        raise ConflictError(
            "note revision conflict: the note changed since it was edited"
        ) from exc
    _write_mentions(session, project_id, revision, mention_payload)
    note.updated_at = _now()
    session.add(note)
    session.commit()
    session.refresh(revision)
    return _revision_read(session, project_id, revision)


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
    note = _mutable_note(session, actor_id, project_id, note_id)
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
    statement = statement.order_by(ProjectNote.updated_at.desc()).offset(offset).limit(limit)
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
# Mentions
# ---------------------------------------------------------------------------


def _write_mentions(
    session: Session,
    project_id: UUID,
    revision: ProjectNoteRevision,
    mentions: list[NoteMentionCreate],
) -> None:
    """Validate every mention through the CURRENT Project read lens and persist
    it against the immutable revision.

    Knowing a UUID is not authority to mention a hidden resource: an invisible or
    foreign target raises one uniform 403 (no existence oracle) and the whole
    revision write is rolled back by the caller's transaction."""
    if not mentions:
        return
    visible = queries.visible_resources(session, project_id)
    for ordinal, mention in enumerate(mentions):
        row = NoteMention(revision_id=revision.revision_id, ordinal=ordinal)
        if mention.resource_id is not None:
            registry = session.get(GlobalResourceRegistry, mention.resource_id)
            if registry is None or mention.resource_id not in visible:
                raise AuthorizationError("mentioned resource is not visible in this project")
            row.target_resource_id = registry.resource_id
            row.target_kind = registry.resource_kind
        elif mention.evidence_id is not None:
            evidence = session.get(Evidence, mention.evidence_id)
            if (
                evidence is None
                or evidence.project_id != project_id
                or evidence.archived_at is not None
            ):
                raise AuthorizationError("mentioned evidence is not visible in this project")
            row.target_evidence_id = evidence.id
        else:
            decision = session.get(Decision, mention.decision_id)
            if (
                decision is None
                or decision.project_id != project_id
                or decision.archived_at is not None
            ):
                raise AuthorizationError("mentioned decision is not visible in this project")
            row.target_decision_id = decision.id
        session.add(row)


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
        label: str | None = None
        resolved = kind is not None
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
        evidence = session.get(Evidence, row.target_evidence_id)
        resolved = (
            evidence is not None
            and evidence.project_id == project_id
            and evidence.archived_at is None
        )
        return NoteMentionRead(
            mention_id=row.id,
            ordinal=row.ordinal,
            evidence_id=row.target_evidence_id,
            label=(evidence.label or f"Evidence ({evidence.kind})") if resolved and evidence else None,
            resolved=resolved,
        )
    decision = session.get(Decision, row.target_decision_id)
    resolved = (
        decision is not None
        and decision.project_id == project_id
        and decision.archived_at is None
    )
    return NoteMentionRead(
        mention_id=row.id,
        ordinal=row.ordinal,
        decision_id=row.target_decision_id,
        label=decision.title if resolved and decision else None,
        resolved=resolved,
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

    Returns the refs plus a `truncated` flag: exceeding `max_notes` or clipping a
    body to `max_note_chars` is reported explicitly, never silently."""
    refs: list[NoteRefRead] = []
    truncated = False
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
    for note_id, revision_id in candidates:
        if len(refs) >= max_notes:
            truncated = True
            break
        ref = _resolve(note_id, revision_id)
        if ref is None:
            continue
        if ref.truncated:
            truncated = True
        refs.append(ref)
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


def _note_read(session: Session, note: ProjectNote) -> NoteRead:
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
    base = _note_read(session, note)
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
