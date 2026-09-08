"""Scientific Object domain operations (identity-free).

Every mutation here assumes authority was already validated and passed in at the
command boundary; this module imports neither Identity nor the web framework. It
writes only the Series/Revision/registry rows — Project links and stewardship are
orchestrated by the command boundary (`revolab.services`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from revolab.domain import persistence
from revolab.domain.errors import ConflictError, NotFoundError
from revolab.domain.types_registry import SCHEMA_VERSION, validate_payload
from revolab.enums import ResourceKind
from revolab.models import ScientificObjectRevision, ScientificObjectSeries


def create_spine(
    session: Session,
    actor_id: UUID,
    type_str: str,
    name: str,
    *,
    description: str | None,
    payload: dict[str, Any],
) -> tuple[UUID, UUID]:
    type_ = persistence.object_type(type_str)
    payload = validate_payload(type_, payload)
    checksum = persistence.payload_checksum(payload)
    series_id, revision_id = persistence.new_id(), persistence.new_id()
    persistence.register(session, series_id, ResourceKind.SCIENTIFIC_OBJECT_SERIES)
    persistence.register(session, revision_id, ResourceKind.SCIENTIFIC_OBJECT_REVISION)
    session.flush()
    session.add(
        ScientificObjectSeries(
            series_id=series_id,
            object_type=type_.value,
            name=name,
            description=description,
            created_by=actor_id,
        )
    )
    session.flush()
    session.add(
        ScientificObjectRevision(
            revision_id=revision_id,
            series_id=series_id,
            revision_seq=1,
            object_type=type_.value,
            schema_version=SCHEMA_VERSION,
            payload=payload,
            checksum=checksum,
            created_by=actor_id,
        )
    )
    return series_id, revision_id


def append_revision(
    session: Session,
    actor_id: UUID,
    series_id: UUID,
    payload: dict[str, Any],
    *,
    object_type: str | None = None,
) -> ScientificObjectRevision:
    series = session.get(ScientificObjectSeries, series_id)
    if series is None:
        raise NotFoundError("series not found")
    if series.archived_at is not None:
        raise ConflictError("archived series cannot gain revisions")
    type_ = (
        persistence.object_type(object_type)
        if object_type is not None
        else persistence.object_type(series.object_type)
    )
    payload = validate_payload(type_, payload)
    last = session.scalar(
        select(ScientificObjectRevision)
        .where(ScientificObjectRevision.series_id == series_id)
        .order_by(ScientificObjectRevision.revision_seq.desc())
        .limit(1)
    )
    seq = (last.revision_seq + 1) if last is not None else 1
    revision_id = persistence.new_id()
    persistence.register(session, revision_id, ResourceKind.SCIENTIFIC_OBJECT_REVISION)
    session.flush()
    revision = ScientificObjectRevision(
        revision_id=revision_id,
        series_id=series_id,
        revision_seq=seq,
        object_type=type_.value,
        schema_version=SCHEMA_VERSION,
        payload=payload,
        checksum=persistence.payload_checksum(payload),
        created_by=actor_id,
    )
    session.add(revision)
    session.flush()
    return revision


def update_series(
    session: Session,
    series_id: UUID,
    *,
    name: str | None,
    description: str | None,
) -> ScientificObjectSeries:
    series = session.get(ScientificObjectSeries, series_id)
    if series is None:
        raise NotFoundError("series not found")
    if name is not None:
        series.name = name
    if description is not None:
        series.description = description
    session.flush()
    return series


def mark_archived(session: Session, series_id: UUID) -> None:
    series = session.get(ScientificObjectSeries, series_id)
    if series is None:
        raise NotFoundError("series not found")
    series.archived_at = datetime.now(UTC)
    session.flush()
