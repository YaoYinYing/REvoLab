"""Bounded artifact inspection boundary (Project Tool Harness).

Resolves an ArtifactReference through the existing authority/readability checks,
then either the REvoLab ContentStore (internal, `authority == revolab`) or the
owning provider driver via the existing ArtifactResolution capability. Returns a
bounded preview — never a copy of the artifact into Core and never credential
material. The caller never receives raw filesystem access; provider credentials
remain inside the CredentialLease/driver transport boundary.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from revolab import services
from revolab.capabilities import ExternalArtifactRef
from revolab.content_store import ContentStore
from revolab.domain import compute as compute_domain
from revolab.domain import persistence
from revolab.domain.errors import AuthorizationError, NotFoundError
from revolab.domain.identity import readable_membership
from revolab.drivers import DriverRegistry
from revolab.enums import CapabilityKind
from revolab.models import ArtifactReference
from revolab.schemas import ArtifactInspectRead
from revolab.secret_store import SecretStore

MAX_PREVIEW_LIMIT = 65_536
DEFAULT_PREVIEW_LIMIT = 2_048


def inspect_artifact(
    session: Session,
    registry: DriverRegistry,
    secret_store: SecretStore,
    content_store: ContentStore,
    actor_id: UUID,
    project_id: UUID,
    artifact_id: UUID,
    *,
    preview_limit: int = DEFAULT_PREVIEW_LIMIT,
) -> ArtifactInspectRead:
    readable_membership(session, actor_id, project_id)
    if not persistence.is_visible(session, project_id, artifact_id):
        raise AuthorizationError("artifact is not visible through this project")
    artifact = session.scalar(
        select(ArtifactReference).where(ArtifactReference.artifact_id == artifact_id)
    )
    if artifact is None:
        raise NotFoundError("artifact reference not found")

    limit = max(0, min(preview_limit, MAX_PREVIEW_LIMIT))
    if limit == 0:
        # No bytes are requested: return an empty preview without touching the
        # ContentStore or any provider transport.
        return ArtifactInspectRead(
            artifact_id=artifact_id,
            authority=artifact.authority,
            native_id=artifact.native_id,
            content_type=artifact.content_type,
            size=artifact.size,
            checksum=artifact.checksum,
            version_id=artifact.version_id,
            preview="",
            preview_size=0,
            truncated=(artifact.size is not None and artifact.size > 0),
            binary=False,
        )
    if artifact.authority == "revolab":
        # Bounded read: the ContentStore never materializes the whole artifact
        # into the inspection path.
        head = content_store.read_range(artifact.native_id, offset=0, limit=limit)
    else:
        provider_key = registry.driver_for_authority(artifact.authority)
        if provider_key is None:
            raise NotFoundError(
                f"no provider registered for authority {artifact.authority!r}"
            )
        # Bounded external read through the preview capability; a provider that
        # cannot bound its read fails closed here rather than full-downloading.
        resolved = compute_domain.resolve_artifact_preview(
            session,
            registry,
            secret_store,
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
            offset=0,
            limit=limit,
        )
        head = resolved.data or b""
    try:
        preview = head.decode("utf-8")
        binary = False
    except UnicodeDecodeError:
        # A lossless 1:1 decode so binary bytes never crash the preview surface;
        # the `binary` flag keeps the boundary truthful.
        preview = head.decode("latin-1")
        binary = True

    return ArtifactInspectRead(
        artifact_id=artifact_id,
        authority=artifact.authority,
        native_id=artifact.native_id,
        content_type=artifact.content_type,
        size=artifact.size,
        checksum=artifact.checksum,
        version_id=artifact.version_id,
        preview=preview,
        preview_size=len(head),
        # Truncation is derived from the DURABLE size where known; when the
        # provider did not record a size, a fully-consumed window is reported
        # truncated rather than claiming a complete artifact.
        truncated=(
            (artifact.size is not None and artifact.size > len(head))
            or (artifact.size is None and len(head) >= limit)
        ),
        binary=binary,
    )


__all__ = [
    "DEFAULT_PREVIEW_LIMIT",
    "MAX_PREVIEW_LIMIT",
    "inspect_artifact",
]
