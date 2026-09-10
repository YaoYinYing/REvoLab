"""Project-scoped HTTP API (the ordinary workspace surface).

Every call that names a global resource does so through a Project. Mutation
requires an acting Actor (X-Actor-Id; real authentication is deferred). Global
provenance edges are exposed only through their typed operations — there is no
generic relation writer.
"""

from __future__ import annotations

import threading
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
    Query,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from revolab import queries, schemas, services
from revolab.agent.builder import build_context
from revolab.agent.model_backend import ModelBackend, OpenAICompatModelBackend
from revolab.agent.runtime import AgentLoopBounds, AgentTurnRunner
from revolab.capabilities import CapabilityError, ExternalArtifactRef, InputBinding
from revolab.config import get_settings
from revolab.content_store import ContentStore
from revolab.db import get_session
from revolab.domain import compute as compute_domain
from revolab.domain import provider as provider_domain
from revolab.domain.errors import DomainError, ModelUnavailableError
from revolab.drivers import DriverRegistry
from revolab.enums import (
    CREDENTIAL_KIND_PATTERN,
    PROVIDER_KEY_PATTERN,
    CapabilityErrorKind,
    CapabilityKind,
    ResourceKind,
    Role,
)
from revolab.models import (
    Actor,
    ArtifactReference,
    GlobalProvenanceEdge,
    GlobalResourceRegistry,
    Project,
    RunReference,
    ToolInvocation,
)
from revolab.secret_store import SecretStore, default_secret_store
from revolab.tools import build_tool_catalog, inspect_artifact
from revolab.tools.registry import LocalToolRegistry, build_default_registry
from revolab.tools.runtime import LocalToolRuntime
from revolab.tools.types import InvocationContext

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

    @app.exception_handler(CapabilityError)
    async def _capability(request: Request, exc: CapabilityError):  # type: ignore[no-untyped-def]
        status = _CAPABILITY_ERROR_STATUS.get(exc.kind, 502)
        return _json_error(status, str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):  # type: ignore[no-untyped-def]
        # Keep FastAPI's documented `HTTPValidationError` envelope (detail = list
        # of errors) so the OpenAPI wire contract stays true, but strip the
        # per-error `input`/`ctx` where Pydantic would otherwise echo the
        # submitted body — which could contain secret material.
        errors = [
            {key: value for key, value in err.items() if key not in {"input", "ctx"}}
            for err in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.exception_handler(LookupError)
    async def _lookup(request: Request, exc: LookupError):  # type: ignore[no-untyped-def]
        return _json_error(404, str(exc))


# Stable HTTP envelope for the Core-owned Capability failure vocabulary. All
# envelope bodies are `{"detail": <string>}` — the ONLY shape the frontend
# surfaces — and never carry secret material or provider stack traces.
_CAPABILITY_ERROR_STATUS = {
    CapabilityErrorKind.AUTH: 502,
    CapabilityErrorKind.NOT_FOUND: 404,
    CapabilityErrorKind.INVALID_PARAM: 422,
    CapabilityErrorKind.PROVIDER_UNAVAILABLE: 503,
    CapabilityErrorKind.NETWORK: 502,
    CapabilityErrorKind.UNKNOWN: 502,
}


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


@lru_cache
def _local_registry() -> LocalToolRegistry:
    """The ONE local tool registry wired into both the human invocation surface
    and the Agent loop catalog so validation and execution share the same set."""
    return build_default_registry()


def get_local_runtime() -> LocalToolRuntime:
    return LocalToolRuntime(_local_registry())


_model_backend_instance: ModelBackend | None = None
_model_backend_initialized = False
_model_backend_lock = threading.Lock()


def _build_model_backend() -> ModelBackend | None:
    settings = get_settings()
    if settings.model_endpoint and settings.model_name:
        return OpenAICompatModelBackend(
            endpoint=settings.model_endpoint,
            model=settings.model_name,
            api_key=(
                settings.model_api_key.get_secret_value()
                if settings.model_api_key is not None
                else None
            ),
            timeout_seconds=settings.model_timeout_seconds,
        )
    if settings.e2e_fake_model:
        if settings.environment == "production":
            raise RuntimeError("the fake model runtime must not be enabled in production")
        from revolab.testing.fake_model import ScriptedModelBackend

        return ScriptedModelBackend()
    return None


def _model_backend() -> ModelBackend | None:
    """Resolve ONE configured concrete model backend (TODO.md section 4). No
    generalized provider framework; no silent fake fallback in production. The
    returned instance owns a transport (httpx.Client) whose lifetime is the
    application's, closed explicitly via `close_model_backend`. Construction is
    guarded so concurrent first requests share ONE transport instead of racing
    to build (and leak) several."""
    global _model_backend_instance, _model_backend_initialized
    if _model_backend_initialized:
        return _model_backend_instance
    with _model_backend_lock:
        if not _model_backend_initialized:
            _model_backend_instance = _build_model_backend()
            _model_backend_initialized = True
        return _model_backend_instance


def close_model_backend() -> None:
    """Close the cached model transport at application shutdown. Idempotent and
    safe when no backend was ever built or the backend has no `close`."""
    global _model_backend_instance, _model_backend_initialized
    with _model_backend_lock:
        backend = _model_backend_instance
        _model_backend_instance = None
        _model_backend_initialized = False
        closer = getattr(backend, "close", None)
        if callable(closer):
            closer()


def get_model_backend() -> ModelBackend:
    backend = _model_backend()
    if backend is None:
        raise ModelUnavailableError(
            "no model runtime configured: set REVOLAB_MODEL_ENDPOINT and "
            "REVOLAB_MODEL_NAME (or enable the test-only fake)"
        )
    return backend


def _agent_bounds() -> AgentLoopBounds:
    settings = get_settings()
    return AgentLoopBounds(
        max_model_turns=settings.agent_max_model_turns,
        max_tool_calls=settings.agent_max_tool_calls,
        max_tool_calls_per_turn=settings.agent_max_tool_calls_per_turn,
        max_context_chars=settings.agent_max_context_chars,
        max_history_messages=settings.agent_max_history_messages,
        max_history_chars=settings.agent_max_history_chars,
        max_skill_count=settings.agent_max_skill_count,
        max_skill_bytes=settings.agent_max_skill_bytes,
        max_tool_result_chars=settings.agent_max_tool_result_chars,
        total_turn_duration_seconds=settings.agent_total_turn_duration_seconds,
    )


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
    project = services.create_project(
        session, actor_id, payload.name, payload.description, visibility=payload.visibility.value
    )
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


@router.patch("/projects/{project_id}", response_model=schemas.ProjectRead)
def patch_project(
    project_id: UUID,
    payload: schemas.ProjectPatch,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> schemas.ProjectRead:
    project = services.update_project(
        session,
        actor_id,
        project_id,
        name=payload.name,
        description=payload.description,
        visibility=payload.visibility.value if payload.visibility is not None else None,
        clear_description=("description" in payload.model_fields_set and payload.description is None),
    )
    return _project_read(project)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(
    project_id: UUID,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> Response:
    services.delete_project(session, actor_id, project_id)
    return Response(status_code=204)


@router.get("/projects/{project_id}/members", response_model=list[schemas.MembershipRead])
def list_members(
    project_id: UUID,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> list[schemas.MembershipRead]:
    return [
        schemas.MembershipRead(project_id=m.project_id, actor_id=m.actor_id, role=Role(m.role))
        for m in services.list_memberships(session, actor_id, project_id)
    ]


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
        role=Role(membership.role),
    )


@router.patch("/projects/{project_id}/members/{member_actor_id}", response_model=schemas.MembershipRead)
def update_membership(
    project_id: UUID,
    member_actor_id: UUID,
    payload: schemas.MembershipUpdate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> schemas.MembershipRead:
    membership = services.update_membership(
        session, actor_id, project_id, member_actor_id, payload.role.value
    )
    return schemas.MembershipRead(
        project_id=membership.project_id,
        actor_id=membership.actor_id,
        role=Role(membership.role),
    )


@router.delete("/projects/{project_id}/members/{member_actor_id}", status_code=204)
def remove_membership(
    project_id: UUID,
    member_actor_id: UUID,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> Response:
    services.remove_membership(session, actor_id, project_id, member_actor_id)
    return Response(status_code=204)


@router.post("/projects/{project_id}/shares", response_model=schemas.ResourceShareRead, status_code=201)
def share_resource(
    project_id: UUID,
    payload: schemas.ResourceShareCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> schemas.ResourceShareRead:
    kind = services.share_resource(session, actor_id, project_id, payload.resource_id)
    return schemas.ResourceShareRead(resource_id=payload.resource_id, resource_kind=kind)


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
        visible_revisions = list(
            session.scalars(
                select(ScientificObjectRevision)
                .where(
                    ScientificObjectRevision.series_id == series_id,
                    ScientificObjectRevision.revision_id.in_(list(visible)),
                )
                .order_by(ScientificObjectRevision.revision_seq)
            )
        )
        current = None
        pin = queries.preferred_revision_id(session, project_id, series_id)
        if pin is not None:
            current = next((r for r in visible_revisions if r.revision_id == pin), None)
        if current is None and visible_revisions:
            current = visible_revisions[-1]
        summary = queries.series_summary(session, series)
        summary["latest_revision"] = queries.revision_summary(current) if current else None
        summary["preferred_revision_id"] = str(pin) if pin else None
        summary["read_only"] = queries.read_only(session, project_id, series_id)
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


@router.put("/projects/{project_id}/objects/{series_id}/preferred-revision", status_code=204)
def set_preferred_revision(
    project_id: UUID,
    series_id: UUID,
    payload: schemas.PreferredRevisionPut,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> Response:
    services.set_preferred_revision(session, actor_id, project_id, series_id, payload.revision_id)
    return Response(status_code=204)


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
        summary = queries.reference_summary(session, resource_id, services._resource_kind(session, resource_id))
        summary["read_only"] = queries.read_only(session, project_id, resource_id)
        result.append(summary)
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
    summary = queries.reference_summary(session, resource_id, kind)
    summary["read_only"] = queries.read_only(session, project_id, resource_id)
    return summary


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
    if evidence is None or evidence.project_id != project_id or evidence.archived_at is not None:
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
    if decision is None or decision.project_id != project_id or decision.archived_at is not None:
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
# Agent Context & Tools (Phase 6) — the Agent is a consumer, never an owner.
# Every mutation below routes to the SAME typed domain/application service used
# by the normal API callers (no Agent-specific persistence; no committed-truth
# path except the explicit authorized commit endpoint under /decisions).
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/context", response_model=schemas.ProjectContextRead)
def build_project_context(
    project_id: UUID,
    payload: schemas.ContextSelectionCreate | None = None,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
    registry: DriverRegistry = Depends(get_driver_registry),
) -> schemas.ProjectContextRead:
    return build_context(session, actor_id, project_id, registry, payload)


@router.get("/projects/{project_id}/agent/tools", response_model=schemas.ToolCatalogRead)
def list_agent_tools(
    project_id: UUID,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
    registry: DriverRegistry = Depends(get_driver_registry),
) -> schemas.ToolCatalogRead:
    return build_tool_catalog(session, actor_id, project_id, registry)


# ---------------------------------------------------------------------------
# Project Tool Harness (Phase 7). The SAME canonical ToolCatalog the Agent
# endpoint above serves is exposed to the human workspace below; invocation is
# closed and typed (registered local tools only — remote Provider tools execute
# through their existing capability endpoints).
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/tools", response_model=schemas.ToolCatalogRead)
def list_project_tools(
    project_id: UUID,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
    registry: DriverRegistry = Depends(get_driver_registry),
) -> schemas.ToolCatalogRead:
    return build_tool_catalog(session, actor_id, project_id, registry)


@router.post(
    "/projects/{project_id}/tools/invocations",
    status_code=201,
    response_model=schemas.ToolResultRead,
)
def invoke_project_tool(
    project_id: UUID,
    payload: schemas.ToolInvocationCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
    registry: DriverRegistry = Depends(get_driver_registry),
    store: SecretStore = Depends(get_secret_store),
    runtime: LocalToolRuntime = Depends(get_local_runtime),
) -> schemas.ToolResultRead:
    ctx = InvocationContext(
        session=session,
        registry=registry,
        secret_store=store,
        content_store=_content_store(),
        actor_id=actor_id,
        project_id=project_id,
    )
    return runtime.invoke(ctx, payload)


@router.get(
    "/projects/{project_id}/tool-invocations",
    response_model=list[schemas.ToolInvocationRead],
)
def list_tool_invocations(
    project_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
) -> list[schemas.ToolInvocationRead]:
    """Project-scoped activity log of persisted local-analysis Tool invocations
    (reproducibility + observability). Never exposes secret material; the stored
    `parameters` are canonical validated model dumps, not raw request input."""
    services.readable_membership(session, actor_id, project_id)
    rows = session.scalars(
        select(ToolInvocation)
        .where(ToolInvocation.project_id == project_id)
        .order_by(ToolInvocation.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return [schemas.ToolInvocationRead(**invocation_read(row)) for row in rows]


def invocation_read(row: ToolInvocation) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "tool_id": row.tool_id,
        "tool_version": row.tool_version,
        "actor_id": row.actor_id,
        "input_resource_ids": list(row.input_resource_ids or []),
        "parameters": row.parameters,
        "result_kind": row.result_kind,
        "result_resource_id": row.result_resource_id,
        "status": row.status,
        "created_at": row.created_at,
    }


@router.post(
    "/projects/{project_id}/agent/turns",
    response_model=schemas.AgentTurnRead,
)
def create_agent_turn(
    project_id: UUID,
    payload: schemas.AgentTurnCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
    registry: DriverRegistry = Depends(get_driver_registry),
    store: SecretStore = Depends(get_secret_store),
    runtime: LocalToolRuntime = Depends(get_local_runtime),
    model: ModelBackend = Depends(get_model_backend),
) -> schemas.AgentTurnRead:
    """Run one bounded Project Agent turn. The Agent is a consumer, never an
    owner: context is rebuilt from Project truth, tool calls are validated
    against the canonical ToolCatalog and executed only through LocalToolRuntime,
    explicit actions become PendingActions, and the only Decision shape
    producible is a DRAFT. No raw provider/model response object is exposed."""
    runner = AgentTurnRunner(
        model,
        runtime,
        registry,
        store,
        _content_store(),
        _agent_bounds(),
        local_registry=_local_registry(),
    )
    return runner.run(
        session,
        actor_id,
        project_id,
        payload.message,
        payload.selection,
        payload.history,
    )


@router.get(
    "/projects/{project_id}/artifacts/{artifact_id}/inspect",
    response_model=schemas.ArtifactInspectRead,
)
def inspect_project_artifact(
    project_id: UUID,
    artifact_id: UUID,
    preview_limit: int = Query(default=2048, ge=0, le=65536),
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
    registry: DriverRegistry = Depends(get_driver_registry),
    store: SecretStore = Depends(get_secret_store),
) -> schemas.ArtifactInspectRead:
    return inspect_artifact(
        session,
        registry,
        store,
        _content_store(),
        actor_id,
        project_id,
        artifact_id,
        preview_limit=preview_limit,
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
        for entry in provider_domain.catalog_entries(
            session,
            actor_id,
            registry,
            policy_permits=lambda capability_kind: services.project_policy_permits(
                session, actor_id, project_id, capability_kind
            ),
        )
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
    registry: DriverRegistry = Depends(get_driver_registry),
) -> schemas.CredentialBindingRead:
    binding = services.provision_credential(
        session,
        store,
        registry,
        actor_id,
        payload.provider_key,
        payload.kind,
        payload.secret_value.get_secret_value(),
    )
    return _credential_read(binding)


@router.put("/credentials/{provider_key}/{kind}", response_model=schemas.CredentialBindingRead)
def replace_credential(
    payload: schemas.CredentialReplace,
    provider_key: str = Path(pattern=PROVIDER_KEY_PATTERN),
    kind: str = Path(pattern=CREDENTIAL_KIND_PATTERN),
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
    store: SecretStore = Depends(get_secret_store),
    registry: DriverRegistry = Depends(get_driver_registry),
) -> schemas.CredentialBindingRead:
    binding = services.rotate_credential(
        session,
        store,
        registry,
        actor_id,
        provider_key,
        kind,
        payload.secret_value.get_secret_value(),
    )
    return _credential_read(binding)


@router.delete("/credentials/{provider_key}/{kind}", status_code=204)
def revoke_credential(
    provider_key: str = Path(pattern=PROVIDER_KEY_PATTERN),
    kind: str = Path(pattern=CREDENTIAL_KIND_PATTERN),
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
    store: SecretStore = Depends(get_secret_store),
) -> Response:
    services.revoke_credential(session, store, actor_id, provider_key, kind)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Compute vertical slice (Phase 4): task-kind discovery (schema-as-data),
# submission, live run state, artifact discovery and resolution. Provider
# vocabulary (task names, parameter names, status strings) flows as data.
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/providers/{provider_key}/compute/task-kinds",
    response_model=list[schemas.ComputeTaskKindRead],
)
def list_compute_task_kinds(
    project_id: UUID,
    provider_key: str = Path(pattern=PROVIDER_KEY_PATTERN),
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
    registry: DriverRegistry = Depends(get_driver_registry),
    store: SecretStore = Depends(get_secret_store),
) -> list[schemas.ComputeTaskKindRead]:
    services.readable_membership(session, actor_id, project_id)
    task_kinds = compute_domain.list_task_kinds(
        session,
        registry,
        store,
        actor_id,
        provider_key,
        permitted=services.project_policy_permits(session, actor_id, project_id, CapabilityKind.COMPUTE),
    )
    return [schemas.ComputeTaskKindRead(**item.__dict__) for item in task_kinds]


@router.get(
    "/projects/{project_id}/providers/{provider_key}/compute/task-kinds/{kind_id}/schema",
    response_model=schemas.ComputeTaskKindSchemaRead,
)
def get_compute_task_kind_schema(
    project_id: UUID,
    provider_key: str = Path(pattern=PROVIDER_KEY_PATTERN),
    kind_id: str = Path(min_length=1, max_length=300),
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
    registry: DriverRegistry = Depends(get_driver_registry),
    store: SecretStore = Depends(get_secret_store),
) -> schemas.ComputeTaskKindSchemaRead:
    services.readable_membership(session, actor_id, project_id)
    schema = compute_domain.task_kind_schema(
        session,
        registry,
        store,
        actor_id,
        provider_key,
        kind_id,
        permitted=services.project_policy_permits(session, actor_id, project_id, CapabilityKind.COMPUTE),
    )
    return schemas.ComputeTaskKindSchemaRead(
        kind_id=schema.kind_id,
        display_name=schema.display_name,
        description=schema.description,
        parameter_schema=dict(schema.parameter_schema),
        input_spec=schemas.ComputeInputSpecRead(
            label=schema.input_spec.label,
            required=schema.input_spec.required,
            multiple=schema.input_spec.multiple,
            max_files=schema.input_spec.max_files,
            accepted_extensions=list(schema.input_spec.accepted_extensions),
        ),
    )


@router.post(
    "/projects/{project_id}/compute/submissions",
    status_code=201,
    response_model=schemas.ComputeSubmissionRead,
)
def create_compute_submission(
    project_id: UUID,
    payload: schemas.ComputeSubmissionCreate,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
    registry: DriverRegistry = Depends(get_driver_registry),
    store: SecretStore = Depends(get_secret_store),
) -> schemas.ComputeSubmissionRead:
    bindings = [
        InputBinding(kind=item.kind, resource_id=item.resource_id, role=item.role)
        for item in payload.inputs
    ]
    result = services.compute_submit(
        session,
        registry,
        store,
        _content_store(),
        actor_id,
        project_id,
        payload.provider_key,
        payload.task_kind,
        bindings,
        payload.params,
    )
    return schemas.ComputeSubmissionRead(
        run_resource_id=result["run_resource_id"],
        authority=result["authority"],
        native_id=result["native_id"],
        task_type=result["task_type"],
        consumed_edges=result["consumed_edges"],
    )


@router.get(
    "/projects/{project_id}/runs/{run_id}/status",
    response_model=schemas.ComputeRunStatusRead,
)
def get_compute_run_status(
    project_id: UUID,
    run_id: UUID,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
    registry: DriverRegistry = Depends(get_driver_registry),
    store: SecretStore = Depends(get_secret_store),
) -> schemas.ComputeRunStatusRead:
    services.readable_membership(session, actor_id, project_id)
    run = _require_run(session, project_id, run_id)
    provider_key = registry.driver_for_authority(run.authority)
    if provider_key is None:
        raise HTTPException(status_code=404, detail=f"no provider registered for authority {run.authority!r}")
    permitted = services.project_policy_permits(session, actor_id, project_id, CapabilityKind.COMPUTE)
    try:
        view = compute_domain.get_run(
            session,
            registry,
            store,
            actor_id,
            provider_key,
            run.native_id,
            permitted=permitted,
        )
    except CapabilityError as exc:
        # A transient provider outage must not invalidate the stored reference:
        # live resolution is simply unavailable. Authorization and
        # credential-missing failures raise AuthorizationError and propagate as
        # their own typed status, never as "unavailable".
        if exc.kind in {CapabilityErrorKind.PROVIDER_UNAVAILABLE, CapabilityErrorKind.NETWORK}:
            return schemas.ComputeRunStatusRead(
                run_resource_id=run_id,
                authority=run.authority,
                native_id=run.native_id,
                available=False,
                detail=str(exc),
            )
        raise
    return schemas.ComputeRunStatusRead(
        run_resource_id=run_id,
        authority=view.authority,
        native_id=view.native_id,
        available=True,
        status=view.status,
        detail=view.status_detail,
    )


@router.post(
    "/projects/{project_id}/runs/{run_id}/artifacts",
    status_code=201,
    response_model=list[schemas.ComputeArtifactRead],
)
def refresh_compute_run_artifacts(
    project_id: UUID,
    run_id: UUID,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
    registry: DriverRegistry = Depends(get_driver_registry),
    store: SecretStore = Depends(get_secret_store),
) -> list[schemas.ComputeArtifactRead]:
    services.readable_membership(session, actor_id, project_id)
    run = _require_run(session, project_id, run_id)
    provider_key = registry.driver_for_authority(run.authority)
    if provider_key is None:
        raise HTTPException(status_code=404, detail=f"no provider registered for authority {run.authority!r}")
    artifacts = services.compute_refresh_artifacts(
        session,
        registry,
        store,
        actor_id,
        project_id,
        provider_key,
        run_id,
        run.native_id,
    )
    return [schemas.ComputeArtifactRead(**item) for item in artifacts]


@router.get(
    "/projects/{project_id}/artifacts/{artifact_id}/resolve",
    responses={
        200: {
            "description": "Live external artifact bytes (not implicitly ingested).",
            "content": {"*/*": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
def resolve_compute_artifact(
    project_id: UUID,
    artifact_id: UUID,
    session: Session = Depends(get_session),
    actor_id: UUID = Depends(get_actor),
    registry: DriverRegistry = Depends(get_driver_registry),
    store: SecretStore = Depends(get_secret_store),
) -> StreamingResponse:
    """Live, on-demand external artifact access. Resolving bytes is NOT implicit
    ingestion: nothing is copied into REvoLab ContentStore here."""
    services.readable_membership(session, actor_id, project_id)
    artifact = _require_artifact(session, project_id, artifact_id)
    if artifact.authority == "revolab":
        raise HTTPException(status_code=404, detail="use the internal artifact content endpoint")
    provider_key = registry.driver_for_authority(artifact.authority)
    if provider_key is None:
        raise HTTPException(
            status_code=404, detail=f"no provider registered for authority {artifact.authority!r}"
        )
    resolved = compute_domain.resolve_artifact(
        session,
        registry,
        store,
        actor_id,
        provider_key,
        ExternalArtifactRef(
            authority=artifact.authority,
            native_id=artifact.native_id,
            version_id=artifact.version_id,
            content_type=artifact.content_type,
            size=artifact.size,
            checksum=artifact.checksum,
        ),
        permitted=services.project_policy_permits(
            session, actor_id, project_id, CapabilityKind.ARTIFACT_RESOLUTION
        ),
    )
    return StreamingResponse(
        iter([resolved.data or b""]),
        media_type=artifact.content_type or "application/octet-stream",
    )


def _require_run(session: Session, project_id: UUID, run_id: UUID) -> RunReference:
    services._require_visible(session, project_id, run_id)
    run = session.get(RunReference, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run reference not found")
    return run


def _require_artifact(session: Session, project_id: UUID, artifact_id: UUID) -> ArtifactReference:
    services._require_visible(session, project_id, artifact_id)
    artifact = session.get(ArtifactReference, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact reference not found")
    return artifact


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
