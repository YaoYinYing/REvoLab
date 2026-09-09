"""Bounded artifact-source resolution for local analysis tools.

A local analysis tool may only read artifact bytes through typed boundaries:

  - an internal `revolab` artifact is read from the ContentStore;
  - an external artifact (e.g. a REvoCompute-produced result) is resolved through
    its owning provider's `ArtifactResolutionCapability`, with project policy and
    credential presence enforced at the invocation boundary.

Every path is closed and bounded: no arbitrary filesystem path, no arbitrary
URL, no unbounded whole-artifact read.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from revolab import services
from revolab.capabilities import ExternalArtifactRef
from revolab.domain import compute as compute_domain
from revolab.domain.errors import AuthorizationError, NotFoundError, ValidationError
from revolab.domain.identity import readable_membership
from revolab.enums import CapabilityKind
from revolab.models import ArtifactReference
from revolab.tools.types import InvocationContext

# Explicit per-analysis bounds (TODO.md section 23): a local analysis Tool never
# becomes an accidental denial-of-service path.
MAX_ANALYSIS_BYTES = 1_048_576  # 1 MiB
MAX_ROWS = 10_000
MAX_COLUMNS = 100
MAX_PLOT_POINTS = 5_000


def _is_visible(session: Session, project_id: UUID, artifact_id: UUID) -> bool:
    from revolab.domain import persistence

    return persistence.is_visible(session, project_id, artifact_id)


def require_artifact_visible(
    session: Session, actor_id: UUID, project_id: UUID, artifact_id: UUID
) -> ArtifactReference:
    """Authorize + load the ArtifactReference an analysis tool wants to read."""
    readable_membership(session, actor_id, project_id)
    if not _is_visible(session, project_id, artifact_id):
        raise AuthorizationError("artifact is not visible through this project")
    artifact = session.get(ArtifactReference, artifact_id)
    if artifact is None:
        raise NotFoundError("artifact reference not found")
    return artifact


def read_analysis_bytes(ctx: InvocationContext, artifact_id: UUID) -> tuple[ArtifactReference, bytes]:
    """Read bounded bytes for a local analysis tool, failing closed on size/kind
    mismatches. External bytes are resolved live through the owning provider and
    are never copied into Core here (only the caller may decide to persist a
    derived result through the typed practitioner path)."""
    artifact = require_artifact_visible(ctx.session, ctx.actor_id, ctx.project_id, artifact_id)
    if artifact.size is not None and artifact.size > MAX_ANALYSIS_BYTES:
        raise ValidationError(
            f"artifact exceeds the local analysis byte bound ({MAX_ANALYSIS_BYTES} bytes)"
        )
    if artifact.authority == "revolab":
        data = ctx.content_store.get(artifact.native_id)
    else:
        provider_key = ctx.registry.driver_for_authority(artifact.authority)
        if provider_key is None:
            raise NotFoundError(
                f"no provider registered for authority {artifact.authority!r}"
            )
        resolved = compute_domain.resolve_artifact(
            ctx.session,
            ctx.registry,
            ctx.secret_store,
            ctx.actor_id,
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
                ctx.session, ctx.actor_id, ctx.project_id, CapabilityKind.ARTIFACT_RESOLUTION
            ),
        )
        data = resolved.data or b""
    if len(data) > MAX_ANALYSIS_BYTES:
        raise ValidationError(
            f"artifact exceeds the local analysis byte bound ({MAX_ANALYSIS_BYTES} bytes)"
        )
    return artifact, data


__all__ = [
    "MAX_ANALYSIS_BYTES",
    "MAX_COLUMNS",
    "MAX_PLOT_POINTS",
    "MAX_ROWS",
    "read_analysis_bytes",
    "require_artifact_visible",
]
