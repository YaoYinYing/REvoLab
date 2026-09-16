"""Project search — an authorization-aware, bounded read projection (Phase 12).

Search is the first Project-wide *retrieval* surface. It is deliberately **not** a
Core domain, a second scientific model, an index, Agent memory, or RAG:

    canonical Project truth
        -> authorization-aware bounded lexical query (this module)
        -> typed SearchHit references + bounded plain-text snippets

Governing invariants (TODO.md sections 4, 14, 15, 16):

> **Search discovers references; it never creates truth, widens authority, or
> silently enlarges Agent context.**

> **Retrieve first, select explicitly, then build context.**

Ownership: search owns *query parsing, authorized retrieval, ranking, bounded
snippets, and the typed SearchHit projection*. Every row it returns stays owned by
its canonical domain — there is no copied `SearchDocument` truth to synchronize.

Authorization is re-derived from CURRENT canonical state on every query and is
applied in SQL, before any row can be ranked, counted, or snippeted:

* the Project must be active and the Actor must have a readable membership;
* global resources are reached ONLY through this Project's `ProjectResourceLink`
  read lens (global registry existence is not Project visibility);
* Project-scoped Evidence/Decision rows must belong to this Project and be
  unarchived;
* Note search uses the CURRENT latest revision only;
* the private Conversation corpus is `Actor x Project` scoped.

An unauthorized row is never ranked, counted, snippeted, or reported as a hidden
result: a unique query matching only an inaccessible resource looks exactly like
no result.

Backends: PostgreSQL is the acceptance truth and contributes native
`to_tsvector`/`ts_rank` ordering (configuration `simple`, so scientific
identifiers never depend on English stemming). Matching itself is a
deterministic case-insensitive token-substring predicate that both PostgreSQL and
SQLite evaluate identically, so the SQLite development substrate keeps the same
*semantic* contract (same authorization, same target classes, same bounds) even
though its relevance ordering is simpler. Neither backend loads the Project into
Python: every corpus is filtered, ranked, and capped inside one bounded SQL query,
and only the bounded candidates are merged.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    String,
    and_,
    case,
    cast,
    exists,
    func,
    literal,
    literal_column,
    or_,
    select,
)
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement
from sqlalchemy.sql.selectable import ScalarSelect

from revolab.domain.errors import AuthorizationError, ValidationError
from revolab.domain.identity import readable_membership
from revolab.enums import (
    MY_CONVERSATION_TARGET_KINDS,
    PROJECT_SHARED_TARGET_KINDS,
    DecisionStatus,
    SearchMatchedField,
    SearchScope,
    SearchTargetKind,
)
from revolab.models import (
    ArtifactReference,
    ConversationMessage,
    Decision,
    Evidence,
    ExternalIdentity,
    ExternalReference,
    LiteratureReference,
    Project,
    ProjectConversation,
    ProjectNote,
    ProjectNoteRevision,
    ProjectResourceLink,
    RunReference,
    ScientificObjectAlias,
    ScientificObjectExternalIdentity,
    ScientificObjectSeries,
)
from revolab.schemas import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    MAX_SEARCH_QUERY_CHARS,
    MAX_SEARCH_SNIPPET_CHARS,
    MAX_SEARCH_TARGET_KINDS,
    MAX_SEARCH_TITLE_CHARS,
    MAX_SEARCH_TOKEN_CHARS,
    MAX_SEARCH_TOKENS,
    ProjectSearchResultsRead,
    SearchHitRead,
)

# Titles are bounded presentation data: a canonical title can legitimately be up
# to 500 chars (literature) and must not blow up the result envelope.
_TITLE_CHARS = MAX_SEARCH_TITLE_CHARS
# Characters that can never appear in a bounded plain-text snippet: C0/C1 control
# codes (including NUL and escape). Everything else — including `<`/`>`/`&` and
# Markdown markers — is preserved verbatim as INERT text.
_CONTROL_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})


@dataclass(frozen=True)
class _Query:
    """One normalized, bounded, parameter-safe query."""

    raw: str
    lower: str
    tokens: tuple[str, ...]
    patterns: tuple[str, ...]
    prefix_pattern: str
    # The canonical UUID this query IS, when the whole query is one. It is matched
    # against the canonical identity column only, inside the same authorization
    # filter, so TODO.md section 15's exact-UUID vector is real and still safe.
    uuid: UUID | None = None

    @property
    def is_postgres_rankable(self) -> bool:
        return bool(self.tokens)


@dataclass(frozen=True)
class _Candidate:
    """One authorized candidate row, already ordered inside SQL and bounded."""

    kind: SearchTargetKind
    target_id: UUID
    title: str
    fields: tuple[tuple[SearchMatchedField, str | None], ...]
    private: bool
    rank: int
    text_rank: float
    created_at: datetime | None
    status: DecisionStatus | None = None

    @property
    def sort_key(self) -> tuple[int, float, float, str, str]:
        return (
            self.rank,
            -self.text_rank,
            -self.created_at.timestamp() if self.created_at is not None else 0.0,
            self.kind.value,
            str(self.target_id),
        )


# ---------------------------------------------------------------------------
# Public application service
# ---------------------------------------------------------------------------


def search(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    *,
    query: str,
    scope: SearchScope = SearchScope.PROJECT_SHARED,
    target_kinds: list[SearchTargetKind] | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> ProjectSearchResultsRead:
    """Run one bounded, authorization-aware Project search.

    Fails closed (typed 422/403) for an over-bound query/limit/kind selection, an
    unknown kind, or a kind outside the requested scope; a non-member, a
    tombstoned Project, or an inactive Project fails closed with 403 before any
    corpus is queried.
    """
    readable_membership(session, actor_id, project_id)
    # Fail-closed backstop: a missing row cannot be reached through
    # `readable_membership`, but the guard keeps this projection self-contained.
    project = session.get(Project, project_id)
    if project is None or project.deleted_at is not None:
        raise AuthorizationError("project is not active")

    # Coerce the scope through its owning enum: a raw string that merely equals an
    # enum VALUE must never fall through to a wider corpus set.
    try:
        effective_scope = scope if isinstance(scope, SearchScope) else SearchScope(scope)
    except ValueError as exc:
        raise ValidationError(f"unknown search scope {scope!r}") from exc

    prepared = _prepare_query(query)
    bounded_limit = _bounded_limit(limit)
    kinds = _resolve_kinds(effective_scope, target_kinds)
    postgres = session.get_bind().dialect.name == "postgresql"

    candidates: list[_Candidate] = []
    # Each corpus contributes at most `limit + 1` authorized, SQL-ranked rows, so
    # a global top-N is always a subset of this bounded candidate set (and the +1
    # makes an "there were more" truncation detectable without a COUNT).
    per_corpus = bounded_limit + 1
    for kind in kinds:
        candidates.extend(
            _CORPORA[kind](session, actor_id, project_id, prepared, per_corpus, postgres)
        )

    candidates.sort(key=lambda candidate: candidate.sort_key)
    hits = [_to_hit(candidate, prepared) for candidate in candidates[:bounded_limit]]
    return ProjectSearchResultsRead(
        project_id=project_id,
        query=prepared.raw,
        scope=effective_scope,
        hits=hits,
        truncated=len(candidates) > bounded_limit,
    )


def search_project_shared(
    session: Session,
    actor_id: UUID,
    project_id: UUID,
    *,
    query: str,
    target_kinds: list[SearchTargetKind] | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> ProjectSearchResultsRead:
    """The Agent-facing entry point: the SAME service, fixed to PROJECT_SHARED.

    The private-conversation scope is not a parameter here, so an Agent can never
    request it — the boundary is structural, not a validated default.
    """
    return search(
        session,
        actor_id,
        project_id,
        query=query,
        scope=SearchScope.PROJECT_SHARED,
        target_kinds=target_kinds,
        limit=limit,
    )


class SearchService:
    """Small stateless façade naming the search application sub-boundary."""

    def search(
        self,
        session: Session,
        actor_id: UUID,
        project_id: UUID,
        *,
        query: str,
        scope: SearchScope = SearchScope.PROJECT_SHARED,
        target_kinds: list[SearchTargetKind] | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> ProjectSearchResultsRead:
        return search(
            session,
            actor_id,
            project_id,
            query=query,
            scope=scope,
            target_kinds=target_kinds,
            limit=limit,
        )


# ---------------------------------------------------------------------------
# Query normalization / bounds (fail closed, never widen)
# ---------------------------------------------------------------------------


def _prepare_query(query: str) -> _Query:
    if not isinstance(query, str):
        raise ValidationError("search query must be text")
    raw = query.strip()
    if not raw:
        raise ValidationError("search query must not be empty")
    if len(raw) > MAX_SEARCH_QUERY_CHARS:
        raise ValidationError(f"search query exceeds {MAX_SEARCH_QUERY_CHARS} characters")
    lower = raw.lower()
    tokens = tuple(_QUERY_SEPARATORS.sub(" ", lower).split())
    if not tokens:
        raise ValidationError("search query must contain at least one searchable character")
    if len(tokens) > MAX_SEARCH_TOKENS:
        raise ValidationError(f"search query exceeds {MAX_SEARCH_TOKENS} terms")
    if any(len(token) > MAX_SEARCH_TOKEN_CHARS for token in tokens):
        raise ValidationError(f"a search term exceeds {MAX_SEARCH_TOKEN_CHARS} characters")
    return _Query(
        raw=raw,
        lower=lower,
        tokens=tokens,
        patterns=tuple(_like_contains(token) for token in tokens),
        prefix_pattern=_like_prefix(lower),
        uuid=_uuid_probe(raw),
    )


def _uuid_probe(raw: str) -> UUID | None:
    """The canonical UUID this query IS, if the whole query is one.

    Hyphens/braces are accepted (canonical textual forms). An exact-UUID query is
    matched against the canonical identity column only, under the same corpus
    authorization filter — it never widens visibility.
    """
    compact = raw.strip().strip("{}").replace("-", "").lower()
    if len(compact) != 32 or any(character not in "0123456789abcdef" for character in compact):
        return None
    try:
        return UUID(compact)
    except ValueError:
        return None


# A token is a maximal run of Unicode word characters (letters/digits/underscore).
# Everything else is a separator: scientific identifiers such as `L72M/Q122A`,
# `8x3e`, `P12345` and `native-123` therefore stay searchable as plain terms, and
# no user input is ever interpreted as SQL, `tsquery`, regex, or glob syntax.
_QUERY_SEPARATORS = re.compile(r"[^\w]+", re.UNICODE)


def _escape_like(value: str) -> str:
    """Escape the LIKE metacharacters so user text is matched literally.

    `%`, `_`, and the escape character itself are neutralized; the query language
    therefore has no wildcards at all.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like_contains(token: str) -> str:
    return f"%{_escape_like(token)}%"


