# Implementation Roadmap

> **Status: Accepted** (merged into `main`). Staged,
> dependency-ordered vertical slices each with a goal,
> owned domains, acceptance evidence, and explicit non-goals. Also classifies the
> current bootstrap into keep / revise / remove / defer.

## Bootstrap classification

The existing repository is a **prototype and source of evidence**, not a spec to
preserve. No backward compatibility is required. Classify every current piece:

### KEEP
- Global UUID identity on all records.
- Provider/external-id pairing + validation (normalize into a first-class
  `ExternalIdentity` registry + series↔external-identity mapping).
- Relation self-reference rejection.
- Relation identity + uniqueness/supersession **deferred to the Phase-1 executable
  spike** (see `SCIENTIFIC_GRAPH.md`); no `UNIQUE(project, source, target,
  relation_type)` is presumed on relations.
- `DecisionEvidence` composite primary key.
- Relational logical graph over PostgreSQL (ADR-0003).
- Alembic drift-check against **both** SQLite (dev) and PostgreSQL (CI).
- The application-scoped driver registry + `importlib.metadata` discovery + explicit
  start/stop with rollback (after the ADR-0012 revisions).
- The generated-contract intent (`frontend/src/contracts/README.md`) — to be made
  executable in Phase 2.

### REVISE
- **Blanket `cascade="all, delete-orphan"`** → explicit soft-archive + link
  semantics; Project owns links, not objects; no destructive delete-orphan.
- **`parent_id` self-tree as the scientific structure** → split into project-local
  organization (ProjectResourceLink folder/container annotation) vs scientific
  relations (global provenance + project knowledge edges).
- **Single `ScientificObject` table + `metadata_json`** → base record + typed
  extension tables; no unvalidated JSON payload.
- **`Evidence.provider`+`external_id` + `evidence_type=RUN|ARTIFACT`** → split into
  distinct `RunReference`/`ArtifactReference`/`LiteratureReference` + Evidence claim,
  keyed by an identity `authority` (not a resolver/provider).
- **`POST /decisions` creating truth immediately** → explicit `draft → committed`
  promotion.
- **`GET /projects/{id}` returning the whole graph** → project metadata + paginated
  collections + bounded graph query.
- **Free-string `relation_type`/`evidence_type`/`status`** → PostgreSQL enums/CHECK.
- **Driver lifecycle 5 states exposed** → collapse to 2 domain-visible states
  (REGISTERED/READY); distinguish in-process startup from the Provider/Capability
  domain model; add the credential concept.
- **Frontend hardcoded type strings** → generated TypeScript client.

### REMOVE
- The checked-in `revolab.db` SQLite artifact at repo root (dev-only, not truth).
- Hardcoded CORS `localhost:5173` as an architecture assumption (make configurable).

### DEFER (explicit non-goals of this phase)
- Real authentication / Identity tables + login.
- Real REvoCompute / REvoDesign / OpenBio drivers.
- Agent chat UI.
- Production deployment.
- Graph database, workflow engine, event sourcing, approval infrastructure.

### Explicitly deferred with reasons (DoD-18: nothing silently postponed)

Every architectural question is either decided here or explicitly parked with the
reason and the phase that resolves it:

