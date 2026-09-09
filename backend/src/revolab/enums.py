"""Core-owned closed vocabulary (single source of truth for domain enums).

These enums are the canonical wire values for REvoLab scientific semantics.
They are consumed by the models, the typed domain services, and the generated
API contracts. Nothing in Core may duplicate them (see ADR-0007/0010/0014).
"""

from __future__ import annotations

import re
from enum import StrEnum

# Canonical identifier grammar for Provider/credential identity. This is the ONE
# place these shapes are defined: driver registration, API/Pydantic validation,
# and credential-management path parameters all reference these — never their own
# copies of the same literals.
PROVIDER_KEY_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,99}$"
CREDENTIAL_KIND_PATTERN = r"^[A-Za-z0-9._-]{1,100}$"

_PROVIDER_KEY_RE = re.compile(PROVIDER_KEY_PATTERN)
_CREDENTIAL_KIND_RE = re.compile(CREDENTIAL_KIND_PATTERN)


def is_valid_provider_key(value: str) -> bool:
    return bool(_PROVIDER_KEY_RE.fullmatch(value))


def is_valid_credential_kind(value: str) -> bool:
    return bool(_CREDENTIAL_KIND_RE.fullmatch(value))


class Role(StrEnum):
    OWNER = "owner"
    MEMBER = "member"
    VIEWER = "viewer"


class ProjectVisibility(StrEnum):
    PRIVATE = "private"
    SHARED_WITH_MEMBERS = "shared_with_members"


class ResourceKind(StrEnum):
    """Global-resource kind labels (singular; concrete tables are plural)."""

    SCIENTIFIC_OBJECT_SERIES = "scientific_object_series"
    SCIENTIFIC_OBJECT_REVISION = "scientific_object_revision"
    RUN_REFERENCE = "run_reference"
    SESSION_REFERENCE = "session_reference"
    ARTIFACT_REFERENCE = "artifact_reference"
    LITERATURE_REFERENCE = "literature_reference"
    EXTERNAL_REFERENCE = "external_reference"


class ObjectType(StrEnum):
    PROTEIN = "protein"
    SEQUENCE = "sequence"
    STRUCTURE = "structure"
    VARIANT = "variant"
    LIGAND = "ligand"
    COMPLEX = "complex"
    DATASET = "dataset"
    ASSAY = "assay"
    CONSTRUCT = "construct"
    OTHER = "other"


class RelationType(StrEnum):
    """Canonical closed relation enum (SCIENTIFIC_GRAPH.md); #1-7 are global
    provenance edges, #8-10 are project knowledge edges. `generated_by` is a
    derived traversal and is deliberately absent."""

    VARIANT_OF = "variant_of"  # 1
    DERIVED_FROM = "derived_from"  # 2
    REPRESENTS = "represents"  # 3
    EVALUATES = "evaluates"  # 4
    CONSUMED_AS_INPUT_BY = "consumed_as_input_by"  # 5
    PRODUCED = "produced"  # 6
    IMPORTED_AS = "imported_as"  # 7
    SELECTS = "selects"  # 8
    SUPERSEDES = "supersedes"  # 9
    CITES = "cites"  # 10


class EvidenceKind(StrEnum):
    EXPERIMENTAL = "experimental"
    LITERATURE = "literature"
    COMPUTATION = "computation"
    OBSERVATION = "observation"
    NOTE = "note"


class EvidenceRole(StrEnum):
    PRIMARY_SUPPORT = "primary_support"
    CORROBORATING = "corroborating"
    BACKGROUND = "background"
    METHODOLOGY = "methodology"
    NEGATIVE_RESULT = "negative_result"
    HYPOTHESIS = "hypothesis"


