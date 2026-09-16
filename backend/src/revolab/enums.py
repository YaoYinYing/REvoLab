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

    Only realized capability kinds exist. Any additional kind is added only when a
    concrete, provider-neutral use case forces it — never pre-projected as
    speculative vocabulary. `LITERATURE_DISCOVERY` was added by Phase 13 because a
    real provider (NCBI PubMed) now realizes it."""

    COMPUTE = "compute"
    ARTIFACT_RESOLUTION = "artifact_resolution"
    LITERATURE_DISCOVERY = "literature_discovery"


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


class AgentTerminationReason(StrEnum):
    """Why one bounded Agent turn ended. A bound hit is a typed, user-visible
    terminal result, never a silent continuation."""

    FINAL_RESPONSE = "final_response"
    MAX_MODEL_TURNS = "max_model_turns"
    MAX_TOOL_CALLS = "max_tool_calls"
    TOTAL_DURATION = "total_duration"
    MODEL_UNAVAILABLE = "model_unavailable"


class AgentToolCallStatus(StrEnum):
    """Per-tool-call outcome inside an Agent turn: executed through the canonical
    runtime (`completed`), converted to a PendingAction (`pending`), refused /
    failed closed (`failed`), or skipped by a per-turn budget (`skipped`)."""

    COMPLETED = "completed"
    PENDING = "pending"
    FAILED = "failed"
    SKIPPED = "skipped"


class ActionRequestStatus(StrEnum):
    """The durable lifecycle of one Agent-proposed explicit action (ADR-0017).

    An Action Request records PROPOSED INTENT, never authority: `pending` is not
    permission, and every execution re-derives current authority from canonical
    state. `executing` is a durable one-shot claim (not a process-memory flag);
    `succeeded` / `failed` / `ambiguous` / `rejected` are terminal. `ambiguous` is
    the honest representation of an external side effect whose outcome cannot be
    confirmed — it is never auto-retried.
    """

    PENDING = "pending"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"
    REJECTED = "rejected"

    @property
    def is_terminal(self) -> bool:
        return self in _ACTION_TERMINAL_STATUSES


# The frozen terminal set: once an action reaches one of these it can never
# silently execute again (a second desired attempt is a NEW Action Request).
_ACTION_TERMINAL_STATUSES = frozenset(
    {
        ActionRequestStatus.SUCCEEDED,
        ActionRequestStatus.FAILED,
        ActionRequestStatus.AMBIGUOUS,
        ActionRequestStatus.REJECTED,
    }
)


class SearchScope(StrEnum):
    """Which authorization-scoped corpus a Project search reads (Phase 12).

    `PROJECT_SHARED` is Project-shared context readable by every member through
    the ordinary Project read lens. `MY_CONVERSATIONS` is the calling Actor's OWN
    durable working memory (never another member's). `ALL` is the explicit union
    of the two, offered only to the human workspace; there is deliberately no
    "everyone's conversations" scope.

    The Agent-facing `project.search` Tool never accepts a scope at all: it is
    fixed to `PROJECT_SHARED`, so private working memory can never be requested
    through the Agent boundary.
    """

    PROJECT_SHARED = "project_shared"
    MY_CONVERSATIONS = "my_conversations"
    ALL = "all"


class SearchTargetKind(StrEnum):
    """The retrieval/presentation classifier of one SearchHit (Phase 12).

    This is deliberately NOT `ResourceKind`: it also names Project-local,
    non-global targets (Note, Conversation), while `ResourceKind` stays the
    global-resource registry vocabulary. It is a read-projection label pointing
    at a canonical identity that an EXISTING domain already owns — it grants no
    ownership and creates no second scientific model.

    Only kinds that actually have searchable canonical fields and a workspace
    navigation target exist here.
    """

    SCIENTIFIC_OBJECT_SERIES = "scientific_object_series"
    EVIDENCE = "evidence"
    DECISION = "decision"
    NOTE = "note"
    RUN_REFERENCE = "run_reference"
    ARTIFACT_REFERENCE = "artifact_reference"
    LITERATURE_REFERENCE = "literature_reference"
    EXTERNAL_REFERENCE = "external_reference"
    CONVERSATION = "conversation"


# Which target kinds belong to which scope. `CONVERSATION` is the ONLY
# Actor-private kind and is therefore never part of the Project-shared corpus.
PROJECT_SHARED_TARGET_KINDS = frozenset(
    kind for kind in SearchTargetKind if kind is not SearchTargetKind.CONVERSATION
)
MY_CONVERSATION_TARGET_KINDS = frozenset({SearchTargetKind.CONVERSATION})


class SearchMatchedField(StrEnum):
    """Which canonical field produced the hit's snippet (presentation metadata).

    Purely descriptive: it is never a relevance score, a confidence, or an
    authorization property. It lets the workspace say *why* a result matched
    without inventing a second scientific vocabulary.
    """

    NAME = "name"
    TITLE = "title"
    DESCRIPTION = "description"
    BODY = "body"
    LABEL = "label"
    INTERPRETATION = "interpretation"
    SCOPE = "scope"
    STATEMENT = "statement"
    NEXT_ACTION = "next_action"
    IDENTIFIER = "identifier"
    CHECKSUM = "checksum"
    TYPE = "type"
    MESSAGE = "message"


class ConversationRole(StrEnum):
    """The only two durable roles of a persisted conversation message. A stored
    transcript is conversational working memory: `user` text and `assistant`
    text are BOTH untrusted conversational data, never system authority.
    Tool results are not stored as their own role — they survive only inside a
    bounded inert summary attached to the assistant message that produced them."""

    USER = "user"
    ASSISTANT = "assistant"
