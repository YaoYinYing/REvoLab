"""External literature discovery + explicit import (Phase 13) application service.

This is an application/query + command sub-boundary in the same placement family
as `revolab.search` and `revolab.notes`: it composes existing public contracts and
adds no Core domain. Both the human Literature workspace and the Agent-facing
read-only literature Tool call the SAME service — there is deliberately no
frontend-specific and no Agent-specific provider path (TODO.md section 16).

The governing boundary:

    External candidate != Project truth.
    LiteratureReference != Evidence.
    Provider/resolver != durable identity authority.

`discover_literature` is a read: it authorizes the CURRENT Project read access,
resolves the CURRENT provider/capability availability through the shared
invocation gate, invokes `LiteratureDiscoveryCapability`, re-bounds the result, and
returns a typed candidate projection. It performs **zero** durable writes.

`import_literature` is the explicit human command: current Project MUTATION
authority, then a CURRENT provider re-resolution of the stable
`(authority, native_id)` identity, then the canonical global LiteratureReference
get-or-create plus its `ProjectResourceLink`. It never trusts browser/model
bibliographic metadata and it never creates Evidence.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from revolab import services
from revolab.capabilities import (
    DEFAULT_LITERATURE_RESULT_LIMIT,
    MAX_LITERATURE_AUTHOR_CHARS,
    MAX_LITERATURE_AUTHORS,
    MAX_LITERATURE_DOI_CHARS,
    MAX_LITERATURE_JOURNAL_CHARS,
    MAX_LITERATURE_QUERY_CHARS,
    MAX_LITERATURE_RESULT_LIMIT,
    MAX_LITERATURE_TITLE_CHARS,
    LiteratureCandidate,
)
from revolab.domain import discovery as discovery_domain
from revolab.domain.errors import ValidationError
from revolab.domain.identity import (
    mutation_capable_membership,
    readable_membership,
    require_active_project,
)
from revolab.drivers import DriverRegistry
from revolab.enums import CapabilityKind
from revolab.schemas import LiteratureCandidateRead, LiteratureDiscoveryResultsRead
from revolab.secret_store import SecretStore

# The persisted identity columns are bounded by the canonical schema; these mirror
# those bounds so an oversized/absurd identity fails closed at the service
# boundary instead of at the database.
_MAX_AUTHORITY_CHARS = 100
_MAX_NATIVE_ID_CHARS = 300


def discover_literature(
    session: Session,
    registry: DriverRegistry,
    secret_store: SecretStore,
    actor_id: UUID,
    project_id: UUID,
    *,
    provider_key: str,
    query: str,
    limit: int = DEFAULT_LITERATURE_RESULT_LIMIT,
) -> LiteratureDiscoveryResultsRead:
    """Run one bounded, authorization-aware external literature search.

    Read-only: a viewer may discover literature when ordinary read policy permits
    (the capability kind is a read-only kind). Fails closed for a non-member, a
    tombstoned Project, an over-bound query/limit, an unknown/unavailable provider,
    or a typed provider failure. Nothing is persisted — a page reload may require
    performing the search again, which is acceptable and deliberate.
    """
    readable_membership(session, actor_id, project_id)
    _require_active_project(session, project_id)
    bounded_query = _bounded_query(query)
    bounded_limit = _bounded_limit(limit)
    result = discovery_domain.search_literature(
        session,
        registry,
        secret_store,
        actor_id,
        provider_key,
        bounded_query,
        bounded_limit,
        permitted=services.project_policy_permits(
            session, actor_id, project_id, CapabilityKind.LITERATURE_DISCOVERY
        ),
    )
    candidates = []
    for candidate in result.candidates[:bounded_limit]:
        if candidate.provider_key != provider_key:
            # A candidate attributed to a different provider is structurally
            # impossible: fail closed rather than silently degrade to a short list.
            raise ValidationError("provider returned a candidate from a different provider")
        candidates.append(_candidate_read(candidate))
    return LiteratureDiscoveryResultsRead(
        provider_key=provider_key,
        query=bounded_query,
        candidates=candidates,
    )


def import_literature(
    session: Session,
    registry: DriverRegistry,
    secret_store: SecretStore,
    actor_id: UUID,
    project_id: UUID,
    *,
    provider_key: str,
    authority: str,
    native_id: str,
) -> Any:
    """Explicitly import ONE publication into the Project context.

    Flow: current Project mutation authority -> current provider availability ->
    `resolve(authority, native_id)` -> verify the returned durable identity matches
    the request -> canonical global LiteratureReference get-or-create ->
    `ProjectResourceLink`. Import does NOT create Evidence, a Decision, a Note, an
    ExternalReference, or a ScientificObject.

    Atomicity: a remote read has no external side effect, so ordinary local
    transaction rollback is sufficient — if resolve fails there is no reference and
    no link.
    """
    mutation_capable_membership(session, actor_id, project_id)
    _require_active_project(session, project_id)
    bounded_authority = _bounded_identity(authority, _MAX_AUTHORITY_CHARS, "authority")
    bounded_native_id = _bounded_identity(native_id, _MAX_NATIVE_ID_CHARS, "native_id")
    candidate = discovery_domain.resolve_literature(
        session,
        registry,
        secret_store,
        actor_id,
        provider_key,
        bounded_authority,
        bounded_native_id,
        permitted=services.project_policy_permits(
            session, actor_id, project_id, CapabilityKind.LITERATURE_DISCOVERY
        ),
    )
    # The provider must independently confirm the EXACT durable identity that was
    # requested; a mismatched or substituted record fails closed before any write.
    if candidate.provider_key != provider_key:
        raise ValidationError("resolved publication came from a different provider")
    if candidate.authority != bounded_authority or candidate.native_id != bounded_native_id:
        raise ValidationError("resolved publication identity does not match the import request")
    return services._persist_literature_reference_from_resolver(
        session,
        actor_id,
        project_id,
        bounded_authority,
        bounded_native_id,
        title=candidate.title,
    )


# ---------------------------------------------------------------------------
# Bounds / projection
# ---------------------------------------------------------------------------


def _require_active_project(session: Session, project_id: UUID) -> None:
    # `readable_membership`/`mutation_capable_membership` already guard this; the
    # shared backstop is kept as defense-in-depth so a tombstoned Project can never
    # be written through this boundary.
    require_active_project(session, project_id)


def _bounded_query(query: str) -> str:
    if not isinstance(query, str):
        raise ValidationError("literature search query must be text")
    text = query.strip()
    if not text or len(text) > MAX_LITERATURE_QUERY_CHARS:
        raise ValidationError(
            f"literature search query must be 1..{MAX_LITERATURE_QUERY_CHARS} characters"
        )
    return text


def _bounded_limit(limit: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValidationError("literature result limit must be a positive integer")
    if limit > MAX_LITERATURE_RESULT_LIMIT:
        raise ValidationError(
            f"literature result limit must not exceed {MAX_LITERATURE_RESULT_LIMIT}"
        )
    return limit


def _bounded_identity(value: str, limit: int, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"literature {field} must be text")
    text = value.strip()
    if not text or len(text) > limit:
        raise ValidationError(f"literature {field} must be 1..{limit} characters")
    return text


def _bounded(value: str | None, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:limit] if text else None


def _candidate_read(candidate: LiteratureCandidate) -> LiteratureCandidateRead:
    """Re-bound a driver-returned candidate into the typed wire projection.

    The driver already applies these limits; re-applying them here is
    defense-in-depth so a mis-wired driver cannot widen the frontend/Agent surface.
    Truncation is honest bounded presentation data; structurally invalid data has
    already failed closed inside the driver.
    """
    authors = [
        bounded
        for bounded in (
            _bounded(author, MAX_LITERATURE_AUTHOR_CHARS)
            for author in candidate.authors[:MAX_LITERATURE_AUTHORS]
        )
        if bounded is not None
    ]
    year = candidate.publication_year
    if not isinstance(year, int) or isinstance(year, bool) or not (1000 <= year <= 2999):
        year = None
    return LiteratureCandidateRead(
        provider_key=candidate.provider_key,
        authority=_bounded(candidate.authority, _MAX_AUTHORITY_CHARS) or "",
        native_id=_bounded(candidate.native_id, _MAX_NATIVE_ID_CHARS) or "",
        title=_bounded(candidate.title, MAX_LITERATURE_TITLE_CHARS),
        authors=authors,
        journal=_bounded(candidate.journal, MAX_LITERATURE_JOURNAL_CHARS),
        publication_year=year,
        doi=_bounded(candidate.doi, MAX_LITERATURE_DOI_CHARS),
    )


__all__ = ["discover_literature", "import_literature"]
