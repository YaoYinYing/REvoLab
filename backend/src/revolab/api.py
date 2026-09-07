from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from revolab import models
from revolab.db import get_session
from revolab.schemas import (
    DecisionCreate,
    DecisionRead,
    EvidenceCreate,
    EvidenceRead,
    ObjectCreate,
    ObjectRead,
    ProjectCreate,
    ProjectGraph,
    ProjectSummary,
    RelationCreate,
    RelationRead,
)

router = APIRouter(prefix="/api")


def get_project_or_404(session: Session, project_id: UUID) -> models.Project:
    project = session.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@router.post("/projects", response_model=ProjectSummary, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, session: Session = Depends(get_session)) -> models.Project:
    project = models.Project(name=payload.name, description=payload.description)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


@router.get("/projects", response_model=list[ProjectSummary])
def list_projects(session: Session = Depends(get_session)) -> list[models.Project]:
    return list(session.scalars(select(models.Project).order_by(models.Project.created_at.desc())))


@router.get("/projects/{project_id}", response_model=ProjectGraph)
def get_project(project_id: UUID, session: Session = Depends(get_session)) -> ProjectGraph:
    project = get_project_or_404(session, project_id)
    return ProjectGraph(
        id=project.id,
        name=project.name,
        description=project.description,
        created_at=project.created_at,
        objects=[ObjectRead.from_model(item) for item in project.objects],
        relations=[RelationRead.model_validate(item) for item in project.relations],
        evidence=[EvidenceRead.from_model(item) for item in project.evidence],
        decisions=[DecisionRead.model_validate(item) for item in project.decisions],
    )


@router.post("/projects/{project_id}/objects", response_model=ObjectRead, status_code=201)
def create_object(project_id: UUID, payload: ObjectCreate, session: Session = Depends(get_session)) -> ObjectRead:
    get_project_or_404(session, project_id)
    if payload.parent_id is not None:
        parent = session.scalar(
            select(models.ScientificObject).where(
                models.ScientificObject.id == payload.parent_id,
                models.ScientificObject.project_id == project_id,
            )
        )
        if parent is None:
            raise HTTPException(status_code=422, detail="parent must belong to the project")
    item = models.ScientificObject(
        project_id=project_id,
        name=payload.name,
        object_type=payload.object_type.value,
        description=payload.description,
        parent_id=payload.parent_id,
        metadata_json=payload.metadata,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return ObjectRead.from_model(item)


@router.get("/projects/{project_id}/objects", response_model=list[ObjectRead])
def list_objects(project_id: UUID, session: Session = Depends(get_session)) -> list[ObjectRead]:
    get_project_or_404(session, project_id)
    objects = session.scalars(
        select(models.ScientificObject)
        .where(models.ScientificObject.project_id == project_id)
        .order_by(models.ScientificObject.parent_id, models.ScientificObject.name)
    )
    return [ObjectRead.from_model(item) for item in objects]


@router.post("/projects/{project_id}/relations", response_model=RelationRead, status_code=201)
def create_relation(project_id: UUID, payload: RelationCreate, session: Session = Depends(get_session)) -> models.Relation:
    get_project_or_404(session, project_id)
    objects = session.scalars(
        select(models.ScientificObject).where(
            models.ScientificObject.project_id == project_id,
            models.ScientificObject.id.in_([payload.source_id, payload.target_id]),
        )
    ).all()
    if len(objects) != 2:
        raise HTTPException(status_code=422, detail="both relation objects must belong to the project")
    relation = models.Relation(
        project_id=project_id,
        source_id=payload.source_id,
        target_id=payload.target_id,
        relation_type=payload.relation_type.value,
        rationale=payload.rationale,
    )
    session.add(relation)
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="relation already exists") from exc
    session.refresh(relation)
    return relation


@router.post("/projects/{project_id}/evidence", response_model=EvidenceRead, status_code=201)
def create_evidence(project_id: UUID, payload: EvidenceCreate, session: Session = Depends(get_session)) -> EvidenceRead:
    get_project_or_404(session, project_id)
    evidence = models.Evidence(
        project_id=project_id,
        evidence_type=payload.evidence_type.value,
        label=payload.label,
        summary=payload.summary,
        provider=payload.provider,
        external_id=payload.external_id,
        metadata_json=payload.metadata,
    )
    session.add(evidence)
    session.commit()
    session.refresh(evidence)
    return EvidenceRead.from_model(evidence)


@router.post("/projects/{project_id}/decisions", response_model=DecisionRead, status_code=201)
def create_decision(project_id: UUID, payload: DecisionCreate, session: Session = Depends(get_session)) -> models.Decision:
    get_project_or_404(session, project_id)
    if payload.evidence_ids:
        all_evidence = session.scalars(
            select(models.Evidence.id).where(
                models.Evidence.project_id == project_id,
                models.Evidence.id.in_(payload.evidence_ids),
            )
        ).all()
        if len(all_evidence) != len(set(payload.evidence_ids)):
            raise HTTPException(status_code=422, detail="all evidence must belong to the project")
    decision = models.Decision(
        project_id=project_id,
        title=payload.title,
        statement=payload.statement,
        status=payload.status,
        next_actions=payload.next_actions,
    )
    session.add(decision)
    session.flush()
    for evidence_id in set(payload.evidence_ids):
        session.add(models.DecisionEvidence(decision_id=decision.id, evidence_id=evidence_id))
    session.commit()
    session.refresh(decision)
    return decision
