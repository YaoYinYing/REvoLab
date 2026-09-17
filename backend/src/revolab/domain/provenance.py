"""Evidence / Provenance domain operations (identity-free).

Global provenance edges, reference identity cards, the import command, and
Evidence claims. Authority and membership are validated at the command boundary
(`revolab.services`) before these entry points are reached; this module enforces
only the structural + semantic invariants (endpoint kinds, visibility of the
target/input through the acting Project, immutability) and never derives or
re-derives mutation authority.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, NamedTuple, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from revolab.domain import persistence, scientific_object
from revolab.domain.errors import ConflictError, NotFoundError, ValidationError
from revolab.domain.grants import MutationGrant
from revolab.enums import (
    LEGAL_EVIDENCE_SOURCE_KINDS,
    DecisionStatus,
    EvidenceKind,
    EvidenceTargetKind,
    RelationType,
    ResourceKind,
)
from revolab.models import (
    ArtifactReference,
    Decision,
    DecisionEvidence,
    Evidence,
    ExternalReference,
    GlobalProvenanceEdge,
    GlobalResourceRegistry,
    LiteratureReference,
    RunReference,
    ScientificObjectRevision,
    SessionReference,
)

# ---------------------------------------------------------------------------
# Reference identity cards (rows only; links/stewardship at the boundary)
# ---------------------------------------------------------------------------


def create_run_reference_row(
    session: Session,
    authority: str,
    native_id: str,
    *,
    task_type: str | None,
    input_parameter_digest: str | None,
    submitted_at: datetime | None,
) -> RunReference:
    resource_id = persistence.new_id()
    persistence.register(session, resource_id, ResourceKind.RUN_REFERENCE)
    session.flush()
    row = RunReference(
        run_id=resource_id,
        authority=authority,
        native_id=native_id,
        task_type=task_type,
        input_parameter_digest=input_parameter_digest,
        submitted_at=submitted_at,
    )
    session.add(row)
    session.flush()
    return row


def create_session_reference_row(
    session: Session, authority: str, native_id: str
) -> SessionReference:
    resource_id = persistence.new_id()
    persistence.register(session, resource_id, ResourceKind.SESSION_REFERENCE)
    session.flush()
    row = SessionReference(session_id=resource_id, authority=authority, native_id=native_id)
    session.add(row)
    session.flush()
    return row


def create_artifact_reference_row(
    session: Session,
    authority: str,
    native_id: str,
    *,
    content_type: str | None,
    size: int | None,
    checksum: str | None,
    version_id: str = "",
) -> ArtifactReference:
    resource_id = persistence.new_id()
    persistence.register(session, resource_id, ResourceKind.ARTIFACT_REFERENCE)
    session.flush()
    row = ArtifactReference(
        artifact_id=resource_id,
        authority=authority,
        native_id=native_id,
        content_type=content_type,
        size=size,
        checksum=checksum,
        version_id=version_id,
    )
    session.add(row)
    session.flush()
    return row


def create_artifact_reference_row_if_absent(
    session: Session,
    authority: str,
    native_id: str,
    *,
    content_type: str | None,
    size: int | None,
    checksum: str | None,
    version_id: str = "",
) -> ArtifactReference | None:
    """Atomically INSERT the artifact identity row, or return None if it exists.

    A plain read-then-insert is not race-safe: two concurrent creators of the SAME
    `(authority, native_id, version_id)` can both observe "not found". Catching the
    resulting `IntegrityError` is NOT a portable recovery either — it aborts the
    whole PostgreSQL transaction, which would destroy unrelated work for a caller
    that composed the insert with `commit=False` (the Local Tool Runtime's derived
    result, or a scientific import bundle), and the `Session.begin_nested()`
    savepoint shim is not reliable on every substrate.

    The insert is therefore expressed as ONE dialect-level "insert if absent"
    statement, so a concurrent creator can never fail here and the caller's
    transaction stays intact and uncommitted. The winner's row is left exactly as
    it was: this never overwrites an existing immutable assertion.
    """
    resource_id = persistence.new_id()
    registry = GlobalResourceRegistry(
        resource_id=resource_id, resource_kind=ResourceKind.ARTIFACT_REFERENCE.value
    )
    session.add(registry)
    session.flush()
    values: dict[str, Any] = {
        "artifact_id": resource_id,
        "authority": authority,
        "native_id": native_id,
        "content_type": content_type,
        "size": size,
        "checksum": checksum,
        "version_id": version_id,
    }
    identity_columns = ["authority", "native_id", "version_id"]
    dialect = session.get_bind().dialect.name
    statement: Any
    if dialect == "postgresql":
        statement = (
            pg_insert(ArtifactReference)
            .values(**values)
            .on_conflict_do_nothing(index_elements=identity_columns)
        )
    elif dialect == "sqlite":
        statement = (
            sqlite_insert(ArtifactReference)
            .values(**values)
            .on_conflict_do_nothing(index_elements=identity_columns)
        )
    else:  # pragma: no cover - an unsupported substrate fails closed
        raise ValidationError(f"unsupported database dialect {dialect!r}")
    inserted = cast(CursorResult[Any], session.execute(statement)).rowcount
    if not inserted:
        # Our own unused registry row must not survive a lost race.
        session.delete(registry)
        session.flush()
        return None
    session.flush()
    return session.get(ArtifactReference, resource_id)


def create_literature_reference_row(
    session: Session, authority: str, native_id: str, *, title: str | None
) -> LiteratureReference:
    resource_id = persistence.new_id()
    persistence.register(session, resource_id, ResourceKind.LITERATURE_REFERENCE)
    session.flush()
    row = LiteratureReference(
        literature_id=resource_id, authority=authority, native_id=native_id, title=title
    )
    session.add(row)
    session.flush()
    return row


def create_external_reference_row(
    session: Session,
    external_identity_id: UUID,
    *,
    checksum: str | None,
    as_of: datetime | None,
    cache_metadata: dict[str, Any] | None,
) -> ExternalReference:
    resource_id = persistence.new_id()
    persistence.register(session, resource_id, ResourceKind.EXTERNAL_REFERENCE)
    session.flush()
    row = ExternalReference(
        external_reference_id=resource_id,
        external_identity_id=external_identity_id,
        checksum=checksum,
        as_of=as_of,
        cache_metadata=cache_metadata,
    )
    session.add(row)
    session.flush()
    return row


def find_run_reference(session: Session, authority: str, native_id: str) -> RunReference | None:
    return session.scalar(
        select(RunReference).where(
            RunReference.authority == authority, RunReference.native_id == native_id
        )
    )


def find_session_reference(session: Session, authority: str, native_id: str) -> SessionReference | None:
    return session.scalar(
        select(SessionReference).where(
            SessionReference.authority == authority, SessionReference.native_id == native_id
        )
    )


def find_artifact_reference(
    session: Session, authority: str, native_id: str, version_id: str
) -> ArtifactReference | None:
    return session.scalar(
        select(ArtifactReference).where(
            ArtifactReference.authority == authority,
            ArtifactReference.native_id == native_id,
            ArtifactReference.version_id == version_id,
        )
    )


def find_literature_reference(
    session: Session, authority: str, native_id: str
) -> LiteratureReference | None:
    return session.scalar(
        select(LiteratureReference).where(
            LiteratureReference.authority == authority, LiteratureReference.native_id == native_id
        )
    )


def assert_reference_compatible(row: Any, **fields: Any) -> None:
    """Same canonical identity may be reused, but it may not silently absorb a
    contradictory immutable assertion: a non-null disagreement with the stored
    immutable record is a conflict, not a merge."""
    for name, value in fields.items():
        if value is None:
            continue
        current = getattr(row, name, None)
        if current is not None and current != value:
            raise ConflictError(
                f"reference identity conflict: {name} differs from the existing immutable record"
            )


def link_series(session: Session, project_id: UUID, resource_id: UUID) -> None:
    kind = persistence.resource_kind(session, resource_id)
    if kind is ResourceKind.SCIENTIFIC_OBJECT_REVISION:
        series_id = persistence.revision_series_id(session, resource_id)
        if not persistence.is_visible(session, project_id, series_id):
            raise ValidationError("link the owning series before linking a revision")
    persistence.link(session, project_id, resource_id)
    session.commit()


def share_into_project(session: Session, project_id: UUID, resource_id: UUID) -> None:
    """Identity-free share write: bind `resource_id` into a Project's context
    with the frozen revision⇒series closure. Sharing a revision also links its
    owning Series (the closure's forward direction), while sharing a Series never
    links revisions (the closure's non-reverse direction). No copy is made — the
    same global `resource_id` is linked as a read lens. Idempotent per link."""
    kind = persistence.resource_kind(session, resource_id)
    if kind is ResourceKind.SCIENTIFIC_OBJECT_REVISION:
        series_id = persistence.revision_series_id(session, resource_id)
        if not persistence.is_visible(session, project_id, series_id):
            persistence.link(session, project_id, series_id)
        persistence.link(session, project_id, resource_id)
    else:
        persistence.link(session, project_id, resource_id)


# ---------------------------------------------------------------------------
# Global provenance edges (#1-7), typed
# ---------------------------------------------------------------------------


def add_conceptual_edge(
    session: Session,
    grant: MutationGrant,
    relation_type: RelationType,
    source_series_id: UUID,
    target_series_id: UUID,
    *,
    commit: bool = True,
) -> GlobalProvenanceEdge:
    """A Series -> Series conceptual edge. `commit=False` lets a multi-edge atomic
    bundle (the Phase-14 scientific import) own its own transaction."""
    persistence.validate_grant(session, grant, source_series_id)
    if (
        persistence.resource_kind(session, source_series_id)
        is not ResourceKind.SCIENTIFIC_OBJECT_SERIES
    ):
        raise ValidationError(f"{relation_type.value} source must be a scientific_object_series")
    if (
        persistence.resource_kind(session, target_series_id)
        is not ResourceKind.SCIENTIFIC_OBJECT_SERIES
    ):
        raise ValidationError(f"{relation_type.value} target must be a scientific_object_series")
    return persistence.insert_edge(
        session,
        grant.actor_id,
        relation_type,
        source_series_id,
        ResourceKind.SCIENTIFIC_OBJECT_SERIES,
        target_series_id,
        ResourceKind.SCIENTIFIC_OBJECT_SERIES,
        commit=commit,
    )


def add_revision_edge(
    session: Session,
    grant: MutationGrant,
    relation_type: RelationType,
    source_revision_id: UUID,
    target_revision_id: UUID,
) -> GlobalProvenanceEdge:
    source_series_id = persistence.revision_series_id(session, source_revision_id)
    persistence.validate_grant(session, grant, source_series_id)
    if (
        persistence.resource_kind(session, source_revision_id)
        is not ResourceKind.SCIENTIFIC_OBJECT_REVISION
    ):
        raise ValidationError(f"{relation_type.value} source must be a scientific_object_revision")
    if (
        persistence.resource_kind(session, target_revision_id)
        is not ResourceKind.SCIENTIFIC_OBJECT_REVISION
    ):
        raise ValidationError(f"{relation_type.value} target must be a scientific_object_revision")
    return persistence.insert_edge(
        session,
        grant.actor_id,
        relation_type,
        source_revision_id,
        ResourceKind.SCIENTIFIC_OBJECT_REVISION,
        target_revision_id,
        ResourceKind.SCIENTIFIC_OBJECT_REVISION,
    )


def add_consumed_input(
    session: Session, actor_id: UUID, project_id: UUID, source_id: UUID, target_id: UUID
) -> GlobalProvenanceEdge:
    """The frozen #5 creator authority (SCIENTIFIC_GRAPH.md): task-submission
    authority + read(input). Authority is checked at the command boundary;
    this domain command enforces readability of both endpoints and the endpoint
    shape, never a source stewardship grant."""
    source_kind = persistence.resource_kind(session, source_id)
    persistence.require_visible(session, project_id, source_id)
    target_kind = persistence.resource_kind(session, target_id)
    persistence.validate_edge_shape(RelationType.CONSUMED_AS_INPUT_BY, source_kind, target_kind)
    persistence.require_visible(session, project_id, target_id)
    return persistence.insert_edge(
        session,
        actor_id,
        RelationType.CONSUMED_AS_INPUT_BY,
        source_id,
        source_kind,
        target_id,
        target_kind,
    )


def record_produced(
    session: Session, grant: MutationGrant, project_id: UUID, source_id: UUID, artifact_id: UUID
) -> GlobalProvenanceEdge:
    source_kind = persistence.resource_kind(session, source_id)
    persistence.validate_grant(session, grant, source_id)
    target_kind = persistence.resource_kind(session, artifact_id)
    persistence.validate_edge_shape(RelationType.PRODUCED, source_kind, target_kind)
    if target_kind is not ResourceKind.ARTIFACT_REFERENCE:
        raise ValidationError("produced target must be an artifact reference")
    persistence.require_visible(session, project_id, artifact_id)
    return persistence.insert_edge(
        session,
        grant.actor_id,
        RelationType.PRODUCED,
        source_id,
        source_kind,
        artifact_id,
        target_kind,
    )


def import_revision(
    session: Session,
    grant: MutationGrant,
    project_id: UUID,
    series_id: UUID,
    source_id: UUID,
    *,
    payload: dict[str, Any],
) -> ScientificObjectRevision:
    persistence.validate_grant(session, grant, series_id)
    source_kind = persistence.resource_kind(session, source_id)
    persistence.validate_edge_shape(
        RelationType.IMPORTED_AS, source_kind, ResourceKind.SCIENTIFIC_OBJECT_REVISION
    )
    persistence.require_visible(session, project_id, source_id)
    revision = scientific_object.append_revision(session, grant, series_id, payload)
    persistence.link(session, project_id, revision.revision_id)
    persistence.insert_edge(
        session,
        grant.actor_id,
        RelationType.IMPORTED_AS,
        source_id,
        source_kind,
        revision.revision_id,
        ResourceKind.SCIENTIFIC_OBJECT_REVISION,
    )
    return revision


def find_external_references(
    session: Session, external_identity_id: UUID
) -> list[ExternalReference]:
    """Every resolver snapshot recorded over one durable external identity.

    Ordered deterministically so a caller can require EXACTLY one canonical
    snapshot instead of silently picking an arbitrary row, and so a corrupt
    two-snapshot state is detectable rather than hidden by row order.
    """
    return list(
        session.scalars(
            select(ExternalReference)
            .where(ExternalReference.external_identity_id == external_identity_id)
            .order_by(ExternalReference.created_at, ExternalReference.external_reference_id)
        )
    )


def add_import_edge(
    session: Session,
    grant: MutationGrant,
    project_id: UUID,
    source_id: UUID,
    revision_id: UUID,
    *,
    commit: bool = True,
) -> GlobalProvenanceEdge:
    """#7 `imported_as`: one `ArtifactReference | ExternalReference` was imported
    as ONE concrete immutable Revision.

    Creator authority is the frozen matrix (SCIENTIFIC_GRAPH.md #7): import
    authority + steward(target Series). The source reference must be readable
    through the acting Project. Unlike `import_revision` this does not append a
    revision — it records provenance for a revision that was created as part of
    the same atomic bundle, so the caller passes `commit=False` to keep the whole
    bundle in ONE transaction.
    """
    target_series_id = persistence.revision_series_id(session, revision_id)
    persistence.validate_grant(session, grant, target_series_id)
    source_kind = persistence.resource_kind(session, source_id)
    persistence.require_visible(session, project_id, source_id)
    return persistence.insert_edge(
        session,
        grant.actor_id,
        RelationType.IMPORTED_AS,
        source_id,
        source_kind,
        revision_id,
        ResourceKind.SCIENTIFIC_OBJECT_REVISION,
        commit=commit,
    )


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def is_frozen(session: Session, evidence_id: UUID) -> bool:
    committed_cite = session.scalar(
        select(DecisionEvidence.decision_id)
        .join(Decision, Decision.id == DecisionEvidence.decision_id)
        .where(
            DecisionEvidence.evidence_id == evidence_id,
            Decision.status == DecisionStatus.COMMITTED.value,
        )
        .limit(1)
    )
    if committed_cite is not None:
        return True
    return (
        session.scalar(select(Evidence.id).where(Evidence.target_evidence_id == evidence_id).limit(1))
        is not None
    )


def create_evidence_row(
    session: Session,
    project_id: UUID,
    actor_id: UUID,
    *,
    kind: str,
    role: str = "primary_support",
    label: str | None = None,
    interpretation: str | None = None,
    polarity: str = "neutral",
    confidence: str | None = None,
    confidence_source: str | None = None,
    scope: str | None = None,
    source_kind: str | None = None,
    source_id: UUID | None = None,
    target_kind: str = "scientific_object_revision",
    target_id: UUID | None = None,
    commit: bool = True,
) -> Evidence:
    source_kind_value = ResourceKind(source_kind) if source_kind else None
    if source_id is not None:
        if source_kind_value is None:
            raise ValidationError("source_id requires source_kind")
        if persistence.resource_kind(session, source_id) is not source_kind_value:
            raise ValidationError("source_kind does not match the registered resource kind")
        if source_kind_value not in LEGAL_EVIDENCE_SOURCE_KINDS:
            raise ValidationError("illegal evidence source kind")
        persistence.require_visible(session, project_id, source_id)
    elif source_kind_value is not None:
        raise ValidationError("source_kind requires source_id")

    if target_id is None:
        raise ValidationError("target_id is required")
    try:
        target_kind_value = EvidenceTargetKind(target_kind)
    except ValueError as exc:
        raise ValidationError(f"unknown evidence target kind {target_kind!r}") from exc
    target_revision_id = target_decision_id = target_evidence_id = None
    if target_kind_value is EvidenceTargetKind.SCIENTIFIC_OBJECT_REVISION:
        if persistence.resource_kind(session, target_id) is not ResourceKind.SCIENTIFIC_OBJECT_REVISION:
            raise ValidationError("evidence revision target must be a scientific_object_revision")
        persistence.require_visible(session, project_id, target_id)
        target_revision_id = target_id
    elif target_kind_value is EvidenceTargetKind.DECISION:
        decision = session.get(Decision, target_id)
        if decision is None or decision.project_id != project_id:
            raise NotFoundError("decision not found in project")
        target_decision_id = target_id
    else:
        evidence = session.get(Evidence, target_id)
        if evidence is None or evidence.project_id != project_id:
            raise NotFoundError("evidence not found in project")
        target_evidence_id = target_id

    try:
        kind_value = EvidenceKind(kind)
    except ValueError as exc:
        raise ValidationError(f"unknown evidence kind {kind!r}") from exc

    row = Evidence(
        project_id=project_id,
        kind=kind_value.value,
        role=role,
        label=label,
        interpretation=interpretation,
        polarity=polarity,
        confidence=confidence,
        confidence_source=confidence_source,
        scope=scope,
        source_resource_id=source_id,
        source_kind=source_kind_value.value if source_kind_value else None,
        target_revision_id=target_revision_id,
        target_decision_id=target_decision_id,
        target_evidence_id=target_evidence_id,
        created_by=actor_id,
    )
    session.add(row)
    session.flush()
    if commit:
        session.commit()
    session.refresh(row)
    return row


_MUTABLE_EVIDENCE_FIELDS = frozenset(
    {"role", "label", "interpretation", "polarity", "confidence", "confidence_source", "scope"}
)


class EvidenceMentionTarget(NamedTuple):
    """The Evidence/Provenance domain's public contract for "may this Evidence be
    a context mention target, and what is its label?".

    Consumers outside this domain (for example the application-level Notebook
    service) use this instead of reaching into `Evidence` ORM internals, so the
    domain keeps ownership of its own visibility rule (`project_id` match and
    `archived_at IS NULL`). `active=False` with a non-None record means the
    Evidence exists but is not a legal mention target here; `None` means unknown.
    """

    evidence_id: UUID
    label: str | None
    active: bool


def evidence_mention_target(
    session: Session, project_id: UUID, evidence_id: UUID
) -> EvidenceMentionTarget | None:
    evidence = session.get(Evidence, evidence_id)
    if evidence is None:
        return None
    active = evidence.project_id == project_id and evidence.archived_at is None
    return EvidenceMentionTarget(
        evidence_id=evidence.id,
        label=(evidence.label or f"Evidence ({evidence.kind})") if active else None,
        active=active,
    )


def update_evidence_row(
    session: Session, project_id: UUID, evidence_id: UUID, **fields: Any
) -> Evidence:
    evidence = session.get(Evidence, evidence_id)
    if evidence is None or evidence.project_id != project_id:
        raise NotFoundError("evidence not found in project")
    if is_frozen(session, evidence_id):
        raise ConflictError("evidence is frozen; create a new evidence row to correct it")
    # Whitelist: only the interpretive fields are mutable; identity, source,
    # target, kind, and lifecycle columns are immutable (never silently dropped).
    for key in fields:
        if key not in _MUTABLE_EVIDENCE_FIELDS:
            raise ValidationError(f"evidence {key} is immutable or unknown")
    for key in ("role", "polarity"):
        if key in fields and fields[key] is None:
            raise ValidationError(f"evidence {key} cannot be null")
    for key, value in fields.items():
        setattr(evidence, key, value)
    session.commit()
    session.refresh(evidence)
    return evidence