class Polarity(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    NEUTRAL = "neutral"


class Confidence(StrEnum):
    LOW = "low"
    MED = "med"
    HIGH = "high"


class CitedAs(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class DecisionStatus(StrEnum):
    DRAFT = "draft"
    COMMITTED = "committed"


class EvidenceTargetKind(StrEnum):
    SCIENTIFIC_OBJECT_REVISION = "scientific_object_revision"
    DECISION = "decision"
    EVIDENCE = "evidence"


# Legal Evidence source kinds (a subset of the registry kinds): the frozen source
# set is run/session/artifact/literature/external reference or a revision.
LEGAL_EVIDENCE_SOURCE_KINDS = frozenset(
    {
        ResourceKind.RUN_REFERENCE,
        ResourceKind.SESSION_REFERENCE,
        ResourceKind.ARTIFACT_REFERENCE,
        ResourceKind.LITERATURE_REFERENCE,
        ResourceKind.EXTERNAL_REFERENCE,
        ResourceKind.SCIENTIFIC_OBJECT_REVISION,
    }
)


class CapabilityKind(StrEnum):
    """Core-owned closed capability vocabulary (ADR-0012). Core categorizes
    realized capabilities by these kinds; it never parses provider vocabulary.

    Only the two realized capability kinds exist. Any additional kind is added
    only when a concrete, provider-neutral use case forces it — never pre-
    projected as speculative vocabulary."""

    COMPUTE = "compute"
    ARTIFACT_RESOLUTION = "artifact_resolution"


class ProviderRuntimeHealth(StrEnum):
    """Per-provider, driver-level, actor-independent runtime health. This is the
    only per-provider state the registry tracks (never persisted)."""

    READY = "ready"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"


class CapabilityAvailability(StrEnum):
    """Actor/Project derived projection of capability availability. Derived per
    query, never stored (ADR-0012 / PROVIDER_CAPABILITIES)."""

    AVAILABLE = "available"
    CREDENTIAL_MISSING = "credential_missing"
    NOT_AUTHORIZED = "not_authorized"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


class CapabilityErrorKind(StrEnum):
    """Core-owned closed failure vocabulary translated at the provider
    invocation boundary (TODO.md section 10). Drivers translate their
    transport/API failures into exactly one of these kinds; they never expose
    raw HTTP-library exceptions or provider stack traces."""

    AUTH = "auth"
    NOT_FOUND = "not_found"
    INVALID_PARAM = "invalid_param"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    NETWORK = "network"
    UNKNOWN = "unknown"


class ToolSource(StrEnum):
    """Where a Project Tool descriptor originates (TODO.md section 3): a REvoLab
    typed domain/analysis operation (`domain`) or an available Provider
    capability (`provider`). Not secret-bearing; presentation metadata only."""

    DOMAIN = "domain"
    PROVIDER = "provider"


class AgentToolAutonomy(StrEnum):
    """The executable agent-autonomy classification (ADR-0013 authority matrix):

    - `automatic`: safe reads/proposals the Agent may perform without further
      authorization;
    - `policy`: a typed domain mutation gated by project policy (the current
      policy is `owner`/`member` membership);
    - `explicit_action`: an operation with side effects or truth-promotion that
      is never auto-executed by the Agent loop — it requires an explicit,
      authorized Actor action (e.g. committing a Decision, submitting compute).

    This is the SINGLE canonical authority truth for Project Tools. The
    `never_agent` class (membership, credential, destructive operations) is
    represented by never projecting such an operation as a Tool at all — it is
    not a fourth wire value.
    """

    AUTOMATIC = "automatic"
    POLICY = "policy"
    EXPLICIT_ACTION = "explicit_action"


class ToolExecutionClass(StrEnum):
    """Where a Project Tool actually runs (TODO.md section 3/6).

    `local` tools are bounded, closed, in-process REvoLab operations (the Local
    Tool Runtime). `remote` tools are projected Provider capabilities whose
    execution truth stays with the external system (REvoCompute); they are
    invoked through the existing capability endpoints, never the local runtime.
    """

    LOCAL = "local"
    REMOTE = "remote"


class ToolSideEffectClass(StrEnum):
    """What a successful Project Tool invocation may durably produce (TODO.md
    section 3/8). Persistence semantics are declared by the tool and enforced by
    the runtime — a ToolResult is never promoted to project truth automatically.

    `creates_derived_result` covers any durable NON-truth resource the tool
    persists through a typed domain operation — locally derived analysis
    artifacts (CSV/plot spec), or harvested external reference identity cards.
    `domain_mutation` is a neutral typed-domain write (Evidence, Decision
    draft/commit). Truth promotion is expressed by the Decision lifecycle
    (draft → committed) and `AgentToolAutonomy` (commit is `explicit_action`),
    NOT by this side-effect class — a Decision DRAFT is never "project truth"."""

    READ_ONLY = "read_only"
    CREATES_DERIVED_RESULT = "creates_derived_result"
    DOMAIN_MUTATION = "domain_mutation"
    EXTERNAL_ACTION = "external_action"


class ToolResultKind(StrEnum):
    """The durable kind of a ToolResult: what the invocation produced (TODO.md
    section 7). `ephemeral` results are never persisted; every other kind names a
    durable REvoLab resource recorded through a typed domain operation.

    Only producing kinds are present: `ephemeral`, `artifact` (persisted derived
    result), `evidence`, and `decision`."""

    EPHEMERAL = "ephemeral"
    ARTIFACT = "artifact"
    EVIDENCE = "evidence"
    DECISION = "decision"
