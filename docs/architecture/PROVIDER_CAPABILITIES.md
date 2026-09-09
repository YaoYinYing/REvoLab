# Provider & Capability Architecture

> **Status: Accepted** (merged into `main`). Defines Provider /
> Driver / Capability / Tool / Credential,
> capability discovery, schema-as-data, failure handling, and the REvoCompute
> integration contract. REvoDesign is an unrelated existing product (not a REvoLab
> backend) and OpenBio is a design reference only; neither has an integration
> contract here.

## Ruling principle

> **REvoLab owns scientific context and relationships. External systems own their
> capabilities and execution truth.**

Core must never grow a provider-specific branch. Instead Core owns (a) typed
capability Protocols, (b) neutral durable references (Run/Artifact/Literature
references), and (c) the import boundary that turns external data into project
truth. Each integration is a Driver that implements one or more capability
Protocols and keeps provider vocabulary inside itself. REvoCompute is the only
currently configured external backend; no `revodesign` / `openbio` branch exists.

## The concepts, who owns them (four credential roles)

Cybersecurity-style credential ownership is split across **four distinct roles, each
owning exactly one thing** (this resolves the "credential ownership 3-way stale"
ambiguity the review found):

| Concept | Owns | Why keep |
|---|---|---|
| **Provider** | Provider / Capability domain | the uniquely-identified external system you point at and bind to |
| **Driver** | Provider / Capability domain | the concrete code realizing capabilities; ≥3 real targets |
| **Capability** | Provider / Capability domain | the protocol boundary that hides provider vocabulary from Core |
| **Tool** | Project Tool Harness (consumed by Agent Context + Presentation) | the typed, Project-scoped execution surface; a first-class Harness abstraction behind which a Driver/Capability is only an implementation detail (Phase 7) |
| **Credential binding** (`ExternalProviderCredentialBinding`) | Identity / Collaboration domain | the non-secret row `(actor_id, provider_key, kind, secret_ref)` — "this Actor has a credential of this kind for this provider; the material lives at secret_ref" |
| **Secret material** (API key / token) | Credential / Secret store | the actual secret; never stored in Core, never read back as plaintext by Core |

- **`ExternalProviderCredentialBinding` IS the real, non-secret binding record** owned
  by the **Identity / Collaboration domain**. It is **not** a fake or made-up
  abstraction, and it is **not** a pass-through. It is the durable row that says *"this
  Actor has a credential of this kind for this provider; the material lives at
  `secret_ref`."* The four roles each own one thing:
  1. **Identity / Collaboration domain** owns the **binding** — the non-secret row
     above.
  2. **Credential / Secret store** owns the **secret material** (the actual API key /
     token). It is **never stored in Core** and **never read back as plaintext by
     Core**; `secret_ref` is only a locator / opaque handle.
  3. **Provider / Capability domain** owns the provider / driver / capability and
     **consumes the credential as an opaque handle**. It never owns or materializes
     the secret itself; it uses the binding's handle to retrieve material **on-demand
     through the Credential store**.
  4. **Project Tool Harness domain** owns the **Tool projection** (the canonical
     `ToolCatalog` the Agent and the human workspace both consume) — **not** the
     credential, and **not** the capability/driver.
- **`ExternalReference` is a stored data shape in the Evidence domain**, not a
  driver concept. The driver side only needs a stable `(authority, native_id)` and
  a resolution interface (see "Authority vs provider" below).

## Authority / namespace vs resolver / provider (identity is not transport)

The reviewer flagged that the general provider architecture fuses *identity authority*
with *access provider*. They are different and must not share one string.

```text
authority / namespace   → who defines the identity: uniprot, pdb, doi, pubmed, ...
resolver / provider     → who you reach it through: an aggregator, a direct API, an engine
```

- **Durable external identity is `(authority, native_id)`.** `UniProt:P12345` must
  keep the same durable identity whether it is resolved today through one resolver or
  tomorrow through a direct UniProt API. Changing the resolver must **never** change
  the identity or invalidate stored references.
- **`ExternalIdentity` is the single `(authority, native_id)` registry** (owned by the
  Scientific Object domain, `UNIQUE(authority, native_id)`). `ExternalReference` does
  **not** re-store the identity; it carries an `external_identity_id` FK + resolver/cache
  metadata. The provider/resolver is the *access* detail (which Driver/Capability can
  fetch it), recorded separately from the durable identity.
- **REvoCompute**: its own run/artifact IDs are special: REvoCompute is *both* the
  authority (it defines those IDs) *and* the provider (it executes them). That is the
  normal case for a compute engine and is fine.
- Public biological sources (UniProt/PDB/DOI/PubMed) are the authority; a resolver
  or aggregator is **never** the authority.

## Identity & discovery without learning vocabulary

- **Provider identity:** a stable lowercase slug (`revocompute`), never
  a path/hostname/username. Alongside: display name, description, version, and
  `required_credential_kinds`.
- **Capability identity:** `(provider_key, capability_kind)` where `capability_kind`
  is a **Core-owned closed enum**. This lets Core *categorize* without ever
  *parsing* provider language. Core never matches on anything provider-written.
- **Discovery:** Core asks "give me all capabilities of kind X" and the registry
  filters realized capability instances by the Core enum. Provider-specific terms
  (e.g. "slurm") live behind the Protocol.

## The capability protocols

A provider may expose one or more of the **realized** capabilities. Only the
protocols with a concrete provider-neutral use case exist; any unrealized
capability kind is deliberately absent until forced by a real provider.