| Question | Status now | Resolved in |
|---|---|---|
| ScientificObject typing, versioning, lifecycle | **Decided** (ADR-0008/0009) | Phase 1 |
| Evidence/reference/provenance model | **Decided** (ADR-0010) | Phase 1 |
| Decision promotion | **Decided** (ADR-0011) | Phase 1 |
| Provider/Driver/Capability/Credential | **Decided** (ADR-0012) | Phase 3 (credential binding), Phase 4/7 (drivers) |
| Agent context + authority | **Decided** (ADR-0013) | Phase 6 |
| Generated API/frontend contract | **Decided** (ADR-0014) | Phase 2 |
| **RelationType closed enum** | **Decided** — canonical list in `SCIENTIFIC_GRAPH.md` | Phase 1 |
| **Edge endpoint semantics (Series vs Revision)** | **Decided** — conceptual semantic edges address Series; content/provenance edges address Revision; Decision targets are explicitly typed (`SCIENTIFIC_GRAPH.md`) | Phase 1 |
| **Edge ownership & lifecycle** | **Decided** — `GlobalProvenanceEdge` (#1–7, Evidence/Provenance-owned, never Project-archived) vs `ProjectKnowledgeEdge` (#8–10, Knowledge/Decision-owned, archived with Decision/Evidence); `generated_by` is derived, not persisted (`SCIENTIFIC_GRAPH.md`) | Phase 1 |
| **ProjectResourceLink referential identity** | **Decided** — thin `GlobalResourceRegistry(resource_id PK, resource_kind)` spine; `ProjectResourceLink.resource_id` FKs to it and **does not store `resource_kind`**; every concrete global table PK is both PK and FK to the registry (single-column FK, no polymorphic FK) (`COLLABORATION_IDENTITY.md`, ADR-0008) | Phase 1 |
| **Relation physical schema** (one table shared by the two edge kinds vs edge-family tables; uniqueness/supersession key) | **Deferred to the Phase-1 executable spike** — see `SCIENTIFIC_GRAPH.md` (the single source for the logical graph contract; do NOT freeze the physical shape in PR1 — ownership/lifecycle are already frozen) | Phase 1 |
| **Evidence kind/role enums** (incl. `hypothesis` role) | **Decided** — in `EVIDENCE_PROVENANCE.md` | Phase 1 |
| **Actor / identity persistence** | **Shape decided** (opaque UUID Actor; `ProjectMembership`/`ResourceStewardship`/`MutationGrant` in `COLLABORATION_IDENTITY.md`); the authority-substrate tables are built in Phase 1, credential binding in Phase 3 — not reopened | Phase 1 (authority substrate), Phase 3 (credential binding) |
| **OpenBio cache semantics** | **Decided** — `ExternalReference` = `ExternalIdentity` FK + resolver/cache metadata (checksum, as_of), never a snapshot copy | deferred external-knowledge phase (not Phase 7) |
| **SessionReference / ExternalReference node types; import via `imported_as` edge (no separate ImportRecord node)** | **Decided** in `SCIENTIFIC_GRAPH.md` / `EVIDENCE_PROVENANCE.md` | Phase 1 (implemented) / deferred external-knowledge phase |

No architectural question is silently postponed: where an item is deferred it is
explicitly named, with its resolved-in phase.

---

## Phases (vertical slices, dependency-ordered)

The phase order derives from architectural dependencies: context core first, then a
real frontend/API slice, then an **Identity foundation** (the Provider/Capability
domain depends on Identity for credential presence), then integrations, then
collaboration/sharing and the agent.

### Phase 1 — Scientific context core + minimal authority substrate

- **Goal:** replace the bootstrap's accidental architecture with the proposed domain
  model — typed object spine + extension tables, project-local organization / global
  relation split, the `GlobalResourceRegistry` referential spine, distinct reference
  nodes, decision promotion, soft-archive lifecycle — **and the minimal authority
  substrate the scientific commands already depend on**, so no command is built on a
  fake identity.
- **Owned domains:** Project, Scientific Object, Evidence/Provenance, Knowledge (Core),
  Identity/Collaboration (minimal authority substrate).
- **Vertical slice:**
  - **Authority substrate:** `Actor` (opaque UUID), `ProjectMembership`
    (owner/member/viewer), `ResourceStewardship` (grant/transfer/freeze), and
    `MutationGrant` issuance at the command boundary — **not** authentication.
  - **Scientific context:** models + migrations + domain services + domain-command API
    for objects/relations/evidence/decisions/promotion, the object-detail aggregate,
    bounded graph query, and a minimal `ContentStore` (fsspec local backend) so uploaded
    artifacts (`authority = revolab`) share the ArtifactReference abstraction.
- **Acceptance evidence:** Alembic drift on SQLite + PG; tests for: atomic create =
  series + initial revision + `ProjectResourceLink` + `ResourceStewardship` (no
  pre-existing grant); `can_mutate` works for owner/member in the steward Project and
  denies viewer/non-steward/tombstoned Project; a mutation command rejects a missing or
  stale `MutationGrant`; no blanket CASCADE (deleting a Project preserves objects);
  object revisioning; decision draft→commit; relationship immutability; typed object
  extension; Evidence freezes once a committed Decision cites it **or** another Evidence
  targets it; stewardship transfer requires project owner + approval; and
  `ContentStore.put/get` returns `checksum`/`size`/`content_type` and never mutates
  stored bytes.
- **Non-goals:** OIDC/login, RBAC engine, public visibility, providers, agent.

### Phase 2 — Real frontend/API vertical slice

- **Goal:** a working REvoLab workspace over the Phase-1 core, driven entirely by
  generated contracts.
- **Owned domains:** Presentation/Workspace.
- **Vertical slice:** generated OpenAPI→TS client (build step) + CI drift gate; the
  workspace IA (Overview / Objects / Evidence / Decisions / Knowledge + context
  inspector); object-detail aggregate UI; provider capability surface (static).
- **Acceptance evidence:** CI drift gate passes (generated == committed); frontend
  consumes zero hand-maintained enums; browser smoke test of the object/evidence/
  decision flows against the real API (no fixtures).
- **Non-goals:** providers, agent, collaboration.

### Phase 3 — Provider identity foundation (credential binding + secret store + availability)

- **Goal:** add the **provider-side** identity work that Phase 4 integrations consume —
  `Actor`/`ProjectMembership`/`ResourceStewardship`/`MutationGrant` already exist from
  Phase 1; this phase adds credentials and availability.
- **Owned domains:** Identity/Collaboration (credential binding), Provider/Capability
  (availability query).
- **Vertical slice:** `ExternalProviderCredentialBinding`
  (`actor_id, provider_key, kind, secret_ref`), the Secret-store material boundary, and
  `CapabilityAvailability(actor, project)` as a derived query. Core holds only the
  non-secret binding; the `CredentialLease` materializes secrets only in the driver
  transport at call time.
- **Acceptance evidence:** an Actor can hold a credential binding; `has_credential(actor,
  provider, kind)` is a derived query; no secret material appears in a Core table or log;
  the availability formula `driver READY AND credential present for the calling Actor
  AND project policy permits` is queryable with a stub driver.
- **Non-goals:** OIDC/login, RBAC engine, per-object ACL, public visibility (all still
  deferred).

### Phase 4 — REvoCompute integration

- **Goal:** link external executions as durable references with no execution-state
  copy, now that Actor-scoped credential presence exists (Phase 3).
- **Owned domains:** Provider/Capability, Evidence/Provenance, Identity (credential
  presence).
- **Vertical slice:** `ComputeCapability` + `ArtifactResolutionCapability`;
  `RunReference`/`ArtifactReference` creation and resolve-on-demand; the documented
  REvoCompute narrow scope/reference contract with `InputBinding` inputs.
- **Acceptance evidence:** a fake REvoCompute provider test proves submit→reference→
  resolve with credential presence gate and typed failure; an ArtifactReference can be
  consumed directly by a run (`consumed_as_input_by` source `ArtifactReference`) without
  a forced import; a test proves Core holds no provider vocabulary; provenance stays
  traversable when the provider goes unreachable.
- **Non-goals:** embedding REvoCompute state; hot-unloading; full collaboration.

### Phase 5 — Project collaboration & sharing

- **Goal:** cross-project sharing by membership/link, never copy; the
  revision⇒series visibility closure and the project-context write invariant are
  exercised for real.
- **Owned domains:** Identity/Collaboration, Project.
- **Vertical slice:** membership/roles are already in the Phase-1 authority substrate;
  this phase adds project visibility (private/shared), sharing a global object across
  projects via membership, and authorization-aware access with the revision⇒series
  closure.
- **Acceptance evidence:** two projects share one object without duplication; linking a
  revision makes its series visible but exposes no sibling revisions; every
  project-scoped write rejects endpoints not already in the Project's link set; deleting
  one Project leaves the shared object + provenance intact; access boundaries enforce
  membership/role.
- **Non-goals:** RBAC engine, per-object ACL, public visibility, real login.

### Phase 6 — Agent context & tools

- **Goal:** the Agent as consumer, with the promotion boundary enforced.
- **Owned domains:** Agent Context.
- **Vertical slice:** `/context` assembly (ContextSelection/ContextBuilder); typed
  ToolCatalog from provider + domain schemas; skills populate; the
  propose → commit loop (a Decision draft becomes truth only via the promotion gate)
  with the authority matrix.
- **Acceptance evidence:** an agent can read bounded context, propose a Decision, and
  have it remain non-truth until committed; a test proves no agent path performs a raw
  write; chat history never appears in the graph.
- **Non-goals:** chat UI polish, RAG.

### Phase 7 — Project Tool Harness & Analysis Runtime

> **Architectural correction (supersedes the earlier REvoDesign/OpenBio Phase 7).**
> REvoLab is a project-centered scientific Harness; REvoCompute is its primary
> heavyweight execution backend. REvoDesign is a method-specific interactive
> design application, and OpenBio is a design reference — neither is a REvoLab
> backend. Phase 7 therefore does **not** integrate them.

- **Goal:** make **Tool a first-class Project Harness abstraction** (the explicit
  execution surface of the Project) while keeping Driver/Capability as
  implementation details behind external boundaries; build the smallest closed
  typed **Local Tool Runtime** for bounded lightweight Project analysis; project
  the existing REvoCompute path through the same ToolCatalog rather than
  duplicating it.
- **Owned domains:** Project Tool Harness (new), Provider/Capability (projection),
  Evidence/Provenance + Knowledge (result/promotion semantics), Presentation.
- **Vertical slice:** a canonical Tool schema consumed by both the human workspace
  and the Agent; local analysis tools (`artifact.inspect`, `table.describe`,
  `table.select`, `plot.xy`); a Local Tool Runtime (lookup → schema validation →
  authorization → typed implementation → typed output → `ToolResult`); local
  tools plus remote REvoCompute tools in one ToolCatalog; a REvoCompute-produced
  artifact analyzed by a local REvoLab Tool.
- **Acceptance evidence:** one real local-analysis flow (Artifact → table tool →
  derived result → optional persisted artifact → Evidence → Decision draft) and
  one remote flow (REvoCompute submit → ArtifactReference → local analysis Tool),
  with explicit ephemeral-vs-persisted result semantics and the Evidence/Decision
  promotion boundary intact. REvoCompute remains the sole heavyweight backend and
  no arbitrary Python/shell/SQL/filesystem/HTTP execution surface exists.
- **Non-goals:** REvoDesign/OpenBio integration, authentication, notebooks,
  arbitrary execution, workflow engines, LLM infrastructure, background-job
  queues, a full scientific-analysis suite.

---

## Final architecture invariants (enter CLAUDE.md)

These are derived from the whole design; they are the concisely load-bearing rules:

> 1. **Project organization is not scientific semantics.** Navigation/folders are
>    UX; scientific meaning lives in typed relations.
> 2. **Project context must not duplicate external execution truth.** References are
>    immutable identity cards; state is resolved through the driver, never copied.
> 3. **Provider vocabulary cannot leak into Core.** Core knows a fixed vocabulary of
>    capability kinds and consumes provider schemas as data.
> 4. **Agent output becomes project truth only through typed, domain-validated
>    operations.** Two gates, never conflated: (a) ALL persistence is a typed domain
>    command (no raw writes); (b) the promotion gate applies ONLY to committing a
>    knowledge assertion (`Decision draft → committed`) — not to ordinary
>    object/evidence creation.
> 5. **Scientific provenance must remain traversable after external systems change.**
>    References are never deleted, only revoked; a provider disappearing never corrupts
>    stored context.
> 6. **Durable identity is an opaque UUID — never a filesystem path or a mutable
>    username; external identity is `(authority, native_id)`, never a resolver/provider.
>    A ScientificObject's conceptual identity (`series_id`) is distinct from its
>    immutable revision identity (`revision_id`); provenance addresses revisions.**
> 7. **Scientific content is immutable once referenced; change is a new version or a
>    superseding record, never in-place.**
> 8. **A Project is a namespace and membership boundary, not the owner of objects;**
>    deleting a Project removes links, never the underlying objects or their
>    provenance. ScientificObjects/references and the global provenance edges (#1–7)
>    are global; the project knowledge edges (#8–10) and Evidence/Decision are
>    project-scoped. Read visibility is not mutation authority: `ProjectResourceLink`
>    grants the read lens; `ResourceStewardship` alone authorizes mutation of a global
>    resource, and global provenance edges are created only by typed authoritative
>    domain operations (never a generic writer).
> 9. **The Agent is a consumer, not an owner — read context → reason → propose →
>    typed tool → domain validation → persisted truth.**
> 10. **Credentials are owned by the credential store; a provider is callable iff its
>     driver is READY and every required credential kind is present for the calling
>     Actor and project policy permits — all queries, never stored truth.**
