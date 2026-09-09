"""Project-scoped HTTP API (the ordinary workspace surface).

Every call that names a global resource does so through a Project. Mutation
requires an acting Actor (X-Actor-Id; real authentication is deferred). Global
provenance edges are exposed only through their typed operations — there is no
generic relation writer.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Path,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from revolab import queries, schemas, services
from revolab.content_store import ContentStore
from revolab.db import get_session
from revolab.domain import provider as provider_domain
from revolab.domain.errors import DomainError
from revolab.drivers import DriverRegistry
from revolab.enums import ResourceKind
from revolab.models import (
    Actor,
    ArtifactReference,
    GlobalProvenanceEdge,
    GlobalResourceRegistry,
    Project,
)
from revolab.secret_store import SecretStore, default_secret_store

router = APIRouter(prefix="/api")


def get_actor(actor_id: str | None = Header(default=None, alias="X-Actor-Id")) -> UUID:
    if actor_id is None:
        raise HTTPException(status_code=401, detail="X-Actor-Id header required")
    try:
        return UUID(actor_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid X-Actor-Id") from exc


def install_exception_handlers(app: FastAPI) -> None:
    from fastapi import Request
    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError):  # type: ignore[no-untyped-def]
        return _json_error(exc.status_code, str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):  # type: ignore[no-untyped-def]
        # Never echo the offending request body: FastAPI's default would include
        # the submitted `input`, which could reflect secret material on a failed
        # credential request.
        return _json_error(422, "invalid request body or parameters")

    @app.exception_handler(LookupError)
    async def _lookup(request: Request, exc: LookupError):  # type: ignore[no-untyped-def]
        return _json_error(404, str(exc))


def _json_error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


@lru_cache
def _content_store() -> ContentStore:
    from revolab.config import get_settings

    return ContentStore(get_settings().content_root)


def get_driver_registry() -> DriverRegistry:
    from revolab.drivers import default_registry

    return default_registry


def get_secret_store() -> SecretStore:
    return default_secret_store()


# ---------------------------------------------------------------------------
# Identity / Project
# ---------------------------------------------------------------------------


@router.post("/actors", response_model=schemas.ActorRead, status_code=201)
def create_actor(session: Session = Depends(get_session)) -> schemas.ActorRead:
    actor_id = services.create_actor(session)
    return schemas.ActorRead(actor_id=actor_id)


@router.get("/actors/{actor_id}", response_model=schemas.ActorRead)
def get_actor_by_id(actor_id: UUID, session: Session = Depends(get_session)) -> schemas.ActorRead:
    """Existence check for a durable opaque Actor id. Not a Project resource;
    used by the frontend to detect and recover from a persisted id that no
    longer exists in this backend (e.g. after a database reset)."""
    actor = session.get(Actor, actor_id)
    if actor is None:
        raise HTTPException(status_code=404, detail="actor not found")
    return schemas.ActorRead(actor_id=actor.actor_id)


@router.post("/projects", response_model=schemas.ProjectRead, status_code=201)
def create_project(
    payload: schemas.ProjectCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> schemas.ProjectRead:
    project = services.create_project(session, actor_id, payload.name, payload.description)
    return _project_read(project)


@router.get("/projects", response_model=list[schemas.ProjectRead])
def list_projects(
    session: Session = Depends(get_session), actor_id: UUID = Depends(get_actor)
) -> list[schemas.ProjectRead]:
    return [_project_read(p) for p in services.list_projects_for_actor(session, actor_id)]


@router.get("/projects/{project_id}", response_model=schemas.ProjectRead)
def get_project(
    project_id: UUID,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> schemas.ProjectRead:
    services.readable_membership(session, actor_id, project_id)
    return _project_read(services.get_project(session, project_id))


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(
    project_id: UUID,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> Response:
    services.delete_project(session, actor_id, project_id)
    return Response(status_code=204)


@router.post("/projects/{project_id}/members", response_model=schemas.MembershipRead, status_code=201)
def add_membership(
    project_id: UUID,
    payload: schemas.MembershipCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> schemas.MembershipRead:
    membership = services.add_membership(session, actor_id, project_id, payload.actor_id, payload.role.value)
    return schemas.MembershipRead(
        project_id=membership.project_id,
        actor_id=membership.actor_id,
        role=membership.role,
    )


# ---------------------------------------------------------------------------
# Scientific objects
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/objects", response_model=list[schemas.ObjectSummaryRead])
def list_objects(
    project_id: UUID,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> Any:
    services.readable_membership(session, actor_id, project_id)
    services.get_project(session, project_id)
    visible = queries.visible_resources(session, project_id)
    from revolab.models import ProjectResourceLink, ScientificObjectRevision, ScientificObjectSeries

    link_ids = session.scalars(
        select(ProjectResourceLink.resource_id)
        .where(
            ProjectResourceLink.project_id == project_id,
            ProjectResourceLink.resource_id.in_(
                select(ScientificObjectSeries.series_id)
            ),
        )
        .order_by(ProjectResourceLink.created_at)
        .offset(offset)
        .limit(limit)
    )
    result: list[dict[str, Any]] = []
    for series_id in link_ids:
        series = session.get(ScientificObjectSeries, series_id)
        if series is None:
            continue
        latest = session.scalar(
            select(ScientificObjectRevision)
            .where(
                ScientificObjectRevision.series_id == series_id,
                ScientificObjectRevision.revision_id.in_(list(visible)),
            )
            .order_by(ScientificObjectRevision.revision_seq.desc())
            .limit(1)
        )
        summary = queries.series_summary(session, series)
        summary["latest_revision"] = queries.revision_summary(latest) if latest else None
        result.append(summary)
    return result


@router.post("/projects/{project_id}/objects", status_code=201, response_model=schemas.ObjectDetailRead)
def create_object(
    project_id: UUID,
    payload: schemas.ObjectCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> Any:
    series_id = services.create_object(
        session,
        actor_id,
        project_id,
        payload.object_type.value,
        payload.name,
        description=payload.description,
        payload=payload.payload,
        folder=payload.folder,
    )
    return queries.object_detail(session, project_id, series_id)


@router.get("/projects/{project_id}/objects/{series_id}", response_model=schemas.ObjectDetailRead)
def get_object(
    project_id: UUID,
    series_id: UUID,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> Any:
    services.readable_membership(session, actor_id, project_id)
    return queries.object_detail(session, project_id, series_id)


@router.patch("/projects/{project_id}/objects/{series_id}", response_model=schemas.ObjectDetailRead)
def patch_object(
    project_id: UUID,
    series_id: UUID,
    payload: schemas.SeriesPatch,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> Any:
    services.update_series(
        session, actor_id, project_id, series_id, name=payload.name, description=payload.description
    )
    return queries.object_detail(session, project_id, series_id)


@router.delete("/projects/{project_id}/objects/{series_id}", status_code=204)
def archive_object(
    project_id: UUID,
    series_id: UUID,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> Response:
    services.archive_series(session, actor_id, project_id, series_id)
    return Response(status_code=204)


@router.post("/projects/{project_id}/objects/{series_id}/revisions", status_code=201)
def append_revision(
    project_id: UUID,
    series_id: UUID,
    payload: schemas.RevisionCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> dict[str, Any]:
    revision = services.append_revision(session, actor_id, project_id, series_id, payload.payload)
    return queries.revision_summary(revision)


@router.post("/projects/{project_id}/objects/{series_id}/import", status_code=201)
def import_object(
    project_id: UUID,
    series_id: UUID,
    payload: schemas.ImportCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> dict[str, Any]:
    revision = services.import_revision(
        session, actor_id, project_id, series_id, payload.source_id, payload=payload.payload
    )
    return queries.revision_summary(revision)


@router.post("/projects/{project_id}/objects/{series_id}/external-identities", status_code=201)
def attach_external_identity(
    project_id: UUID,
    series_id: UUID,
    payload: schemas.ExternalIdentityAttach,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> dict[str, Any]:
    mapping = services.attach_external_identity(
        session,
        actor_id,
        project_id,
        series_id,
        payload.authority,
        payload.native_id,
        qualifier=payload.qualifier,
        kind=payload.kind,
        is_canonical=payload.is_canonical,
    )
    return {
        "external_identity_id": str(mapping.external_identity_id),
        "qualifier": mapping.qualifier,
        "series_id": str(mapping.series_id),
        "is_canonical": mapping.is_canonical,
    }


_REFERENCE_KINDS = frozenset(
    {
        ResourceKind.RUN_REFERENCE,
        ResourceKind.SESSION_REFERENCE,
        ResourceKind.ARTIFACT_REFERENCE,
        ResourceKind.LITERATURE_REFERENCE,
        ResourceKind.EXTERNAL_REFERENCE,
    }
)


@router.get("/projects/{project_id}/resources", response_model=list[schemas.ReferenceRead])
def list_resources(
    project_id: UUID,
    resource_kind: ResourceKind | None = None,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> Any:
    """Project-scoped collection of the reference-card subset of visible
    resources (run/session/artifact/literature/external). The Project lens
    applies: only resources linked into this Project are returned."""
    services.readable_membership(session, actor_id, project_id)
    from revolab.models import ProjectResourceLink

    kinds = _REFERENCE_KINDS if resource_kind is None else {resource_kind}
    kinds = {k for k in kinds if k in _REFERENCE_KINDS}
    link_ids = session.scalars(
        select(ProjectResourceLink.resource_id)
        .join(
            GlobalResourceRegistry,
            GlobalResourceRegistry.resource_id == ProjectResourceLink.resource_id,
        )
        .where(
            ProjectResourceLink.project_id == project_id,
            GlobalResourceRegistry.resource_kind.in_([kind.value for kind in kinds]),
        )
        .order_by(ProjectResourceLink.created_at)
        .offset(offset)
        .limit(limit)
    )
    result = []
    for resource_id in link_ids:
        result.append(queries.reference_summary(session, resource_id, services._resource_kind(session, resource_id)))
    return result


@router.get("/projects/{project_id}/resources/{resource_id}")
def get_resource(
    project_id: UUID,
    resource_id: UUID,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> dict[str, Any]:
    services.readable_membership(session, actor_id, project_id)
    kind = services._resource_kind(session, resource_id)
    services._require_visible(session, project_id, resource_id)
    return queries.reference_summary(session, resource_id, kind)


# ---------------------------------------------------------------------------
# Typed provenance-edge operations (per-edge authority; no generic writer)
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/relations/variant_of", status_code=201)
def variant_of(
    project_id: UUID,
    payload: schemas.SeriesEdgeCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> dict[str, Any]:
    edge = services.add_variant_of(session, actor_id, project_id, payload.source_series_id, payload.target_series_id)
    return _edge_read(edge)


@router.post("/projects/{project_id}/relations/represents", status_code=201)
def represents(
    project_id: UUID,
    payload: schemas.SeriesEdgeCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> dict[str, Any]:
    edge = services.add_represents(session, actor_id, project_id, payload.source_series_id, payload.target_series_id)
    return _edge_read(edge)


@router.post("/projects/{project_id}/relations/derived_from", status_code=201)
def derived_from(
    project_id: UUID,
    payload: schemas.RevisionEdgeCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> dict[str, Any]:
    edge = services.add_derived_from(
        session, actor_id, project_id, payload.source_revision_id, payload.target_revision_id
    )
    return _edge_read(edge)


@router.post("/projects/{project_id}/relations/evaluates", status_code=201)
def evaluates(
    project_id: UUID,
    payload: schemas.RevisionEdgeCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> dict[str, Any]:
    edge = services.add_evaluates(
        session, actor_id, project_id, payload.source_revision_id, payload.target_revision_id
    )
    return _edge_read(edge)


@router.post("/projects/{project_id}/relations/consumed_as_input_by", status_code=201)
def consumed_as_input_by(
    project_id: UUID,
    payload: schemas.InputConsumedCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> dict[str, Any]:
    edge = services.add_consumed_input(session, actor_id, project_id, payload.source_id, payload.target_id)
    return _edge_read(edge)


@router.post("/projects/{project_id}/relations/produced", status_code=201)
def produced(
    project_id: UUID,
    payload: schemas.ProducedCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> dict[str, Any]:
    edge = services.record_produced(session, actor_id, project_id, payload.source_id, payload.artifact_id)
    return _edge_read(edge)


def _edge_read(edge: GlobalProvenanceEdge) -> dict[str, Any]:
    return {
        "edge_id": str(edge.edge_id),
        "relation_type": edge.relation_type,
        "source_id": str(edge.source_id),
        "source_kind": edge.source_kind,
        "target_id": str(edge.target_id),
        "target_kind": edge.target_kind,
    }


# ---------------------------------------------------------------------------
# Reference creation
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/runs", status_code=201)
def create_run(
    project_id: UUID,
    payload: schemas.RunReferenceCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> dict[str, Any]:
    run = services.create_run_reference(
        session,
        actor_id,
        project_id,
        payload.authority,
        payload.native_id,
        task_type=payload.task_type,
        input_parameter_digest=payload.input_parameter_digest,
    )
    return _reference_read(run.run_id, ResourceKind.RUN_REFERENCE, run)


@router.post("/projects/{project_id}/sessions", status_code=201)
def create_session(
    project_id: UUID,
    payload: schemas.SessionReferenceCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> dict[str, Any]:
    row = services.create_session_reference(session, actor_id, project_id, payload.authority, payload.native_id)
    return _reference_read(row.session_id, ResourceKind.SESSION_REFERENCE, row)


@router.post("/projects/{project_id}/literature", status_code=201)
def create_literature(
    project_id: UUID,
    payload: schemas.LiteratureReferenceCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> dict[str, Any]:
    row = services.create_literature_reference(
        session, actor_id, project_id, payload.authority, payload.native_id, title=payload.title
    )
    return _reference_read(row.literature_id, ResourceKind.LITERATURE_REFERENCE, row)


@router.post("/projects/{project_id}/external-references", status_code=201)
def create_external_reference(
    project_id: UUID,
    payload: schemas.ExternalReferenceCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> dict[str, Any]:
    row = services.create_external_reference(
        session,
        actor_id,
        project_id,
        payload.authority,
        payload.native_id,
        kind=payload.kind,
        checksum=payload.checksum,
        cache_metadata=payload.cache_metadata,
    )
    return _reference_read(row.external_reference_id, ResourceKind.EXTERNAL_REFERENCE, row)


@router.post("/projects/{project_id}/artifacts", status_code=201)
def upload_artifact(
    project_id: UUID,
    file: UploadFile,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> dict[str, Any]:
    data = file.file.read()
    artifact = services.create_internal_artifact(
        session, actor_id, project_id, _content_store(), data, content_type=file.content_type
    )
    return _reference_read(artifact.artifact_id, ResourceKind.ARTIFACT_REFERENCE, artifact)


@router.get("/projects/{project_id}/artifacts/{artifact_id}/content")
def resolve_artifact_content(
    project_id: UUID,
    artifact_id: UUID,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> StreamingResponse:
    services.readable_membership(session, actor_id, project_id)
    services._require_visible(session, project_id, artifact_id)
    artifact = session.get(ArtifactReference, artifact_id)
    if artifact is None or artifact.authority != "revolab":
        raise HTTPException(status_code=404, detail="artifact is not an internal revolab artifact")
    data = _content_store().get(artifact.native_id)
    return StreamingResponse(
        iter([data]),
        media_type=artifact.content_type or "application/octet-stream",
    )


def _reference_read(resource_id: UUID, kind: ResourceKind, row: object) -> dict[str, Any]:
    base = {
        "resource_id": str(resource_id),
        "resource_kind": kind.value,
        "authority": getattr(row, "authority", None),
        "native_id": getattr(row, "native_id", None),
        "checksum": getattr(row, "checksum", None),
        "size": getattr(row, "size", None),
        "content_type": getattr(row, "content_type", None),
        "version_id": getattr(row, "version_id", None),
        "task_type": getattr(row, "task_type", None),
        "title": getattr(row, "title", None),
        "created_at": getattr(row, "created_at", None),
        "revoked_at": getattr(row, "revoked_at", None),
    }
    return base


# ---------------------------------------------------------------------------
# Evidence & Decision
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/evidence", response_model=list[schemas.EvidenceRead])
def list_evidence(
    project_id: UUID,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> list[schemas.EvidenceRead]:
    services.readable_membership(session, actor_id, project_id)
    from revolab.models import Evidence

    rows = session.scalars(
        select(Evidence)
        .where(Evidence.project_id == project_id, Evidence.archived_at.is_(None))
        .order_by(Evidence.created_at.desc())
        .offset(offset).limit(limit)
    )
    out = []
    for e in rows:
        summary = queries.evidence_summary(e, frozen=queries._evidence_frozen(session, e.id))
        out.append(schemas.EvidenceRead(**summary))
    return out


@router.post("/projects/{project_id}/evidence", status_code=201, response_model=schemas.EvidenceRead)
def create_evidence(
    project_id: UUID,
    payload: schemas.EvidenceCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> Any:
    evidence = services.create_evidence(
        session,
        actor_id,
        project_id,
        kind=payload.kind.value,
        role=payload.role.value,
        label=payload.label,
        interpretation=payload.interpretation,
        polarity=payload.polarity.value,
        confidence=payload.confidence.value if payload.confidence else None,
        confidence_source=payload.confidence_source,
        scope=payload.scope,
        source_kind=payload.source_kind.value if payload.source_kind else None,
        source_id=payload.source_id,
        target_kind=payload.target_kind.value,
        target_id=payload.target_id,
    )
    return queries.evidence_summary(evidence, frozen=False)


@router.get("/projects/{project_id}/evidence/{evidence_id}", response_model=schemas.EvidenceRead)
def get_evidence(
    project_id: UUID,
    evidence_id: UUID,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> Any:
    services.readable_membership(session, actor_id, project_id)
    from revolab.models import Evidence

    evidence = session.get(Evidence, evidence_id)
    if evidence is None or evidence.project_id != project_id:
        raise HTTPException(status_code=404, detail="evidence not found in project")
    return queries.evidence_summary(
        evidence, frozen=queries._evidence_frozen(session, evidence_id)
    )


@router.patch("/projects/{project_id}/evidence/{evidence_id}", response_model=schemas.EvidenceRead)
def patch_evidence(
    project_id: UUID,
    evidence_id: UUID,
    payload: schemas.EvidencePatch,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> Any:
    fields = payload.model_dump(exclude_unset=True)
    evidence = services.update_evidence(session, actor_id, project_id, evidence_id, **fields)
    return queries.evidence_summary(
        evidence, frozen=queries._evidence_frozen(session, evidence_id)
    )


@router.get("/projects/{project_id}/decisions", response_model=list[schemas.DecisionRead])
def list_decisions(
    project_id: UUID,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> list[schemas.DecisionRead]:
    services.readable_membership(session, actor_id, project_id)
    from revolab.models import Decision

    rows = session.scalars(
        select(Decision)
        .where(Decision.project_id == project_id, Decision.archived_at.is_(None))
        .order_by(Decision.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return [schemas.DecisionRead(**queries.decision_summary(session, d)) for d in rows]


@router.post("/projects/{project_id}/decisions", status_code=201, response_model=schemas.DecisionRead)
def create_decision(
    project_id: UUID,
    payload: schemas.DecisionCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> Any:
    decision = services.create_decision(
        session,
        actor_id,
        project_id,
        title=payload.title,
        statement=payload.statement,
        next_actions=payload.next_actions,
        cites=[{"evidence_id": c.evidence_id, "cited_as": c.cited_as.value} for c in payload.cites],
        selects=[{"target_id": s.target_id, "target_kind": s.target_kind.value} for s in payload.selects],
    )
    return queries.decision_summary(session, decision)


@router.get("/projects/{project_id}/decisions/{decision_id}", response_model=schemas.DecisionRead)
def get_decision(
    project_id: UUID,
    decision_id: UUID,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> Any:
    services.readable_membership(session, actor_id, project_id)
    from revolab.models import Decision

    decision = session.get(Decision, decision_id)
    if decision is None or decision.project_id != project_id:
        raise HTTPException(status_code=404, detail="decision not found in project")
    return queries.decision_summary(session, decision)


@router.patch("/projects/{project_id}/decisions/{decision_id}", response_model=schemas.DecisionRead)
def patch_decision(
    project_id: UUID,
    decision_id: UUID,
    payload: schemas.DecisionPatch,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> Any:
    decision = services.update_decision(
        session,
        actor_id,
        project_id,
        decision_id,
        title=payload.title,
        statement=payload.statement,
        next_actions=payload.next_actions,
        cites=[{"evidence_id": c.evidence_id, "cited_as": c.cited_as.value} for c in payload.cites]
        if payload.cites is not None
        else None,
        selects=[{"target_id": s.target_id, "target_kind": s.target_kind.value} for s in payload.selects]
        if payload.selects is not None
        else None,
    )
    return queries.decision_summary(session, decision)


@router.post("/projects/{project_id}/decisions/{decision_id}/commit", response_model=schemas.DecisionRead)
def commit_decision(
    project_id: UUID,
    decision_id: UUID,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> Any:
    decision = services.commit_decision(session, actor_id, project_id, decision_id)
    return queries.decision_summary(session, decision)


@router.post("/projects/{project_id}/decisions/{decision_id}/supersede", status_code=201)
def supersede_decision(
    project_id: UUID,
    decision_id: UUID,
    payload: schemas.SupersedeCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> dict[str, Any]:
    link = services.supersede_decision(
        session, actor_id, project_id, payload.superseding_decision_id, decision_id
    )
    return {
        "superseding_decision_id": str(link.decision_id),
        "superseded_decision_id": str(link.superseded_decision_id),
    }


# ---------------------------------------------------------------------------
# Graph / context
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/graph")
def graph(
    project_id: UUID,
    from_id: UUID,
    depth: int = 2,
    kinds: str = "relation,evidence,decision",
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> dict[str, Any]:
    services.readable_membership(session, actor_id, project_id)
    return queries.bounded_graph(
        session, project_id, from_id, depth=depth, kinds=set(kinds.split(","))
    )


# ---------------------------------------------------------------------------
# Provider Catalog + Actor-scoped credential management (Phase 3)
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/providers", response_model=list[schemas.ProviderRead])
def list_project_providers(
    project_id: UUID,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
    registry: DriverRegistry = Depends(get_driver_registry),
) -> list[schemas.ProviderRead]:
    services.readable_membership(session, actor_id, project_id)
    return [
        schemas.ProviderRead(**entry)
        for entry in provider_domain.catalog_entries(session, actor_id, project_id, registry)
    ]


@router.get("/credentials", response_model=list[schemas.CredentialBindingRead])
def list_my_credentials(
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> list[schemas.CredentialBindingRead]:
    return [_credential_read(binding) for binding in services.list_credential_bindings(session, actor_id)]


@router.post("/credentials", response_model=schemas.CredentialBindingRead, status_code=201)
def create_credential(
    payload: schemas.CredentialProvision,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
    store: SecretStore = Depends(get_secret_store),
) -> schemas.CredentialBindingRead:
    binding = services.provision_credential(
        session,
        store,
        actor_id,
        payload.provider_key,
        payload.kind,
        payload.secret_value.get_secret_value(),
    )
    return _credential_read(binding)


@router.put("/credentials/{provider_key}/{kind}", response_model=schemas.CredentialBindingRead)
def replace_credential(
    payload: schemas.CredentialReplace,
    provider_key: str = Path(pattern=schemas.PROVIDER_KEY_PATTERN),
    kind: str = Path(pattern=schemas.CREDENTIAL_KIND_PATTERN),
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
    store: SecretStore = Depends(get_secret_store),
) -> schemas.CredentialBindingRead:
    binding = services.rotate_credential(
        session,
        store,
        actor_id,
        provider_key,
        kind,
        payload.secret_value.get_secret_value(),
    )
    return _credential_read(binding)


@router.delete("/credentials/{provider_key}/{kind}", status_code=204)
def revoke_credential(
    provider_key: str = Path(pattern=schemas.PROVIDER_KEY_PATTERN),
    kind: str = Path(pattern=schemas.CREDENTIAL_KIND_PATTERN),
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
    store: SecretStore = Depends(get_secret_store),
) -> Response:
    services.revoke_credential(session, store, actor_id, provider_key, kind)
    return Response(status_code=204)


def _credential_read(binding: object) -> schemas.CredentialBindingRead:
    return schemas.CredentialBindingRead(
        provider_key=getattr(binding, "provider_key"),
        kind=getattr(binding, "kind"),
    )


def _project_read(project: Project) -> schemas.ProjectRead:
    return schemas.ProjectRead(
        id=project.id,
        name=project.name,
        description=project.description,
        visibility=project.visibility,
        created_at=project.created_at,
        deleted_at=project.deleted_at,
    )
