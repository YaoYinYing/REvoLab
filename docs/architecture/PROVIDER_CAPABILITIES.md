# Provider & Capability Architecture

> **Status: Proposed — pending human architecture review.** Defines Provider /
> Driver / Capability / Tool / Credential,
> capability discovery, schema-as-data, failure handling, and the REvoCompute /
> REvoDesign / OpenBio integration contracts. Reconciles Subagents B and G.

## Ruling principle

> **REvoLab owns scientific context and relationships. External systems own their
> capabilities and execution truth.**

Core must never grow a branch named `revocompute` / `revodesign` / `openbio`.
Instead Core owns (a) typed capability Protocols, (b) neutral durable references
(Run/Artifact/Literature references), and (c) the import/promotion boundary that
turns external data into project truth. Each integration is a Driver that implements
one or more capability Protocols and keeps provider vocabulary inside itself.

## The five concepts (keep) and one that is integrated elsewhere

| Concept | Owned by | Why keep |
|---|---|---|
| **Provider** | Provider domain | the uniquely-identified external system you point at and bind to |
| **Driver** | Provider domain | the concrete code realizing capabilities; ≥3 real targets |
| **Capability** | Provider domain | the protocol boundary that hides provider vocabulary from Core |
| **Tool** | Agent domain | the typed, agent-facing presentation of a capability |
| **Credential** | Credential store | how a project learns a provider is callable |

- **`CredentialBinding` is NOT introduced** — it is the textbook one-use-case fake
  abstraction. A presence lookup (`has_credential(provider, kind)`) suffices. Add a
  real binding entity only when a second cross-project sharing use case forces it.
- **`ExternalReference` is a stored data shape in the Evidence domain**, not a
  driver concept. The driver side only needs a stable `(authority, native_id)` and
  a resolution interface (see "Authority vs provider" below).

## Authority / namespace vs resolver / provider (identity is not transport)

The reviewer flagged that the general provider architecture fuses *identity authority*
with *access provider*. They are different and must not share one string.

```text
authority / namespace   → who defines the identity: uniprot, pdb, doi, pubmed, ...
resolver / provider     → who you reach it through: openbio, direct-uniprot, revocompute, ...
```

- **Durable external identity is `(authority, native_id)`.** `UniProt:P12345` must
  keep the same durable identity whether it is resolved today through OpenBio or
  tomorrow through a direct UniProt API. Changing the resolver must **never** change
  the identity or invalidate stored references.
- **`ExternalId` and `ExternalReference` carry the `authority`, not the provider.**
  The provider/resolver is the *access* detail (which Driver/Capability can fetch it),
  recorded separately from the durable identity.
- **REvoCompute**: its own run/artifact IDs are special: REvoCompute is *both* the
  authority (it defines those IDs) *and* the provider (it executes them). That is the
  normal case for a compute engine and is fine.
- Public biological sources (UniProt/PDB/DOI/PubMed) are the authority; OpenBio is a
  *resolver/aggregator*, never the authority. This holds even when OpenBio is the only
  resolver currently configured.

## Identity & discovery without learning vocabulary

- **Provider identity:** a stable lowercase slug (`revocompute`, `openbio`), never
  a path/hostname/username. Alongside: display name, description, version, and
  `required_credential_kinds`.
- **Capability identity:** `(provider_key, capability_kind)` where `capability_kind`
  is a **Core-owned closed enum**. This lets Core *categorize* without ever
  *parsing* provider language. Core never matches on anything provider-written.
- **Discovery:** Core asks "give me all capabilities of kind X" and the registry
  filters realized capability instances by the Core enum. Provider-specific terms
  (e.g. "slurm") live behind the Protocol.

## The capability protocols

A provider may expose many capabilities. Concretely:

```python
class Capability(Protocol):            # base
    provider_key: str
    kind: CapabilityKind               # COMPUTE | SEARCH | ARTIFACT_RESOLUTION | DESIGN | INTERACTIVE_HANDOFF

class ComputeCapability(Capability, Protocol):            # REvoCompute (batch)
    def list_task_kinds(self, credential) -> list[TaskKindRef]: ...
    def task_kind_schema(self, kind_id) -> JsonSchema: ...   # schema-as-data
    def submit(self, kind_id, params: dict, credential) -> RunHandle: ...
    def get_run(self, run_id, credential) -> RunView: ...
    def cancel(self, run_id, credential) -> None: ...

class ArtifactResolutionCapability(Capability, Protocol):  # pure read
    def resolve(self, ext_ref, rev=None, credential) -> ArtifactHandle: ...

class SearchCapability(Capability, Protocol):              # OpenBio / knowledge lookup
    def search(self, query, filters=None, credential) -> list[ExternalHit]: ...
    def hit_schema(self) -> JsonSchema: ...

class DesignCapability(Capability, Protocol):              # durable export half
    def open_design(self, object_ref, credential) -> DesignSession: ...
    def export(self, session_id, credential) -> list[ArtifactHandle]: ...
    def list_sessions(self, credential) -> list[DesignSession]: ...

class InteractiveHandoffCapability(Capability, Protocol):  # deep-link / interactive half
    def create_handoff(self, object_ref, return_callback, credential) -> HandoffRef: ...
    def on_return(self, handoff_id) -> HandoffResult: ...
```

**Naming decisions (from the review):**
- `ComputeCapability`, `ArtifactResolutionCapability` — keep.
- `LiteratureSearchCapability` → **renamed `SearchCapability`** (one reader protocol
  covers literature *and* biological-database lookup; avoid 5 near-identical search
  protocols).
- `DesignCapability` — **split into `DesignCapability` (export) + the interactive
  `InteractiveHandoffCapability`**, because REvoDesign is stateful and interactive;
  forcing it into the stateless compute/submit shape is a fake generic abstraction.

## Schema-as-data discovery

- **Per-capability JSON Schema as data, returned verbatim.** Every capability
  method accepting structured params declares its own input/output JSON Schema
  (Draft 2020-12, versioned).
- Core validates provider params **only against the provider's own returned schema**;
  Core assigns zero meaning to the fields.
- The same schemas feed the **frontend** (generated forms), the **Agent**
  (tool-call arg schemas), and **drift detection** — one source, no duplicated
  contracts.

**Invariant:** Core never contains a field name or value specific to a provider.

## Credentials & availability (Actor-contextual)

- The **Credential store owns** `(actor_id, provider_key, credential_kind) →
  secret-ref`. It is opaque to Core; Core cannot read secret values. Credentials are
  **Actor-scoped** (per `COLLABORATION_IDENTITY.md`), not Project-owned.
- Every capability method takes a `credential` handle acquired from the store for the
  **calling actor**; no secret transits Core services or persists in the project
  graph.
- **Availability is Actor-contextual** — not a single Project-wide boolean. Members
  of one Project may have different permissions for the same provider (e.g. only one
  member has an OpenBio commercial key or a REvoCompute privileged-runner grant). It
  is always a **derived query**, never stored:

```text
available(actor, provider) = (driver loaded & READY)
                        AND all(required credential kinds present for this actor)
                        AND project policy permits the requested operation for this actor
```

Because it is derived on demand, a credential revocation or a role change flips
availability with **no migration**.

## How frontend and Agent discover

Both read the **same read-only Provider Catalog, evaluated for the current Actor**:

```
GET /api/providers            -> [{key, name, capabilities:[{kind, describe() schema}],
                                   credential_status(mine) }]      (actor-scoped)
GET /api/providers/{key}/schema/{capability_kind} -> JSON Schema
```

- **Frontend** renders the catalog through the current Actor's lens (shows only what
  the signed-in user can call); generic JSON Schema forms; no capability knowledge in
  the frontend.