```python
class Capability(Protocol):            # base
    provider_key: str
    kind: CapabilityKind               # COMPUTE | ARTIFACT_RESOLUTION

class ComputeCapability(Capability, Protocol):            # REvoCompute (batch)
    def list_task_kinds(self, credentials: CredentialLease) -> list[TaskKindRef]: ...
    def task_kind_schema(self, kind_id) -> JsonSchema: ...   # schema-as-data
    def submit(self, kind_id, inputs: list[InputBinding],
               params: dict, credentials: CredentialLease) -> RunHandle: ...
    def get_run(self, run_id, credentials: CredentialLease) -> RunView: ...
    def cancel(self, run_id, credentials: CredentialLease) -> None: ...

class InputBinding:                                       # REvoLab-neutral resource ref
    kind: ScientificObjectRevision | ArtifactReference    # Canonical graph edge #5
    resource_id: UUID                                      # resolved via GlobalResourceRegistry
    role: str | None                                       # provider schema names the role

class ArtifactResolutionCapability(Capability, Protocol):  # pure read
    def resolve(self, ext_ref, rev=None, credentials: CredentialLease) -> ArtifactHandle: ...
```

`ComputeCapability` and `ArtifactResolutionCapability` are the only realized
protocols (both implemented by the REvoCompute driver).

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

- The **binding** — `ExternalProviderCredentialBinding(actor_id, provider_key, kind,
  secret_ref)` — is a **non-secret** record owned by the **Identity / Collaboration
  domain**: "this Actor has a credential of this kind for this provider." The **secret
  material** it points at is owned by the **Credential / Secret store**: opaque to
  Core, never read back as plaintext. Credentials are **Actor-scoped** (per
  `COLLABORATION_IDENTITY.md`), not Project-owned.
- **Last-mile materialization is an explicit lease (round 5), never a raw `secret_ref`
  or raw bytes in the Driver's hands by default.** The flow:

  ```text
  Capability call
      ↓
  InvocationContext(actor_id, project_id)
      ↓
  Provider invocation layer  →  builds an ephemeral CredentialLease / SecretAccessor
      ↓                          (no secret_ref, no secret bytes leave the layer)
  Driver transport            →  reads ONE credential per kind via:
                                  credentials.get("api_key")
                                  credentials.get("organization_token")
  ```

  The lease supports the provider's **plural** `required_credential_kinds`; it is
  **ephemeral** — in-memory for the call, never persisted, never logged, never
  returned to Core. Application/Core code never sees `secret_ref` or secret bytes.
  (If a provider *must* stay fully secret-transparent, the Secret store would have to
  do request signing/proxying itself; that is a different architecture — the chosen
  model here is the ephemeral lease, not the signing proxy.)
- **Availability is Actor-contextual** — not a single Project-wide boolean. Members
  of one Project may have different permissions for the same provider (e.g. only
  one member holds the REvoCompute privileged-runner credential). It is always a
  **derived query**, never stored:

```text
available(actor, provider, project) = (driver RuntimeHealth == READY)
                        AND all(required credential kinds present for this actor)
                        AND project policy permits the requested operation for this actor
```

Because it is derived on demand, a credential revocation or a role change flips
availability with **no migration**.

## How frontend and Agent discover

Both read the **same read-only Provider Catalog, evaluated for the current Actor**:

```
GET /api/projects/{project_id}/providers            -> [{key, name, capabilities:[{kind, describe() schema}],
                                                        credential_status(mine) }]  (Actor + Project lens)
GET /api/projects/{project_id}/providers/{key}/schema/{capability_kind} -> JSON Schema
```

Bare `/api/providers` exists only as an internal/admin canonical surface; the ordinary
workspace surface is project-scoped (see `WORKSPACE_INFORMATION_ARCHITECTURE.md`).

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
  `credentials: CredentialLease` arg) — the single most important gap before any real
  driver is written.
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
list_task_kinds()                     → discover task types (id, name, inputs schema, version)
task_kind_schema(kind)                → JSON Schema for parameters
submit(task_kind, inputs: [InputBinding], params, auth, idempotency_key) → RunReference
run(run_ref, auth)                    → RunStatus (refreshed on demand, never copied)
artifacts(run_ref, auth)              → [ArtifactReference]
resolve_artifact(artifact_ref, auth)  → ArtifactAccess (content, checksum, size, version)
```

`InputBinding` is REvoLab-neutral: `kind ∈ {ScientificObjectRevision, ArtifactReference}`
plus a `resource_id` (a `GlobalResourceRegistry.resource_id`) and an optional `role`.
Provider-specific input vocabulary (e.g. "this is the candidate variant vs the reference
structure") is expressed through the task-kind input schema (schema-as-data), never in
Core. The run's inputs are persisted on the REvoLab side only as the
`consumed_as_input_by` edge — the driver maps each `InputBinding` to the provider's own
reference at submit time.

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

# REvoDesign / OpenBio are not integration targets

REvoDesign is an **unrelated existing product** — a method-specific, PyMOL-heavy
enzyme-design application — and is not a REvoLab backend. OpenBio is a **design
reference only**: its product ideas may inform REvoLab, but REvoLab does not
integrate with it. No design/search capability, no unrelated-provider driver, and
no `revodesign` / `openbio` branch exists in Core.

The provider-neutral import boundary those deferred directions would have needed —
`ExternalIdentity` (`(authority, native_id)` registry) + `ExternalReference`
(identity FK + resolver/cache metadata) + the `imported_as` edge — is already
owned by the Scientific Object / Evidence domains and documented in
`SCIENTIFIC_GRAPH.md` and `EVIDENCE_PROVENANCE.md`; it exists independent of any
particular provider and is not expanded here.

Recorded in **ADR-0012** (provider/capability vocabulary) and the Phase 7 roadmap
correction in `IMPLEMENTATION_ROADMAP.md`.