def _like_prefix(value: str) -> str:
    return f"{_escape_like(value)}%"


def _bounded_limit(limit: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValidationError("search limit must be an integer")
    if limit < 1 or limit > MAX_SEARCH_LIMIT:
        raise ValidationError(f"search limit must be between 1 and {MAX_SEARCH_LIMIT}")
    return limit


def _resolve_kinds(
    scope: SearchScope, target_kinds: list[SearchTargetKind] | None
) -> tuple[SearchTargetKind, ...]:
    allowed = _allowed_kinds(scope)
    if target_kinds is None:
        return tuple(kind for kind in SearchTargetKind if kind in allowed)
    if not target_kinds:
        raise ValidationError("at least one target kind must be requested")
    seen: list[SearchTargetKind] = []
    for raw_kind in target_kinds:
        try:
            kind = (
                raw_kind
                if isinstance(raw_kind, SearchTargetKind)
                else SearchTargetKind(raw_kind)
            )
        except ValueError as exc:
            raise ValidationError(f"unknown search target kind {raw_kind!r}") from exc
        if kind not in allowed:
            raise ValidationError(
                f"target kind {kind.value!r} is not available in this search scope"
            )
        if kind not in seen:
            seen.append(kind)
    if len(seen) > MAX_SEARCH_TARGET_KINDS:
        raise ValidationError(f"at most {MAX_SEARCH_TARGET_KINDS} target kinds may be requested")
    return tuple(seen)


def _allowed_kinds(scope: SearchScope) -> frozenset[SearchTargetKind]:
    """The closed kind set of one scope. Anything but the two named scopes fails
    closed — an unrecognized scope never widens to "all kinds"."""
    try:
        effective = scope if isinstance(scope, SearchScope) else SearchScope(scope)
    except ValueError as exc:
        raise ValidationError(f"unknown search scope {scope!r}") from exc
    if effective is SearchScope.PROJECT_SHARED:
        return PROJECT_SHARED_TARGET_KINDS
    if effective is SearchScope.MY_CONVERSATIONS:
        return MY_CONVERSATION_TARGET_KINDS
    if effective is SearchScope.ALL:
        return frozenset(SearchTargetKind)
    raise ValidationError(f"unknown search scope {scope!r}")


# ---------------------------------------------------------------------------
# Shared SQL building blocks
# ---------------------------------------------------------------------------


def _text_expr(*parts: Any) -> ColumnElement[Any]:
    """`coalesce(a,'') || ' ' || coalesce(b,'') ...` on both backends.

    SQLAlchemy renders `+` on string expressions as `||`, which PostgreSQL and
    SQLite both support, so one predicate serves both substrates.
    """
    exprs: list[ColumnElement[Any]] = [
        func.coalesce(cast(part, String), literal("")) for part in parts
    ]
    combined: ColumnElement[Any] = exprs[0]
    for part in exprs[1:]:
        combined = combined + literal(" ") + part
    return combined


def _lower_text(*parts: Any) -> ColumnElement[Any]:
    return func.lower(_text_expr(*parts))


def _text_rank(
    postgres: bool, raw_query: str, text_expr: Any
) -> ColumnElement[Any]:
    """PostgreSQL native text-search ordering signal; a constant on SQLite.

    Configuration `simple` is used deliberately: it is language-neutral, so an
    identifier such as `P12345` is never stemmed into a different term. This is
    an ORDERING AID only — it is never exposed and never a property of a hit.
    """
    if not postgres:
        return literal(0.0)
    return func.ts_rank(
        func.to_tsvector(literal_column("'simple'"), text_expr),
        func.plainto_tsquery(literal_column("'simple'"), raw_query),
    )


def _match_all(
    query: _Query, text_lower: ColumnElement[Any], identity_column: Any = None
) -> ColumnElement[Any]:
    """Every query term must appear as a case-insensitive substring.

    AND across terms, substring within the row: this keeps multi-term queries
    meaningful and keeps identifiers exact rather than stemmed. An exact canonical
    UUID is OR'd onto the lexical match, still inside the corpus authorization
    filter, so it is never an existence oracle.

    Case folding is `lower()` on the DATABASE: PostgreSQL folds per the database
    locale (so `CAFÉ` matches `café`) while SQLite folds ASCII only. Authorization,
    target classes and bounds are identical on both substrates; only non-ASCII case
    folding differs (an accepted SQLite limitation, TODO.md section 13).
    """
    base = and_(*[text_lower.like(pattern, escape="\\") for pattern in query.patterns])
    return _uuid_or(query, identity_column, base)


def _uuid_or(
    query: _Query, identity_column: Any, base: ColumnElement[Any]
) -> ColumnElement[Any]:
    if identity_column is None or query.uuid is None:
        return base
    return or_(base, identity_column == query.uuid)


def _exists_like(
    pattern: str,
    table: Any,
    *,
    own: Any,
    columns: tuple[ColumnElement[Any], ...],
    join: Any = None,
) -> ColumnElement[Any]:
    """One bounded EXISTS over an auxiliary table (external identities, aliases).

    Kept in SQL so an identifier match never requires loading the Project into
    Python.
    """
    statement = select(literal(1)).select_from(table)
    if join is not None:
        statement = statement.join(join)
    statement = statement.where(own)
    return exists(statement.where(or_(*[column.like(pattern, escape="\\") for column in columns])))


def _rank_case(
    query: _Query,
    title_lower: ColumnElement[Any],
    identity_lowers: tuple[ColumnElement[Any], ...] = (),
    identity_exact: Any = None,
    identity_column: Any = None,
) -> ColumnElement[Any]:
    """A small, documented, non-ML ordering key.

    0 = exact canonical UUID / external identifier match
    1 = exact title/name match
    2 = title/name prefix match
    3 = other authorized full-text/lexical match
    """
    conditions: list[tuple[Any, int]] = []
    if identity_column is not None and query.uuid is not None:
        conditions.append((identity_column == query.uuid, 0))
    if identity_exact is not None:
        conditions.append((identity_exact, 0))
    conditions.extend((identity == query.lower, 0) for identity in identity_lowers)
    conditions.append((title_lower == query.lower, 1))
    conditions.append((title_lower.like(query.prefix_pattern, escape="\\"), 2))
    return case(*conditions, else_=3)


def _rows(
    session: Session, statement: Any, limit: int
) -> list[Any]:
    return list(session.execute(statement.limit(limit)))


def _ordered(
    statement: Any,
    rank: ColumnElement[Any],
    text_rank: ColumnElement[Any],
    tie: Any,
    identity: Any,
) -> Any:
    """Deterministic per-corpus ordering: the canonical identity is the FINAL
    tie-break, so the SQL `LIMIT`ed top-N is reproducible (the application merge
    key uses the same last resort)."""
    return statement.order_by(rank.asc(), text_rank.desc(), tie.desc(), identity.asc())


def _created(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else None


# ---------------------------------------------------------------------------
# Corpora — PROJECT_SHARED
# ---------------------------------------------------------------------------


def _series_corpus(
    session: Session, actor_id: UUID, project_id: UUID, query: _Query, limit: int, postgres: bool
) -> list[_Candidate]:
    series = ScientificObjectSeries
    link = ProjectResourceLink
    alias = ScientificObjectAlias
    ext = ScientificObjectExternalIdentity
    identity = ExternalIdentity

    text_lower = _lower_text(series.name, series.description, cast(series.object_type, String))
    name_lower = func.lower(series.name)
    ext_join = identity.external_identity_id == ext.external_identity_id
    ext_own = ext.series_id == series.series_id

    def ext_match(pattern: str) -> ColumnElement[Any]:
        return _exists_like(
            pattern,
            ext,
            join=identity,
            own=ext_own,
            columns=(
                func.lower(identity.native_id),
                func.lower(identity.authority),
            ),
        )

    def alias_match(pattern: str) -> ColumnElement[Any]:
        return _exists_like(
            pattern,
            alias,
            own=alias.series_id == series.series_id,
            columns=(func.lower(alias.alias),),
        )

    match = and_(
        *[
            or_(text_lower.like(pattern, escape="\\"), ext_match(pattern), alias_match(pattern))
            for pattern in query.patterns
        ]
    )
    match = _uuid_or(query, series.series_id, match)
    ext_exact = exists(
        select(literal(1))
        .select_from(ext)
        .join(identity, ext_join)
        .where(ext_own)
        .where(func.lower(identity.native_id) == query.lower)
    )
    rank = _rank_case(
        query, name_lower, identity_exact=ext_exact, identity_column=series.series_id
    )
    text_rank = _text_rank(postgres, query.raw, _text_expr(series.name, series.description))
    statement = _ordered(
        select(
            series.series_id,
            series.name,
            series.description,
            cast(series.object_type, String),
            rank.label("rank"),
            text_rank.label("text_rank"),
            series.created_at,
        )
        .join(link, link.resource_id == series.series_id)
        .where(link.project_id == project_id, series.archived_at.is_(None), match),
        rank,
        text_rank,
        series.created_at,
        series.series_id,
    )
    return [
        _Candidate(
            kind=SearchTargetKind.SCIENTIFIC_OBJECT_SERIES,
            target_id=row[0],
            title=_plain(row[1], _TITLE_CHARS) or f"Object {str(row[0])[:8]}",
            fields=(
                (SearchMatchedField.NAME, row[1]),
                (SearchMatchedField.DESCRIPTION, row[2]),
                (SearchMatchedField.TYPE, row[3]),
            ),
            private=False,
            rank=row[4],
            text_rank=float(row[5] or 0.0),
            created_at=_created(row[6]),
        )
        for row in _rows(session, statement, limit)
    ]


def _evidence_corpus(
    session: Session, actor_id: UUID, project_id: UUID, query: _Query, limit: int, postgres: bool
) -> list[_Candidate]:
    evidence = Evidence
    text_lower = _lower_text(evidence.label, evidence.interpretation, evidence.scope)
    label_lower = func.lower(func.coalesce(evidence.label, evidence.interpretation, evidence.kind))
    rank = _rank_case(query, label_lower, identity_column=evidence.id)
    text_rank = _text_rank(
        postgres, query.raw, _text_expr(evidence.label, evidence.interpretation, evidence.scope)
    )
    statement = _ordered(
        select(
            evidence.id,
            evidence.label,
            evidence.interpretation,
            evidence.scope,
            evidence.kind,
            rank.label("rank"),
            text_rank.label("text_rank"),
            evidence.created_at,
        )
        .where(
            evidence.project_id == project_id,
            evidence.archived_at.is_(None),
            _match_all(query, text_lower, evidence.id),
        ),
        rank,
        text_rank,
        evidence.created_at,
        evidence.id,
    )
    candidates: list[_Candidate] = []
    for row in _rows(session, statement, limit):
        title = _plain(row[1], _TITLE_CHARS) or _plain(row[2], _TITLE_CHARS) or f"{row[4]} evidence"
        candidates.append(
            _Candidate(
                kind=SearchTargetKind.EVIDENCE,
                target_id=row[0],
                title=title,
                fields=(
                    (SearchMatchedField.LABEL, row[1]),
                    (SearchMatchedField.INTERPRETATION, row[2]),
                    (SearchMatchedField.SCOPE, row[3]),
                ),
                private=False,
                rank=row[5],
                text_rank=float(row[6] or 0.0),
                created_at=_created(row[7]),
            )
        )
    return candidates


def _decision_corpus(
    session: Session, actor_id: UUID, project_id: UUID, query: _Query, limit: int, postgres: bool
) -> list[_Candidate]:
    decision = Decision
    next_actions = cast(decision.next_actions, String)
    text_lower = _lower_text(decision.title, decision.statement, next_actions)
    title_lower = func.lower(decision.title)
    rank = _rank_case(query, title_lower, identity_column=decision.id)
    text_rank = _text_rank(
        postgres, query.raw, _text_expr(decision.title, decision.statement, next_actions)
    )
    statement = _ordered(
        select(
            decision.id,
            decision.title,
            decision.statement,
            next_actions,
            decision.status,
            rank.label("rank"),
            text_rank.label("text_rank"),
            decision.created_at,
        )
        .where(
            decision.project_id == project_id,
            decision.archived_at.is_(None),
            _match_all(query, text_lower, decision.id),
        ),
        rank,
        text_rank,
        decision.created_at,
        decision.id,
    )
    return [
        _Candidate(
            kind=SearchTargetKind.DECISION,
            target_id=row[0],
            title=_plain(row[1], _TITLE_CHARS) or f"Decision {str(row[0])[:8]}",
            fields=(
                (SearchMatchedField.TITLE, row[1]),
                (SearchMatchedField.STATEMENT, row[2]),
                (SearchMatchedField.NEXT_ACTION, row[3]),
            ),
            private=False,
            rank=row[5],
            text_rank=float(row[6] or 0.0),
            created_at=_created(row[7]),
            status=DecisionStatus(row[4]),
        )
        for row in _rows(session, statement, limit)
    ]


def _note_corpus(
    session: Session, actor_id: UUID, project_id: UUID, query: _Query, limit: int, postgres: bool
) -> list[_Candidate]:
    note = ProjectNote
    revision = ProjectNoteRevision
    latest_seq = (
        select(func.max(revision.revision_seq))
        .where(revision.note_id == note.id)
        .correlate(note)
        .scalar_subquery()
    )
    text_lower = _lower_text(note.title, revision.body)
    title_lower = func.lower(note.title)
    rank = _rank_case(query, title_lower, identity_column=note.id)
    text_rank = _text_rank(postgres, query.raw, _text_expr(note.title, revision.body))
    statement = _ordered(
        select(
            note.id,
            note.title,
            revision.body,
            rank.label("rank"),
            text_rank.label("text_rank"),
            note.updated_at,
        )
        .join(
            revision,
            and_(revision.note_id == note.id, revision.revision_seq == latest_seq),
        )
        .where(
            note.project_id == project_id,
            note.archived_at.is_(None),
            _match_all(query, text_lower, note.id),
        ),
        rank,
        text_rank,
        note.updated_at,
        note.id,
    )
    return [
        _Candidate(
            kind=SearchTargetKind.NOTE,
            target_id=row[0],
            title=_plain(row[1], _TITLE_CHARS) or f"Note {str(row[0])[:8]}",
            fields=(
                (SearchMatchedField.TITLE, row[1]),
                # Only the LATEST revision body is ever read: a superseded
                # revision can never make a Note look current.
                (SearchMatchedField.BODY, row[2]),
            ),
            private=False,
            rank=row[3],
            text_rank=float(row[4] or 0.0),
            created_at=_created(row[5]),
        )
        for row in _rows(session, statement, limit)
    ]


def _run_corpus(
    session: Session, actor_id: UUID, project_id: UUID, query: _Query, limit: int, postgres: bool
) -> list[_Candidate]:
    run = RunReference
    link = ProjectResourceLink
    compound = run.authority + literal(":") + run.native_id
    text_lower = _lower_text(run.authority, run.native_id, run.task_type)
    identity_lowers = (func.lower(run.native_id), func.lower(compound))
    title_lower = func.lower(compound)
    rank = _rank_case(query, title_lower, identity_lowers, identity_column=run.run_id)
    text_rank = _text_rank(postgres, query.raw, _text_expr(run.authority, run.native_id, run.task_type))
    statement = _ordered(
        select(
            run.run_id,
            run.authority,
            run.native_id,
            run.task_type,
            rank.label("rank"),
            text_rank.label("text_rank"),
            run.created_at,
        )
        .join(link, link.resource_id == run.run_id)
        .where(
            link.project_id == project_id,
            run.revoked_at.is_(None),
            _match_all(query, text_lower, run.run_id),
        ),
        rank,
        text_rank,
        run.created_at,
        run.run_id,
    )
    return [
        _Candidate(
            kind=SearchTargetKind.RUN_REFERENCE,
            target_id=row[0],
            title=_plain(f"{row[1]}:{row[2]}", _TITLE_CHARS) or f"Run {str(row[0])[:8]}",
            fields=(
                (SearchMatchedField.IDENTIFIER, f"{row[1]}:{row[2]}"),
                (SearchMatchedField.TYPE, row[3]),
            ),
            private=False,
            rank=row[4],
            text_rank=float(row[5] or 0.0),
            created_at=_created(row[6]),
        )
        for row in _rows(session, statement, limit)
    ]


def _artifact_corpus(
    session: Session, actor_id: UUID, project_id: UUID, query: _Query, limit: int, postgres: bool
) -> list[_Candidate]:
    artifact = ArtifactReference
    link = ProjectResourceLink
    compound = artifact.authority + literal(":") + artifact.native_id
    text_lower = _lower_text(
        artifact.authority,
        artifact.native_id,
        artifact.checksum,
        artifact.content_type,
        artifact.version_id,
    )
    identity_lowers = (
        func.lower(artifact.native_id),
        func.lower(compound),
        func.lower(artifact.checksum),
    )
    title_lower = func.lower(compound)
    rank = _rank_case(query, title_lower, identity_lowers, identity_column=artifact.artifact_id)
    text_rank = _text_rank(
        postgres, query.raw, _text_expr(artifact.authority, artifact.native_id, artifact.content_type)
    )
    statement = _ordered(
        select(
            artifact.artifact_id,
            artifact.authority,
            artifact.native_id,
            artifact.checksum,
            artifact.content_type,
            artifact.version_id,
            rank.label("rank"),
            text_rank.label("text_rank"),
            artifact.created_at,
        )
        .join(link, link.resource_id == artifact.artifact_id)
        .where(
            link.project_id == project_id,
            artifact.revoked_at.is_(None),
            _match_all(query, text_lower, artifact.artifact_id),
        ),
        rank,
        text_rank,
        artifact.created_at,
        artifact.artifact_id,
    )
    candidates: list[_Candidate] = []
    for row in _rows(session, statement, limit):
        label = f"{row[1]}:{row[2]}" + (f"@{row[5]}" if row[5] else "")
        candidates.append(
            _Candidate(
                kind=SearchTargetKind.ARTIFACT_REFERENCE,
                target_id=row[0],
                title=_plain(label, _TITLE_CHARS) or f"Artifact {str(row[0])[:8]}",
                fields=(
                    (SearchMatchedField.IDENTIFIER, label),
                    (SearchMatchedField.CHECKSUM, row[3]),
                    (SearchMatchedField.TYPE, row[4]),
                ),
                private=False,
                rank=row[6],
                text_rank=float(row[7] or 0.0),
                created_at=_created(row[8]),
            )
        )
    return candidates


def _literature_corpus(
    session: Session, actor_id: UUID, project_id: UUID, query: _Query, limit: int, postgres: bool
) -> list[_Candidate]:
    literature = LiteratureReference
    link = ProjectResourceLink
    compound = literature.authority + literal(":") + literature.native_id
    text_lower = _lower_text(literature.authority, literature.native_id, literature.title)
    identity_lowers = (func.lower(literature.native_id), func.lower(compound))
    title_lower = func.lower(func.coalesce(literature.title, compound))
    rank = _rank_case(query, title_lower, identity_lowers, identity_column=literature.literature_id)
    text_rank = _text_rank(
        postgres, query.raw, _text_expr(literature.authority, literature.native_id, literature.title)
    )
    statement = _ordered(
        select(
            literature.literature_id,
            literature.authority,
            literature.native_id,
            literature.title,
            rank.label("rank"),
            text_rank.label("text_rank"),
            literature.created_at,
        )
        .join(link, link.resource_id == literature.literature_id)
        .where(link.project_id == project_id, _match_all(query, text_lower, literature.literature_id)),
        rank,
        text_rank,
        literature.created_at,
        literature.literature_id,
    )
    return [
        _Candidate(
            kind=SearchTargetKind.LITERATURE_REFERENCE,
            target_id=row[0],
            title=_plain(row[3], _TITLE_CHARS) or f"{row[1]}:{row[2]}",
            fields=(
                (SearchMatchedField.TITLE, row[3]),
                (SearchMatchedField.IDENTIFIER, f"{row[1]}:{row[2]}"),
            ),
            private=False,
            rank=row[4],
            text_rank=float(row[5] or 0.0),
            created_at=_created(row[6]),
        )
        for row in _rows(session, statement, limit)
    ]


def _external_corpus(
    session: Session, actor_id: UUID, project_id: UUID, query: _Query, limit: int, postgres: bool
) -> list[_Candidate]:
    external = ExternalReference
    identity = ExternalIdentity
    link = ProjectResourceLink
    compound = identity.authority + literal(":") + identity.native_id
    text_lower = _lower_text(identity.authority, identity.native_id, external.checksum)
    identity_lowers = (func.lower(identity.native_id), func.lower(compound))
    rank = _rank_case(query, func.lower(compound), identity_lowers, identity_column=external.external_reference_id)
    text_rank = _text_rank(
        postgres, query.raw, _text_expr(identity.authority, identity.native_id, external.checksum)
    )
    statement = _ordered(
        select(
            external.external_reference_id,
            identity.authority,
            identity.native_id,
            external.checksum,
            rank.label("rank"),
            text_rank.label("text_rank"),
            external.created_at,
        )
        .select_from(external)
        .join(identity, identity.external_identity_id == external.external_identity_id)
        .join(link, link.resource_id == external.external_reference_id)
        .where(link.project_id == project_id, _match_all(query, text_lower, external.external_reference_id)),
        rank,
        text_rank,
        external.created_at,
        external.external_reference_id,
    )
    return [
        _Candidate(
            kind=SearchTargetKind.EXTERNAL_REFERENCE,
            target_id=row[0],
            title=_plain(f"{row[1]}:{row[2]}", _TITLE_CHARS) or f"Reference {str(row[0])[:8]}",
            fields=(
                (SearchMatchedField.IDENTIFIER, f"{row[1]}:{row[2]}"),
                (SearchMatchedField.CHECKSUM, row[3]),
            ),
            private=False,
            rank=row[4],
            text_rank=float(row[5] or 0.0),
            created_at=_created(row[6]),
        )
        for row in _rows(session, statement, limit)
    ]


# ---------------------------------------------------------------------------
# Corpus — Actor-private working memory (never Project-shared, never Agent)
# ---------------------------------------------------------------------------


def _conversation_corpus(
    session: Session, actor_id: UUID, project_id: UUID, query: _Query, limit: int, postgres: bool
) -> list[_Candidate]:
    conversation = ProjectConversation
    message = ConversationMessage
    title_lower = func.lower(conversation.title)
    content_lower = func.lower(message.content)
    message_match = and_(*[content_lower.like(pattern, escape="\\") for pattern in query.patterns])
    match = and_(
        *[
            or_(
                title_lower.like(pattern, escape="\\"),
                exists(
                    select(literal(1))
                    .select_from(message)
                    .where(message.conversation_id == conversation.id)
                    .where(content_lower.like(pattern, escape="\\"))
                ),
            )
            for pattern in query.patterns
        ]
    )
    match = _uuid_or(query, conversation.id, match)
    first_message: ScalarSelect[Any] = (
        select(message.content)
        .where(message.conversation_id == conversation.id)
        .where(message_match)
        .order_by(message.seq)
        .limit(1)
        .scalar_subquery()
    )
    rank = _rank_case(query, title_lower, identity_column=conversation.id)
    text_rank = _text_rank(postgres, query.raw, _text_expr(conversation.title))
    statement = _ordered(
        select(
            conversation.id,
            conversation.title,
            first_message.label("message"),
            rank.label("rank"),
            text_rank.label("text_rank"),
            conversation.updated_at,
        )
        .where(
            conversation.project_id == project_id,
            conversation.actor_id == actor_id,
            conversation.archived_at.is_(None),
            match,
        ),
        rank,
        text_rank,
        conversation.updated_at,
        conversation.id,
    )
    return [
        _Candidate(
            kind=SearchTargetKind.CONVERSATION,
            target_id=row[0],
            title=_plain(row[1], _TITLE_CHARS) or "Untitled conversation",
            fields=(
                (SearchMatchedField.TITLE, row[1]),
                (SearchMatchedField.MESSAGE, row[2]),
            ),
            # Private by construction: this corpus is Actor x Project scoped and
            # never enters the Agent-facing tool.
            private=True,
            rank=row[3],
            text_rank=float(row[4] or 0.0),
            created_at=_created(row[5]),
        )
        for row in _rows(session, statement, limit)
    ]


_CORPORA: dict[SearchTargetKind, Any] = {
    SearchTargetKind.SCIENTIFIC_OBJECT_SERIES: _series_corpus,
    SearchTargetKind.EVIDENCE: _evidence_corpus,
    SearchTargetKind.DECISION: _decision_corpus,
    SearchTargetKind.NOTE: _note_corpus,
    SearchTargetKind.RUN_REFERENCE: _run_corpus,
    SearchTargetKind.ARTIFACT_REFERENCE: _artifact_corpus,
    SearchTargetKind.LITERATURE_REFERENCE: _literature_corpus,
    SearchTargetKind.EXTERNAL_REFERENCE: _external_corpus,
    SearchTargetKind.CONVERSATION: _conversation_corpus,
}


# ---------------------------------------------------------------------------
# Presentation: bounded plain-text title/snippet (never HTML, never active)
# ---------------------------------------------------------------------------


def _plain(value: str | None, limit: int) -> str:
    if not value:
        return ""
    flattened = " ".join(str(value).split())
    printable = "".join(
        character
        for character in flattened
        if unicodedata.category(character) not in _CONTROL_CATEGORIES
    )
    if len(printable) <= limit:
        return printable
    return printable[: max(limit - 1, 0)] + "\u2026"


def _snippet(candidate: _Candidate, query: _Query) -> str | None:
    """A bounded plain-text preview around the first matching term.

    Extraction happens in the application, NOT via `ts_headline`: no
    database-generated markup can ever reach a renderer, so a hostile stored
    Markdown/HTML body stays inert text in the snippet.
    """
    for _, value in candidate.fields:
        if value and any(token in value.lower() for token in query.tokens):
            return _plain(value, MAX_SEARCH_SNIPPET_CHARS)
    for _, value in candidate.fields:
        if value:
            return _plain(value, MAX_SEARCH_SNIPPET_CHARS)
    return None


def _matched_field(candidate: _Candidate, query: _Query) -> SearchMatchedField | None:
    for field, value in candidate.fields:
        if value and any(token in value.lower() for token in query.tokens):
            return field
    return None


def _to_hit(candidate: _Candidate, query: _Query) -> SearchHitRead:
    return SearchHitRead(
        target_kind=candidate.kind,
        target_id=candidate.target_id,
        title=_plain(candidate.title, _TITLE_CHARS) or str(candidate.target_id),
        snippet=_snippet(candidate, query),
        matched_field=_matched_field(candidate, query),
        private=candidate.private,
        status=candidate.status,
    )


__all__ = ["SearchService", "search", "search_project_shared"]