- **Agent** materializes the catalog into **Tools** for the current session's Actor:
  for each capability the actor may call, a typed tool whose arg schema = the provider
  JSON Schema and target = `(provider_key, kind, method)`. An unavailable/unpermitted
  provider produces no tool for that actor, so the Agent never proposes an
  unexecutable call.

## Failure, runtime health, and actor availability (three distinct coeffects)

The review flagged that **runtime health** and **caller availability are different
things and must not share one state**. Split them:

**ProviderRuntimeHealth** — per-provider, driver-level, **actor-independent**:

```text
READY | DEGRADED | UNREACHABLE
```

This is the only thing the registry may track per provider (lazily probed). `DEGRADED`
means the driver is loaded but some dependency is unhealthy; `UNREACHABLE` means live
calls fail. There is **no `credential_missing` here** — that is never a provider
property.

**CapabilityAvailability(actor, project)** — a **derived projection, never stored**:

```text
AVAILABLE | CREDENTIAL_MISSING | NOT_AUTHORIZED | PROVIDER_UNAVAILABLE
```

Derived per call/query as:

```text
available(actor, provider, project)
  = (driver RuntimeHealth == READY)
    AND required credential kinds present for this Actor
    AND project policy permits the requested operation for this actor
```

One provider can be `AVAILABLE` for Alice (who holds the credential) and
`CREDENTIAL_MISSING` for Bob (who does not) in the same Project — exactly the case the
single per-provider state could not express. Availability is recomputed on demand, so
credential rotation and role changes take effect with no migration.

**Failure behavior:**
- **Typed `CapabilityError`** with a stable kind enum
  (`AUTH | NOT_FOUND | INVALID_PARAM | PROVIDER_UNAVAILABLE | NETWORK | UNKNOWN`),
  mapped to a stable HTTP error envelope. **No silent fallbacks.**
- When a provider's runtime health becomes **`UNREACHABLE`**: live calls fail with
  `PROVIDER_UNAVAILABLE`; the catalog denotes the provider unavailable to every actor;
  **stored references/evidence are unaffected** — they become "unverifiable", never
  deleted, never re-invalidated. Only *live resolution* is suspended.
- **No hot-unloading** (ADR-0005). An unreachable driver stays loaded and rejects
  calls.

## Critique of ADR-0004 / current `drivers.py`

- **KEEP:** Python `Protocol`; application-scoped registry; `importlib.metadata`
  entry points; explicit sequenced start with rollback; rejection of pluggy/custom
  framework before a real 1:N need.
- **REVISE:** split the single `Driver` Protocol into a thin registration/lifecycle
  `Driver` + per-kind `Capability` Protocols (a Driver *realizes* capabilities);
  replace `capabilities() -> set[str]` with a typed `kind → instance` map; collapse
  the 5 lifecycle states into **2 domain-visible states** (`REGISTERED`, `READY`),
  keeping the rest as internal startup transients that never leak into the API.
  **Add the credential concept** (`required_credential_kinds`, presence probe,
  `credential` arg) — the single most important gap before any real driver is written.
- **REMOVE:** lifecycle-as-domain-knowledge (no driver DB table, no STARTED/STOPPED
  in the API); no hot-unload/reactive machinery.

**Key separation:** *in-process lifecycle* (startup resource ownership, transient)
vs *Provider/Capability/Credential/Tool* (durable domain model the API/frontend/
Agent talk about). The bridge is thin: after `start_all`, the registry projects
realized capabilities + credential presence into the read-only Provider Catalog.

---

# REvoCompute integration contract

REvoLab must **not import REvoCompute internals**. The minimum external contract
REvoLab needs (documented upstream, not implemented here):

