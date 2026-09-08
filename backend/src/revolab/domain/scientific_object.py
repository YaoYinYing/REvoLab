"""Scientific Object domain operations (identity-free).

Every resource mutation here consumes a pre-validated `MutationGrant` (issued by
the command boundary) and re-validates it against current stewardship state. This
module imports neither Identity nor the web framework. Project links and
stewardship are orchestrated by the command boundary (`revolab.services`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from revolab.domain import persistence
from revolab.domain.errors import ConflictError, NotFoundError
from revolab.domain.grants import MutationGrant
from revolab.domain.types_registry import SCHEMA_VERSION, validate_payload
from revolab.enums import ResourceKind
from revolab.models import (
    ExternalIdentity,
    ScientificObjectExternalIdentity,
    ScientificObjectRevision,
    ScientificObjectSeries,
)


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
    grant: MutationGrant,
    series_id: UUID,
    payload: dict[str, Any],
) -> ScientificObjectRevision:
    persistence.validate_grant(session, grant, series_id)
    series = session.get(ScientificObjectSeries, series_id)
    if series is None:
        raise NotFoundError("series not found")
    if series.archived_at is not None:
        raise ConflictError("archived series cannot gain revisions")

    # The conceptual type is immutable: a revision always inherits its Series'
    # object_type. A conceptual type change is a new Series + a scientific
    # relation, never an in-place revision of a different type.
    type_ = persistence.object_type(series.object_type)
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
        created_by=grant.actor_id,
    )
    session.add(revision)
    session.flush()
    return revision


def update_series(
    session: Session,
    grant: MutationGrant,
    series_id: UUID,
    *,
    name: str | None,
    description: str | None,
) -> ScientificObjectSeries:
    persistence.validate_grant(session, grant, series_id)
    series = session.get(ScientificObjectSeries, series_id)
    if series is None:
        raise NotFoundError("series not found")
    if name is not None:
        series.name = name
    if description is not None:
        series.description = description
    session.flush()
    return series


def mark_archived(session: Session, grant: MutationGrant, series_id: UUID) -> None:
    persistence.validate_grant(session, grant, series_id)
    series = session.get(ScientificObjectSeries, series_id)
    if series is None:
        raise NotFoundError("series not found")
    series.archived_at = datetime.now(UTC)
    session.flush()


def get_or_create_external_identity(
    session: Session, authority: str, native_id: str, *, kind: str | None
) -> ExternalIdentity:
    """The Scientific Object domain owns the ExternalIdentity registry."""
    identity = session.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.authority == authority, ExternalIdentity.native_id == native_id
        )
    )
    if identity is not None:
        return identity
    identity = ExternalIdentity(authority=authority, native_id=native_id, kind=kind)
    session.add(identity)
    session.flush()
    return identity


def attach_external_identity(
    session: Session,
    grant: MutationGrant,
    series_id: UUID,
    authority: str,
    native_id: str,
    *,
    qualifier: str = "identity",
    kind: str | None = None,
    is_canonical: bool = False,
) -> ScientificObjectExternalIdentity:
    persistence.validate_grant(session, grant, series_id)
    identity = get_or_create_external_identity(session, authority, native_id, kind=kind)
    mapping = session.get(ScientificObjectExternalIdentity, (identity.external_identity_id, qualifier))
    if mapping is not None:
        if mapping.series_id != series_id:
            raise ConflictError("external identity already maps to another series for this qualifier")
        mapping.is_canonical = is_canonical
    else:
        mapping = ScientificObjectExternalIdentity(
            external_identity_id=identity.external_identity_id,
            qualifier=qualifier,
            series_id=series_id,
            is_canonical=is_canonical,
        )
        session.add(mapping)
    session.flush()
    return mapping
