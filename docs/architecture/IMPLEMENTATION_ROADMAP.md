# Implementation Roadmap

> **Status:** Accepted. Staged, dependency-ordered vertical slices each with a goal,
> owned domains, acceptance evidence, and explicit non-goals. Also classifies the
> current bootstrap into keep / revise / remove / defer.

## Bootstrap classification

The existing repository is a **prototype and source of evidence**, not a spec to
preserve. No backward compatibility is required. Classify every current piece:

### KEEP
- Global UUID identity on all records.
- Provider/external-id pairing + validation (normalize into a first-class
  `ExternalId` registry).
- Relation self-reference rejection.
- Unique `(project, source, target, relation_type)` on relations.
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
- **`parent_id` self-tree as the scientific structure** → split into organization
  (folders/membership) vs scientific relations.
- **Single `ScientificObject` table + `metadata_json`** → base record + typed
  extension tables; no unvalidated JSON payload.
- **`Evidence.provider`+`external_id` + `evidence_type=RUN|ARTIFACT`** → split into
  distinct `RunReference`/`ArtifactReference`/`LiteratureReference` + Evidence claim.
- **`POST /decisions` creating truth immediately** → explicit `proposed → committed`
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
| Provider/Driver/Capability/Credential | **Decided** (ADR-0012) | Phase 3/6 |
| Agent context + authority | **Decided** (ADR-0013) | Phase 5 |
| Generated API/frontend contract | **Decided** (ADR-0014) | Phase 2 |
| **RelationType closed enum** | **Decided** — canonical list in `SCIENTIFIC_GRAPH.md` | Phase 1 |
| **Generic Relation target type** (poly-target for Evidence/Decision edges) | **Decided** — nullable node-type discriminator + per-kind FKs | Phase 1 |
| **Evidence kind/role enums** (incl. `hypothesis` role) | **Decided** — in `EVIDENCE_PROVENANCE.md` | Phase 1 |
| **Actor / identity persistence** | **Shape decided** (opaque UUID Actor; auth identity/membership/role/credential separation in `COLLABORATION_IDENTITY.md`); tables built in Phase 4, not reopened | Phase 4 |
| **OpenBio cache semantics** | **Decided** — ExternalReference = identity + bounded validated metadata, never a snapshot copy | Phase 6 |
| **SessionReference / ExternalReference node types; import via `imported_as` edge (no separate ImportRecord node)** | **Decided** in `SCIENTIFIC_GRAPH.md` / `EVIDENCE_PROVENANCE.md` | Phase 1/6 |

No architectural question is silently postponed: where an item is deferred it is
explicitly named, with its resolved-in phase.

---

## Phases (vertical slices, dependency-ordered)

The phase order derives from architectural dependencies: context core first, then a
real frontend/API slice, then integrations, then collaboration and agent.

### Phase 1 — Scientific context core

- **Goal:** replace the bootstrap's accidental architecture with the accepted domain
  model: typed object spine + extension tables, organization/relation split,
  distinct reference nodes, decision promotion, soft-archive lifecycle, explicit
  Project-membership ownership.
- **Owned domains:** Project, Scientific Object, Evidence/Provenance, Knowledge (
  Core), Identity (ownership boundary only).
- **Vertical slice:** models + migrations + domain services + domain-command API for
  objects/relations/evidence/decisions/promotion, with the object-detail aggregate and
  bounded graph query.
- **Acceptance evidence:** Alembic drift on SQLite + PG; tests for: no blanket CASCADE
  (deleting a Project preserves objects), object versioning, decision proposed→commit,
  relationship immutability, typed object extension.
- **Non-goals:** auth, providers, agent.

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

### Phase 3 — REvoCompute integration

- **Goal:** link external executions as durable references with no execution-state
  copy.
- **Owned domains:** Provider/Capability, Evidence/Provenance.
- **Vertical slice:** `ComputeCapability` + `ArtifactResolutionCapability`;
  `RunReference`/`ArtifactReference` creation and resolve-on-demand; the documented
  REvoCompute narrow scope/reference contract.
- **Acceptance evidence:** a fake REvoCompute provider test proves submit→reference→
  resolve with credential presence gate and typed failure; a test proves Core holds
  no provider vocabulary; provenance stays traversable when the provider goes
  unreachable.
- **Non-goals:** embedding REvoCompute state; hot-unloading.

### Phase 4 — Project collaboration

- **Goal:** cross-project sharing by membership/link, never copy.
- **Owned domains:** Identity/Collaboration, Project.
- **Vertical slice:** Actor + membership + owner/member/viewer; project visibility
  (private/shared); sharing a global object across projects via membership;
  authorization-aware access.
- **Acceptance evidence:** two projects share one object without duplication; deleting
  one Project leaves the shared object + provenance intact; access boundaries enforce
  membership/role.
- **Non-goals:** RBAC engine, per-object ACL, public visibility, real login.

### Phase 5 — Agent context & tools

- **Goal:** the Agent as consumer, with the promotion boundary enforced.
- **Owned domains:** Agent Context.
- **Vertical slice:** `/context` assembly (ContextSelection/ContextBuilder); typed
  ToolCatalog from provider + domain schemas; skills populate; the
  propose→promote loop with the authority matrix.
- **Acceptance evidence:** an agent can read bounded context, propose a Decision, and
  have it remain non-truth until committed; a test proves no agent path performs a raw
  write; chat history never appears in the graph.
- **Non-goals:** chat UI polish, RAG.

### Phase 6 — REvoDesign & OpenBio integration

- **Goal:** interactive design and external biological knowledge fit without
  special-case Core logic.
- **Owned domains:** Provider/Capability, Evidence/Provenance.
- **Vertical slice:** `DesignCapability`/`InteractiveHandoffCapability`;
  `SearchCapability` over OpenBio; import-promotion boundary (ExternalReference →
  ScientificObject only via explicit import).
- **Acceptance evidence:** a design export produces an object + provenance chain
  without Core branching; an OpenBio lookup can be cached, imported, or used as
  evidence — three distinct outcomes; Core depends only on capability Protocols.

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
>    operations.** Chat is conversation until explicitly promoted.
> 5. **Scientific provenance must remain traversable after external systems change.**
>    References are never deleted, only revoked; a provider disappearing never corrupts
>    stored context.
> 6. **Durable identity is an opaque UUID — never a filesystem path or a mutable
>    username.**
> 7. **Scientific content is immutable once referenced; change is a new version or a
>    superseding record, never in-place.**
> 8. **A Project is a namespace and membership boundary, not the owner of objects;**
>    deleting a Project removes links, never the underlying objects or their
>    provenance.
> 9. **The Agent is a consumer, not an owner — read context → reason → propose →
>    typed tool → domain validation → persisted truth.**
> 10. **Credentials are owned by the credential store; a provider is callable iff its
>     driver is READY and every required credential kind is present — both are
>     queries, not stored truth.**