```text
list_task_kinds()                     → discover task types (id, name, required_inputs, version)
task_kind_schema(kind)                → JSON Schema for parameters
submit(task_kind, input_refs, params, auth, idempotency_key) → RunReference
run(run_ref, auth)                    → RunStatus (refreshed on demand, never copied)
artifacts(run_ref, auth)              → [ArtifactReference]
resolve_artifact(artifact_ref, auth)  → ArtifactAccess (content, checksum, size, version)
```

**Separate the two truths:**
- REvoCompute canonical mutable execution state (Run/task/status/job) is
  authoritative **only** there.
- REvoLab stores a **RunReference** / **ArtifactReference** — immutable identity
  records, not snapshots. REvoLab never stores REvoCompute task tables or status as
  truth; it keeps only the immutable pin and resolves fresh state through the driver.

**Cross-user/project sharing without the old REvoCompute Project model:** REvoCompute
exposes a **narrow scope/reference API** (grant/deny access to a run/artifact by
immutable ref under an auth context); it does **not** know REvoLab's project
hierarchy. REvoLab Project membership decides who may *reference* a run/artifact; the
authorization to read the underlying bytes stays in REvoCompute. Sharing = sharing a
neutral reference, never a copy.

---

# REvoDesign integration contract

REvoDesign is **interactive and stateful** and is fundamentally different from
REvoCompute. Do NOT force it into `ComputeCapability`.

**Flows:**

```text
REvoLab Structure --open_design--> REvoDesign session
  (user interacts in REvoDesign; REvoLab is NOT polling every frame)
REvoDesign --export--> Structure/Variant/DesignSet
  → REvoLab creates a NEW object + provenance
```

- REvoDesign shares the base `Driver` lifecycle and the durable-reference
  discipline, and implements `DesignCapability` (open/export) + the
  `InteractiveHandoffCapability` (deep-link/open-in-UI).
- **Provenance is captured only at the export/import boundary**: input object →
  session (SessionReference) → ArtifactReference (checksum) → output object, linked
  by `derived_from`/`represents`. The ephemeral interactive timeline is never stored.
- **A durable result** = a content-addressed ArtifactReference, optionally imported
  into a new REvoLab ScientificObject + provenance edges — never the session timeline.

---

# OpenBio / biological knowledge integration

OpenBio is primarily an **external biological knowledge capability**
(PDB/UniProt/PubMed/ChEMBL/…), **not** REvoLab's database.

**Four entry modes — one boundary** (e.g. UniProt P12345):

```text
1. Live external lookup    → transient result, never persisted as project truth
2. Cached external reference → REvoLab stores an ExternalReference: the stable
     identity `(authority, native_id)` (e.g. UniProt/P12345) + bounded, validated
     metadata (label, kind, a content checksum when known). This is a durable identity
     handle with lightweight validated metadata — NOT a copy of the provider's data. It
     honors invariant #2 (no-mutable-external-copy): provider records stay authoritative
     in the provider; no snapshot/full payload is mirrored into REvoLab.
3. Imported scientific object → user/agent imports an ExternalReference into a
     REvoLab Protein object (typed import; reference becomes provenance origin
     via an imported_as edge). Explicit import is the ONLY way an external entity
     becomes a first-class REvoLab object.
4. Project evidence        → the reference/import is linked as Evidence supporting a Decision
```

**Boundary rule:** an entity becomes a REvoLab object **only via explicit import**
(mode 3). Lookup/cache (1, 2) are non-committal; evidence (4) is a relation, not an
object.

**Core records needed (all provider-neutral, each a canonical graph node — see
`SCIENTIFIC_GRAPH.md` node categories):** `ExternalReference`, `RunReference`,
`SessionReference` (REvoDesign interactive session identity card), `ArtifactReference`,
`LiteratureReference`, `Evidence`. **Import provenance is a single mechanism — the
`imported_as` edge** whose payload records the source reference and content
fingerprint; there is **no separate `ImportRecord` node** (a second mechanism would
duplicate import modeling). No OpenBio vocabulary leaks into Core.

Recorded in **ADR-0012**.
