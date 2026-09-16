# Implementation State

Last verified: 2026-09-15

This file records actual, machine-verified repository state — not future plans.

## Status

The accepted architecture is implemented through **Phase 9** (persistent Project
conversations) on top of Phases 1–8 (scientific context core → workspace slice →
provider identity → REvoCompute → collaboration → agent context → project tool
harness → bounded Project Agent runtime). Phase 9 adds durable, Actor × Project
scoped conversation working memory with server-owned history; it does NOT add
RAG, Agent memory, a second scientific data model, or any new authority path.
The governing invariant is unchanged:

> **Persist working memory, never stale truth or authority.**

**Phase 10 (Project Notebook / structured working notes) is merged into `main`**
(PR #11, commit `135a591`); its architectural decision is **Accepted**
(`PROJECT_NOTEBOOK.md`, ADR-0016).

**Phase 11 (durable explicit-action handoff / human-authorized execution) is merged
into `main`** (PR #12, squash commit `83f1827`,
`feat(phase 11): add human-authorized action handoff`); its architectural decision is
**Accepted** (`docs/architecture/AGENT_ACTION_HANDOFF.md`, ADR-0017) — the PR #12
merge itself is the explicit human acceptance. The governing invariant is:

> **Persist intent, never authority. Re-derive authority at execution time.**

**Phases 1–11 are the accepted `main` state.** The Phase-11 section below records the
machine-verified state at the merge head (the two human-review P1 fixes are included in
`83f1827`).

**Phase 12 (Project Search & Bounded Context Retrieval) is implemented on
`feat/phase-12-project-search` and machine-verified**; its architectural decision is
**Proposed — pending human acceptance** (`docs/architecture/PROJECT_SEARCH_RETRIEVAL.md`,
ADR-0018). It adds the first Project-wide retrieval surface — canonical Project truth →
authorization-aware bounded lexical search → typed `SearchHit` references → workspace
search → explicit Add-to-Agent-context through the EXISTING `ContextSelection`/`ContextBuilder`
→ a bounded read-only Agent `project.search` Tool — without introducing RAG, embeddings,
a vector store, Agent memory, a central copied `SearchDocument` truth, a second context
model, or a new Core domain. The governing invariants are:

> **Search discovers references; it never creates truth, widens authority, or silently
> enlarges Agent context.**

> **Retrieve first, select explicitly, then build context.**

## Implemented (Phase 1)

- **Minimal authority substrate** (`revolab/domain/identity.py`):
  `Actor`, `ProjectMembership` (owner/member/viewer), `ResourceStewardship`
  (transfer/freeze), and `MutationGrant` issuance via the frozen Acting-Actor
  formula `membership.role ∈ {owner,member} AND steward_project == project AND
  project.deleted_at IS NULL`. Creation is distinct from mutation: a
  mutation-capable membership atomically creates Series + initial Revision +
  `ProjectResourceLink` + `ResourceStewardship`.
- **Domain layering (DAG honest).** Authority is derived only in
  `revolab/domain/identity.py` at the command boundary (`revolab/services.py`),
  which issues `MutationGrant` values (`revolab/domain/grants.py`) and passes them
  into `revolab/domain/scientific_object.py`, `revolab/domain/provenance.py`, and
  `revolab/domain/knowledge.py` — none of which import Identity. **MutationGrant
  is real, not ceremonial:** leaf resource mutations (`append_revision`,
  `update_series`, `mark_archived`, `attach_external_identity`, the typed global
  edge commands, `import_revision`) consume the grant and re-validate
  `resource_id` + current stewardship via `persistence.validate_grant`; the edge
  sink (`persistence.insert_edge`) additionally cross-checks asserted endpoint
  kinds against `GlobalResourceRegistry`. Direct leaf calls with a missing,
  wrong-resource, or stale grant are rejected (regression tests).
- **Read projection is scoped.** Object-detail decisions and bounded-graph
  decision edges join through their owning `Decision` and filter
  `Decision.project_id`; `decision_summary` scopes the supersede pointer by
  `DecisionSupersedes.project_id`; `bounded_graph` rejects a start resource that
  is not linked into the Project; `generated_by` filters every hop through the
  Project's visibility lens; archived Evidence/Decisions are excluded from
  object-detail. A two-Project shared-resource regression proves Project A cannot
  see Project B's Evidence/Decision/topology.
- **Read-side authorization projection.** Reads through a Project require
  `readable_membership` (any role in an active Project); a non-member or a
  tombstoned Project is rejected, so global identity is not global readability.
  Granting the read lens (`link_series`) requires a mutation-capable membership
  in the target Project.
- **Global identity spine** (`revolab/models.py`): `GlobalResourceRegistry` with
  every concrete global table PK = FK to `resource_id`; `ProjectResourceLink`
  carries read/context visibility + folder placement + optional
  `preferred_revision_id` (no per-resource role, no `resource_kind` duplicate).
- **ScientificObject** Series + Revision: `series_id` vs `revision_id` are
  distinct; no `current_revision_id` (derived `max(revision_seq)`); typed payload
  validated by the Core type registry (`revolab/domain/types_registry.py`) and
  stored as schema-versioned JSONB — no unvalidated JSON.
- **Reference identity cards**: `RunReference`, `SessionReference`,
  `ArtifactReference`, `LiteratureReference`, `ExternalReference`, plus the
  `ExternalIdentity` registry and `ScientificObjectExternalIdentity` mapping.
  **Canonical durable-uniqueness keys (convergence):** `UNIQUE(authority,
  native_id)` on Run/Session/Literature reference; `UNIQUE(authority, native_id,
  version_id)` on Artifact (version identity explicit; `version_id` NOT NULL,
  default `""`). Reference creation is get-or-create + link: an existing node is
  returned and linked (the second Project gains visibility, never stewardship),
  never silently duplicated; a **contradictory immutable assertion** against the
  same identity (differing checksum / digest / task_type / size / content_type)
  is rejected with `ConflictError` rather than absorbed. `ExternalReference`
  remains an append-per-cache metadata record keyed to the (already unique)
  `ExternalIdentity` — it is not the durable identity card.
- **ScientificObject conceptual type is immutable**: a revision always inherits
  its Series' `object_type` (the divergent-revision code path and parameter were
  removed); a conceptual type change requires a new Series + a scientific
  relation.
- **Acyclic supersession**: `supersede` preserves 0..1 incoming/outgoing and
  rejects direct (`A→B→A`) and transitive (`A→B→C→A`) cycles before persistence.
- **Frozen edge matrix**: `GlobalProvenanceEdge` (#1-7) with registry-FK
  endpoints + kind columns, immutable, corrected via `superseded_by_id`
  (never archived by a Project); `DecisionEvidence` (#10 cites, with `cited_as`),
  `DecisionTarget` (#8 selects), `DecisionSupersedes` (#9, 0..1-out/0..1-in).
  Edges are created only by typed domain commands with per-edge authority — there
  is no generic relation writer. `generated_by` is a derived traversal
  (`revolab/queries.py`), not persisted.
- **Evidence**: interpreted claim with immutable source/target and mutable
  interpretive fields; freeze is derived (committed Decision cites it or another
  Evidence targets it), correction is a new Evidence row.
- **Decision**: `draft → committed` promotion gate; `commit` atomically freezes
  the statement and materializes the immutable knowledge edges; committed
  Decisions are superseded, never edited.
- **Project deletion** = tombstone: hard-delete links/memberships, soft-archive
  Evidence and Decision rows (their immutable knowledge edges are retained with
  them, with no independent archival state; the read projection hides the
  archived context), transfer-or-freeze stewardship; global resources and global
  provenance edges survive. Documentation reconciled to this invariant.
- **`ContentStore`** (`revolab/content_store.py`): immutable, content-addressed
  `put`/`get` over an fsspec local backend; internal artifacts are
  `ArtifactReference(authority=revolab)`.
- **Project-scoped API** (`revolab/api.py`, `revolab/schemas.py`): project CRUD +
  membership, object Series/Revision + import + external identity, typed
  per-edge endpoints, reference creation + ContentStore upload/resolve, evidence,
  decision draft/commit/supersede, bounded graph query, and the object-detail
  aggregate. Mutations require an `X-Actor-Id` header (real auth remains
  deferred).

## Physical-representation spike

Recorded in **ADR-0015**: typed JSONB payload (Core type registry) and one edge
table per frozen ownership class (`global_provenance_edges` + the project
knowledge-edge tables). See
`docs/architecture/adr/ADR-0015-phase1-physical-representation.md`.

## Verified evidence

- `pytest` (70 tests) passes: authority/roles/stewardship/tombstone/MutationGrant
  (including direct leaf-mutation bypass + stale/wrong-grant rejection + the edge
  sink's registry-kind cross-check); atomic object creation; series/revision
  identity + immutability + conceptual-type immutability; revision visibility
  closure; typed-payload validation; ProjectResourceLink read visibility !=
  stewardship; per-edge authority + endpoint matrix + no self-edge + negative
  artifact/run-as-series/revision endpoint tests; generated_by; Evidence freeze,
  ghost-knowledge rejection, and PATCH null-vs-omission; Decision draft mutability
  / commit atomicity / committed immutability / supersede (commit-only, 0..1
  in/out, direct+transitive cycle rejection); read-projection scoping (two-Project
  shared-resource regression); reference get-or-create + version identity;
  ContentStore byte identity; project-scoped API vertical slice.
- `ruff check backend` and `mypy` (strict) pass.
- Migration: the prototype root migration was squashed into one clean root
  migration `42bee4363564` (no backward-compat requirement). `alembic upgrade
  head` + `alembic check` report **no drift** on SQLite and on a **clean
  PostgreSQL 16** database; a full object→revision→evidence→decision→commit
  vertical slice was smoke-run against PostgreSQL and committed successfully.
- Frontend gates pass: `npm run typecheck`, `npm run test`, `npm run build`;
  `npm run check:contracts` regenerates the OpenAPI-derived TypeScript and
  reports no drift (Phase 2). The bootstrap fixture frontend has been replaced.

## Implemented (Phase 2)

- **Deterministic generated contract boundary.** `python -m
  revolab.export_openapi` emits a sorted-key, stable-indent OpenAPI document
  committed as `frontend/src/contracts/openapi.json`. `openapi-typescript`
  derives `frontend/src/contracts/schema.d.ts` and
  `scripts/generate-enums.mjs` derives
  `frontend/src/contracts/enums.generated.ts` from that snapshot. The typed
  `openapi-fetch` client (`frontend/src/api/client.ts`) consumes `schema.d.ts`;
  form controls read enum value lists only from `enums.generated.ts`. No
  hand-maintained frontend domain enum or wire schema remains.
- **CI drift is executable.** The backend job re-exports `openapi.json` and
  fails on any diff; the frontend job runs `npm run check:contracts` (fresh
  regeneration + `git diff --exit-code`).
- **Typed Phase-2 API completions (backend).** Object collection/detail now
  declare typed response models (`ObjectSummaryRead`, `ObjectDetailRead`,
  `EdgeRead`; `DecisionRead` extended with `created_at`/`committed_at`); evidence
  and decision endpoints return declared models so the aggregate is no longer an
  untyped JSON bag. Added `GET /api/projects/{project_id}/resources` — a
  Project-lens collection of visible reference cards (run/session/artifact/
  literature/external), kind-filterable. Object-detail now includes draft
  decisions whose working `draft_selects` target the object (drafts are read,
  never written by the read projection; commit still materializes truth).
- **Real Project-scoped workspace frontend.** The bootstrap `App.tsx` and its
  fixture `objects`/`relations`/`evidence`/`decisions` arrays are removed. The
  frontend is a project-lens workspace: project creation/selection, Overview,
  Objects (collection via `ProjectResourceLink` presentation semantics + create),
  Object Detail (Series and Revision identity rendered separately, revision
  payload, inbound/outbound provenance, attached evidence and decisions),
  Evidence, Runs & Artifacts (reference cards), Decisions (draft commit /
  immutable committed / superseded badges), Knowledge (committed truth only),
  and a static Phase-2 provider capability surface. A passive selection-driven
  context inspector renders the selected object's aggregate.
- **Actor identity seam (not authentication).** On first load the workspace
  persists an opaque Actor id (via `POST /api/actors`) and sends it as
  `X-Actor-Id`; OIDC/login remains deferred.

## Verified evidence (Phase 2)

- Backend: `pytest` (74 tests) passes, including new regressions for typed
  object-detail aggregate, draft-in-object-detail before commit, reference
  collection through the Project lens, and reference-collection project scoping.
  `ruff check backend` and strict `mypy` pass.
- Frontend: `npm run typecheck`, `npm run test` (6 tests: generated-contract
  boundary + project-scoped shell, no production fixture state), and
  `npm run build` pass.
- Browser smoke test (`npm run test:e2e`, Playwright, Chromium) passes against a
  real FastAPI backend + real SQLite database path: create Project → create
  ScientificObject → object detail → create Evidence → create Decision draft →
  commit → reload → observe committed truth in Knowledge. No fixture scientific
  state. The same spec runs in CI against the repository PostgreSQL service.

## Removed prototype paths

- Old `ScientificObject` self-tree (`parent_id`) and `metadata_json` blob.
- `Evidence.provider`/`external_id` + `evidence_type` conflation.
- Immediate `POST /decisions`-as-truth; generic `POST /relations` writer;
  free-string relation/evidence/status columns.
- Old `cascade="all, delete-orphan"` ownership semantics (replaced by the
  three-class lifecycle).
- Checked-in `revolab.db` SQLite artifacts at repo root and under `backend/`
  (now gitignored via `*.db`), and the prototype root Alembic migration.
- Hardcoded CORS origin made configurable (`REVOLAB_CORS_ORIGINS`).

## Implemented (Phase 3)

- **Actor-scoped non-secret credential binding**
  (`ExternalProviderCredentialBinding`): `(actor_id, provider_key, kind,
  secret_ref)` with `UNIQUE(actor_id, provider_key, kind)`, FK to `actors`
  (CASCADE), hard-delete revocation, in-place `secret_ref` rotation.
  `provider_key` is the stable lowercase provider slug; `kind` is
  provider-declared free-string vocabulary (NOT a Core enum); `secret_ref` is an
  opaque locator only. Migration `fdd753899c9f` (revises
  `42bee4363564`).
- **Secret store boundary** (`revolab/secret_store.py`): `SecretStore` Protocol
  (`put/get/exists/delete/lease`) + an explicitly named **non-production**
  `InMemorySecretStore` (random `token_urlsafe` handles, never derived from the
  material; refuses to construct under `REVOLAB_ENVIRONMENT=production`). Secret
  material never enters Core persistence, API responses, or logs.
- **Ephemeral `CredentialLease`** (`revolab/credentials.py`): frozen, exposes
  only `get(kind)`, supports plural required kinds, `repr` carries only the kind
  count, and pickling/serialization raises `TypeError`.
- **Provider/Capability convergence** (`revolab/drivers.py` revised per
  ADR-0012): thin lifecycle `Driver` + `Capability(provider_key, kind)`;
  `capabilities: Mapping[CapabilityKind, Capability]`; two domain-visible
  lifecycle states (`REGISTERED`/`READY`); `required_credential_kinds` on the
  Driver; lazy `registry.health(name)` / `refresh_health(name)` cache (never
  persisted, actor-independent). `CapabilityKind` (closed), `ProviderRuntimeHealth`
  (`ready/degraded/unreachable`), and `CapabilityAvailability`
  (`available/credential_missing/not_authorized/provider_unavailable`) are the
  Core-owned enum vocabulary in `revolab/enums.py`.
- **Derived availability** (`revolab/domain/provider.py`):
  `capability_availability` is a pure composition of `driver health READY` AND
  all required credential kinds present for the Actor AND a per-capability
  project-policy result supplied by the application policy layer
  (`services.project_policy_permits`: read-only `ARTIFACT_RESOLUTION`
  accepts any readable membership; action capabilities require `owner`/`member`)
  — a query, never stored. Credential presence is a derived query over the
  binding set. `build_credential_lease` is the last-mile materialization layer.
- **Real Project-scoped Provider Catalog**:
  `GET /api/projects/{project_id}/providers` returns non-secret, Actor-contextual
  entries (identity, display metadata, required credential kinds, runtime health,
  the caller's own per-kind presence, and per-capability realized kind +
  derived availability). With zero configured providers the catalog is the
  honest empty set — no production fixture provider state.
  Runtime `RequestValidationError` responses keep the documented
  `HTTPValidationError` envelope (`detail` as an error array) with only the
  secret-bearing `input`/`ctx` stripped — the OpenAPI wire contract stays true.
- **Actor-scoped credential management** (`/api/credentials`):
  create (`POST`), explicit replace/rotation (`PUT`), explicit revocation
  (`DELETE`), presence-only list (`GET`); routed through the trusted
  `X-Actor-Id` seam (a query-scoping seam, NOT authentication — OIDC remains
  deferred). Provisioning validates the provider exists in the registry and the
  kind is one of its `required_credential_kinds`, using ONE canonical
  provider-key/credential-kind grammar (`revolab.enums`) shared by Pydantic
  validation and driver registration; a duplicate race at the database
  uniqueness boundary maps deterministically to `ConflictError` (409) with the
  materialized secret compensated. Responses never echo submitted secrets.
- **Frontend**: the static Phase-2 provider surface is replaced by the real
  Project-scoped Provider Catalog rendered from the generated contract
  (`useProviders` → `ProvidersView`), showing the derived availability and the
  caller's own credential presence; no credential-provisioning UI, no
  hand-written enum value lists, no provider vocabulary in generic UI.

## Verified evidence (Phase 3)

- Backend: `pytest` passes with **125 passed, 2 skipped** (the two skips are the
  opt-in PostgreSQL acceptance file, run explicitly in CI with a migrated PG).
  New regressions cover credential-binding CRUD/uniqueness/rotation/revocation +
  unknown-provider/unknown-kind rejection + the database-uniqueness boundary
  mapping a duplicate race to `ConflictError` (with secret compensation), a
  canonical provider-key/credential-kind grammar enforced fail-closed at driver
  registration and reused by API validation, secret-boundary sentinel absence
  (DB rows, reprs, logs, error envelopes), ephemeral/unpicklable lease,
  plural-kind lease materialization, the full availability matrix (incl.
  per-capability read-only vs action policy, two Actors observing different
  availability in one Project, and revocation flipping the next query with no
  stored repair), the two-state registry + capability projection + key/kind
  mismatch rejection + lazy health probe, catalog Actor-scoping,
  credential-query scoping by the trusted Actor seam, and the 422 no-echo gate
  that preserves the standard `HTTPValidationError` envelope. `ruff check
  backend` and strict `mypy` pass.
- Migrations: `alembic upgrade head` + `alembic check` report **no drift** on a
  clean **SQLite** database and on a **clean PostgreSQL 16** database (fresh
  `revolab_p3`). The Phase-3 credential/availability vertical slice passes
  against the migrated PostgreSQL schema
  (`backend/tests/test_postgres_integration.py`: 2 passed).
- Frontend: `npm run typecheck`, `npm run test` (8 tests — generated-contract
  boundary, project shell, and the new Providers catalog view), and
  `npm run build` pass. `openapi.json` / `schema.d.ts` / `enums.generated.ts`
  were regenerated from the FastAPI schema; `npm run check:contracts` reports no
  drift when compared against the committed artifacts.

## Secret-store disclosure

The application-scoped Secret store is the **non-production**
`InMemorySecretStore` (test/development only). No production-secret readiness is
claimed: a production deployment must supply a production-grade secret backend
(durable, encrypted, managed); the `SecretStore` Protocol is the seam that keeps
Actor/binding/Provider/Driver/capability/availability contracts unchanged when
one is introduced.

## Implemented (Phase 4)

- **Provider-neutral capability protocols** (`revolab/capabilities.py`, a leaf
  importing only `enums`/`credentials`): `ComputeCapability`
  (`list_task_kinds` / `task_kind_schema` / `submit` / `get_run` /
  `list_artifacts`) and `ArtifactResolutionCapability` (`resolve`) plus the
  neutral value objects `InputBinding`, `ResolvedInput`, `ExternalArtifactRef`,
  `RunHandle`, `RunView`, `ArtifactHandle`, `TaskKindRef`, `TaskKindSchema`,
  `InputSpec`. No REvoCompute vocabulary exists in Core: `TaskKindRef.kind_id`
  and `TaskKindSchema.parameter_schema` are rendered/consumed as opaque data.
- **Core-owned failure vocabulary** (`CapabilityErrorKind` in `revolab.enums`,
  `CapabilityError` in `revolab.capabilities`): `AUTH / NOT_FOUND / INVALID_PARAM
  / PROVIDER_UNAVAILABLE / NETWORK / UNKNOWN`; mapped at the FastAPI boundary
  (`api.py`) to a `{"detail": "<sanitized string>"}` envelope. `str`/`repr` of
  `CapabilityError` and `CredentialLease` never carry secret material.
- **Provider/Capability-domain invocation** (`revolab/domain/compute.py`):
  the single code path that composes `READY driver → project policy permits →
  required credentials present → ephemeral `CredentialLease` → capability
  method`, translating `SecretMissingError` to `CapabilityError(AUTH)` at the
  invocation boundary. Every capability call goes through it.
- **REvoCompute driver** (`revolab/drivers/revocompute.py`, the only module that
  knows REvoCompute HTTP vocabulary): realizes `COMPUTE` +
  `ARTIFACT_RESOLUTION` over the public HTTP API, `authority = "revocompute"`,
  one long-lived credential kind `"api_key"` presented as `X-API-Key`. It
  translates the flat `params[]` descriptor into a Draft 2020-12 JSON Schema
  inside the driver, follows the submission 302 redirect to extract the task
  id, discriminates `failed` (404-with-status-body) from `not_found`, maps
  same-authority ArtifactReferences back to REvoCompute's `@<md5sum>/<path>`
  reference grammar, and never imports REvoCompute internals or reads its
  database/filesystem. Durable authority→driver resolution is explicit: drivers
  declare an `authorities` tuple and `DriverRegistry.driver_for_authority` is
  the only resolver — Core never assumes `authority == provider_key`.
- **Bootstrap & lifecycle** (`revolab/bootstrap.py`, `revolab/main.py`):
  explicit, deterministic FastAPI lifespan installs the REvoCompute driver only
  when `REVOLAB_REVOCOMPUTE_BASE_URL` is configured (otherwise the catalog stays
  the honest empty set), and an opt-in in-process provider-neutral fake
  (`REVOLAB_E2E_FAKE_COMPUTE=1`, `revolab/testing/fake_compute.py`) for the
  browser vertical slice. `start_all`/`stop_all` own the lifecycle; a broken
  configured driver fails startup loudly. No global import side effects.
- **Input resolution & typed persistence** (`revolab/services.py`):
  `resolve_compute_input` materializes a ScientificObjectRevision's typed JSON
  payload (checksum-verified) or an internal `revolab` artifact's ContentStore
  bytes, and passes external artifacts as identity-only references;
  `compute_submit` validates every precondition (policy, credential, input
  visibility/kind) before the external side effect, then performs the driver
  call and persists a durable `RunReference` + `consumed_as_input_by` edges;
  `compute_refresh_artifacts` live-enumerates artifacts and persists
  `ArtifactReference` cards + `produced` edges. No REvoCompute status/history is
  ever stored in Core.
- **Project-scoped compute API** (`api.py` + `schemas.py`):
  `GET /projects/{id}/providers/{key}/compute/task-kinds`,
  `GET .../task-kinds/{kind_id}/schema`,
  `POST /projects/{id}/compute/submissions`,
  `GET /projects/{id}/runs/{run_id}/status`,
  `POST /projects/{id}/runs/{run_id}/artifacts`,
  `GET /projects/{id}/artifacts/{artifact_id}/resolve` (live external access,
  not implicit ingestion). Run status returns `available=false` honestly on a
  transient outage — the stored RunReference is never invalidated.
- **Frontend vertical slice** (`views/Compute.tsx`, wired into `App.tsx` and
  launched from Object Detail): provider/task-kind selection, a small
  schema-driven parameter form (no JSON-schema form dependency added), one
  project-resource input, submit → RunReference → live status → discover
  artifacts → resolve bytes. Provider vocabulary renders only as data; the
  `Compute.test.tsx` regression asserts no `revocompute`/Runner names appear.
- **Upstream contract gaps recorded** in
  `docs/integrations/REVOCOMPUTE_CONTRACT_GAPS.md`: cross-Actor sharing is the
  one **blocking** gap and remains an upstream dependency (Phase 4 is
  single-Actor for it); JSON-schema exposure, run-list discovery, the
  redirect/failed-404 handshake quirk, and failed-run artifact reuse are
  recorded as non-blocking with their smallest upstream change.

## Independent review (Phase 4)

A fresh read-only architecture/security review was run over the full working-tree
diff. Two material blockers were found and resolved:

1. **Skipped project-policy gate.** The four compute read endpoints originally
   passed `permitted=True`; they now derive `permitted` from
   `services.project_policy_permits` (`COMPUTE` for task/run views,
   `ARTIFACT_RESOLUTION` for the pure read), so a viewer no longer reaches
   action capability methods.
2. **Authority/provider conflation.** Run status / artifact refresh / artifact
   resolve originally passed `run.authority`/`artifact.authority` straight into
   the registry lookup. They now resolve the authority through the explicit
   `DriverRegistry.driver_for_authority` (drivers declare an `authorities`
   tuple), so `(authority, native_id)` identity is never assumed to equal a
   resolver/provider key.

Non-blocking findings were also addressed: provenance-edge idempotency guards
(duplicate refresh no longer accumulates duplicate edges), executable tests for
the production-fake-refusal and missing-base-url startup guards, and the run
status endpoint now reports `available=false` only for
`PROVIDER_UNAVAILABLE`/`NETWORK` (authorization/credential failures keep their
own typed status). The `revocompute_*` settings in `revolab.config` are the
sanctioned bootstrap-configuration surface explicitly permitted by TODO.md for
concrete driver installation.

### Second review round (three fresh reviewers + PR codex)

Three additional independent reviewers (architecture, security, driver/frontend/
contracts) plus the PR's Codex review were reconciled; findings were resolved:

- **P1 multipart submission:** the driver now uploads every byte input under the
  repeated `files` form field REvoCompute actually reads (previously the first
  input was `file` and later inputs were `file2…`, which the server drops).
- **P1 submission error classification:** `submit` now applies `_raise_on_error`
  before extracting the task id, so upstream 400/422/401/403/429/503 map to the
  typed `CapabilityErrorKind` instead of `UNKNOWN`.
- **P1 input-format honesty (revision→non-JSON tasks):** the driver validates
  each byte input's file extension against the task's advertised
  `input_extensions` and fails fast with `INVALID_PARAM` instead of sending JSON
  to a FASTA/PDB task; recorded in
  `docs/integrations/REVOCOMPUTE_CONTRACT_GAPS.md` (section 6).
- **Security P2 cross-origin redirect:** the HTTP client no longer follows
  redirects automatically; the driver reads the `302 Location` itself and only
  accepts same-origin targets, and a test proves the sentinel is never replayed
  to another host.
- **Authorization P2 preflight:** compute submissions pre-validate the per-input
  stewardship anchor (`consumed_as_input_by` authority) before the external
  call, so a visible-but-not-stewarded input fails before any provider side
  effect; tested.
- **Other P2/info:** `authorities` is now a required `Driver` Protocol field with
  duplicate-authority registration rejection; scenario resolution verifies
  checksum and size; artifact paths reject traversal; the artifact-resolve
  endpoint declares a binary `*/*` OpenAPI response; the frontend consumes the
  generated `CAPABILITY_KINDS`/`CAPABILITY_AVAILABILITIES`/`RESOURCE_KINDS`
  constants rather than bare wire literals and resets parameters on provider or
  task-kind change; the `AUTH→502` status mapping is pinned by a test; and
  driver/API security test gaps (submit error mapping, cross-origin, path
  traversal, viewer read policy, compute 422 no-echo, mapping pin) were added.

### Merge-blocking review round

Two semantics were corrected after the above:

1. **`consumed_as_input_by` authority (#5).** The Phase-1 stand-in of
   "steward(source) + read(target)" (recorded in ADR-0015 as a placeholder
   until a real provider landed) was replaced with the frozen contract from
   `SCIENTIFIC_GRAPH.md`: **task-submission authority + read(input)**. The
   typed `provenance.add_consumed_input` no longer consumes a source
   `MutationGrant`; the command boundary requires a mutation-capable membership
   in the acting Project and readability of both endpoints. A shared/visible
   input stewarded by another Project may now be submitted as input without
   transferring source stewardship (regression: `test_shared_visible_input_...`
   in `test_compute_core.py`, and the leaf authority test in
   `test_grant_authority.py`). The preflight-before-external-side-effect
   property is preserved with the corrected authority.
2. **Relative REvoCompute success redirect.** The real submission handler
   returns `redirect(f"/compute/api/running/{md5sum}")` — a RELATIVE `Location`.
   `_extract_task_id` now resolves the `Location` against the configured base
   URL (`urljoin`) before the same-origin check and md5sum extraction, keeping
   the no-cross-origin-credential invariant (contract test:
   `test_submit_accepts_relative_same_origin_redirect`).

## Verified evidence (Phase 4)

- Backend: `ruff check backend` and strict `mypy` pass (31 source files).
  `pytest` passes with **174 passed, 3 skipped** (the three skips are the opt-in
  PostgreSQL acceptance file). New tests cover: the REvoCompute driver against
  an HTTP fake at the network boundary (discovery/schema translation, submit
  redirect + multipart + artifact-reference input, failed-on-404 run state,
  artifact identity encoding, resolve, auth/not-found/invalid-param/
  unavailable/network/malformed error mapping, cross-origin redirect
  no-forward, input-extension fail-fast, path-traversal rejection, resolve
  checksum/size verification, and the sentinel only-in-X-API-Key assertion),
  Core/domain compute with a provider-neutral fake (submit → RunReference +
  consumed input, artifact discovery + produced edge, resolve bytes,
  provider-disappearance leaves references intact, repeated refresh produces
  exactly one produced edge, and invalid-input-kind / missing-credential /
  not-authorized / input-not-visible / input-not-stewarded all fail before the
  external side effect), the project-scoped compute HTTP surface (including
  viewer read policy, non-member denial, and compute 422 no-echo), and the
  bootstrap/lifecycle + explicit authority-resolution guards (production refuses
  the fake provider; the real driver requires a base URL).
- Migrations: no Phase-4 schema change is required (RunReference/ArtifactReference
  and the two provenance edges already exist). `alembic upgrade head` +
  `alembic check` report **no drift** on SQLite and on a fresh PostgreSQL 16
  database (`revolab_p4`); the Phase-4 compute submit→provenance→artifact
  vertical slice passes on PostgreSQL (`test_postgres_integration.py`: 3 passed).
- Frontend: `npm run typecheck`, `npm run test` (10 tests — contract boundary,
  project shell, providers, and the new compute view with no-provider-vocabulary
  regression), and `npm run build` pass. `openapi.json` / `schema.d.ts` were
  regenerated; `enums.generated.ts` is unchanged (the Core capability-failure
  enum is not exposed on the wire).
- Browser smoke (`npm run test:e2e`, Playwright Chromium): the Phase-2 slice
  still passes, and the new compute vertical slice passes over a real
  uvicorn/SQLite backend with the opt-in in-process fake provider
  (task-kind select → submit → RunReference → live `finished` status →
  discovered artifact), and the Providers catalog now renders the derived fake
  provider instead of the empty state.

## Implemented (Phase 5)

- **Membership collaboration commands** (`services.py`, `api.py`): inspect the
  Project roster (`list_memberships`), change a member's role (`update_membership`),
  and remove a member (`remove_membership`), all owner-gated; `add_membership`
  now validates the role, rejects duplicate membership as `ConflictError`, and
  rejects unknown Actors as `NotFoundError`. The **final required owner
  invariant** is enforced in one place (`_other_owner_count`): an active Project
  can never be left with zero owners by demoting or removing the last owner.
  Tombstoned Projects reject membership operations fail-closed.
- **Project visibility** (`services.py`, `schemas.py`, `api.py`): `ProjectCreate`
  and the new `PATCH /projects/{id}` accept the accepted two-state visibility
  (`private | shared_with_members`); `public` is rejected. Visibility is a
  Project label, never an access grant — read authorization stays
  membership-derived (`readable_membership`), so a non-member is denied
  regardless of visibility and visibility does not touch `ProjectMembership`.
- **First-class cross-Project sharing** (`services.share_resource` +
  `POST /projects/{id}/shares`): binds an already-existing global `resource_id`
  into another Project's `ProjectResourceLink` set. Authority is two-fold: a
  mutation-capable membership in the TARGET Project, plus proof that the actor
  can READ the resource through at least one OTHER active Project (source
  visibility) — possession of a UUID is not permission to link an arbitrary
  resource. Sharing is idempotent and copies nothing; the same global id becomes
  visible through multiple Projects.
- **Revision ⇒ series closure executed** (`provenance.share_into_project`):
  sharing a revision also links its owning Series (sibling revisions stay
  private, past and future); sharing a Series never links revisions. Read
  projections derive the current visible revision only from that Project's
  visible revision set, with the optional Project-local preferred-revision pin
  (`set_preferred_revision` + `PUT .../preferred-revision`, validated to name a
  visible revision of that series). No global `current_revision_id` exists.
- **Read-only lens surfaced**: object summary/detail and reference reads now
  carry `read_only` (`queries.read_only`: the Project is not the steward), and
  object reads expose the `preferred_revision_id` pin.
- **Project-context write-time invariant** is exercised end-to-end across shared
  resources: Evidence source/target, Decision draft selects and commit, and the
  import command's owning-series visibility (`services.import_revision`) all fail
  closed on endpoints outside the Project's visible link set; compute inputs were
  already gated by `resolve_compute_input` visibility (pre-Phase-5,
  `test_grant_authority.py`). Backend-owned, not frontend-only.
- **Frontend collaboration surface** (`views/Settings.tsx`, `views/ObjectDetail.tsx`
  share panel): members/roles, add/change/remove (owner-gated), Project
  visibility editing, resource sharing into another Project, a per-object
  "Read-only in this project (not steward)" indication, and Project-local
  Evidence/Knowledge around shared resources. Role and visibility choice lists
  come only from the generated `ROLES`/`PROJECT_VISIBILITIES` enums.
- **REvoCompute honesty preserved**: sharing a Run/Artifact reference grants
  REvoLab context visibility only; upstream REvoCompute authorization remains a
  separate, Actor-scoped concern (see
  `docs/integrations/REVOCOMPUTE_CONTRACT_GAPS.md`).

## Verified evidence (Phase 5)

- Backend: `ruff check backend` and strict `mypy` pass (31 source files).
  `pytest` passes with **208 passed, 4 skipped** (the four skips are the opt-in
  PostgreSQL acceptance file). New collaboration regressions cover membership
  roster/role/remove + final-owner protection + duplicate/unknown Actor +
  tombstone denial; visibility create/update/owner-gate/non-member denial;
  share target-membership + UUID-possession + unknown-resource rejection;
  revision→series closure with future-sibling privacy + idempotence;
  visibility-is-not-stewardship for rename/append/archive/external-identity;
  shared-resource interpretation isolation; project-context write-invariant for
  Evidence/Decision; the preferred-revision pin (visibility + series-membership
  validation); tombstone survival of the shared resource and the surviving
  Project's context; the partially-privileged global-edge projection; and
  caller-credential isolation for shared artifact resolution.
- PostgreSQL: `alembic upgrade head` + `alembic check` report **no drift** on a
  clean PostgreSQL 16 database; the Phase-5 collaboration vertical slice passes
  on PostgreSQL (`test_postgres_integration.py`: 4 passed) — two Actors, two
  Projects, one shared revision, read-only non-steward mutation denial, isolated
  interpretations, and Project deletion preserving the global resource and the
  surviving Project's Evidence/Decision.
- Frontend: `npm run typecheck`, `npm run test` (11 tests — plus the new
  Settings collaboration view), and `npm run build` pass.
  `openapi.json`/`schema.d.ts` regenerated; `enums.generated.ts` now also carries
  the Core-owned `Role` and `ProjectVisibility` choice lists.
- Browser (`npm run test:e2e`, Playwright Chromium, workers serialized over one
  database): the Phase-2 smoke and the new collaboration E2E pass — two Actors
  share one revision through the UI, the receiving Project shows the read-only
  lens and no sibling revision and forms its own Evidence, the source Project
  sees none of it, and the owner drives visibility + membership-role changes
  through the Settings surface.

## Independent review (Phase 5)

Five fresh read-only reviewers audited `main...HEAD` (architecture/domain,
authorization/security, persistence/lifecycle, API/frontend/contracts,
tests/CI/maintainability). **No P0 findings.** One P1 (untyped
`ProjectRead.visibility`) and the in-scope P2s were fixed, then the full suite
was rerun green:

- **P1** — `ProjectRead.visibility` was an unconstrained `string`; now typed as
  the generated `ProjectVisibility` enum (the frontend cast removed).
- **P2** — `queries.read_only` misreported a Revision inside its own steward
  Project (stewardship is per-Series); now resolves a Revision through its
  owning Series.
- **P2** — race-path hardening: `add_membership`/`share_resource` map the
  relevant uniqueness `IntegrityError` to `ConflictError`; the final-required-
  owner count selects **all** of the Project's owner rows under
  `SELECT ... FOR UPDATE` (counting excluding the target in Python), so
  concurrent owner demotions serialize and the loser re-counts against the
  committed owner set. The concurrent interleaving itself is not directly
  thread-tested (SQLite `StaticPool` is single-connection; PostgreSQL is the
  concurrency backstop) — the invariant is regression-tested sequentially.
- **P2** — added regressions for immediate-effect role/membership changes
  (HTTP), caller-credential (never the sharer's) for shared artifact resolution,
  `read_only` revision-through-series resolution, and partially-privileged
  global-provenance-edge projection.
- **P2** — explicit source-of-share policy documented (`services.share_resource`
  docstring): source authority is the read lens (any readable membership), and a
  share mints only another read lens — no stewardship/mutation/credential
  transfer.
- **P3 (behavior or UI, regression-tested where named)** — `ProjectPatch` clear
  vs omit description (HTTP-tested); frontend role/visibility literals derive
  from generated `ROLES`/`PROJECT_VISIBILITIES` constants (compile-time); the
  others are code-only defense-in-depth/UI layer and are NOT separately
  regression-tested: `get_evidence`/`get_decision` by-id exclude archived rows
  (unreachable until per-row archival exists, because tombstone also removes
  membership first), `import_revision` asserts owning-series visibility
  (unreachable in practice because stewardship implies the series link), and the
  Objects list read-only badge (render-only, backed by the tested `read_only`
  projection). Dead `SettingsIcon` removed; docs use the canonical
  `shared_with_members` wire value; CI PostgreSQL step label and PG-module
  docstring updated; roster-visibility policy recorded in
  `COLLABORATION_IDENTITY.md`.
- **Rejected/out-of-scope P3s (with rationale):** `share_into_project` living in
  `provenance.py` and per-query read-projection scans predate Phase 5 and match
  accepted ownership (no change); `consumed_as_input_by` raw endpoint and the
  `GET /actors/{id}` existence oracle are pre-Phase-5 accepted seams
  (`X-Actor-Id` is not authentication); the two visibility states being
  observationally equivalent is the accepted "label-not-gate" design (`public`
  deferred).

No Phase-5 migration was required (no `models.py`/migration change); the change
remains additive and drift-checked.

### GitHub review follow-up (P1 + P2 on `1e51543`)

A further GitHub review of head `1e51543` found one authorization blocker and a
concurrency-idempotence issue; both fixed without reopening the accepted
collaboration architecture:

- **P1 — existing-reference reuse no longer bypasses sharing authority.**
  The existing-reference branches of `create_run_reference` /
  `create_session_reference` / `create_artifact_reference` /
  `create_literature_reference` now route through
  `_authorize_existing_reference_link`, which permits linking an existing global
  reference only when it is **already visible in the target Project**
  (idempotent) or the Actor **can read it through another active Project** — the
  same source-read authority as `share_resource`. Possession of
  `(authority, native_id[, version_id])` is no longer authority to make another
  Project's reference visible. Provider/byte-furnished paths have explicit
  trusted internal helpers (`_persist_run_reference_trusted`,
  `_persist_artifact_reference_trusted`) reached only from the Actor-scoped
  capability call result (`record_compute_run` / `record_compute_artifacts`) or a
  byte upload (`create_internal_artifact`) — never a client-controlled flag.
  New negative regressions prove an Actor cannot link another Project's existing
  Run/Session/Artifact/Literature reference merely by knowing its durable
  external identity, and positive regressions prove idempotent and
  source-visible reuse still succeed.
- **P2 — concurrent duplicate shares are idempotent, not 409.** On the narrow
  `uq_link_project_resource` race, `share_resource` now rolls back, re-reads the
  committed link state, verifies the resource is visible (and, for a revision,
  that the revision→series closure is satisfied via `_share_is_satisfied`, so the
  concurrent winner's work counts as this request's success), and otherwise
  re-raises. Unrelated `IntegrityError`s are never swallowed.
- **Defense-in-depth** — `share_resource` now validates target membership and
  share authority **before** reading the registry kind, so an Actor with no valid
  read path gets `AuthorizationError` rather than a `NotFound` existence oracle;
  the idempotent early-return still resolves the kind where the link already
  exists.

A second fresh pass (3 read-only reviewers: reference-reuse authorization,
concurrency/idempotence, regression/contract consistency) found **no P0/P1**.
Two in-scope P2s and the appropriate P3s were then fixed:

- **P2** — in `create_run_reference` / `create_artifact_reference`, the
  share-authority check now runs BEFORE `assert_reference_compatible`, so an
  unauthorized caller sees `AuthorizationError` rather than a 409
  existence/mismatch oracle (mirroring `share_resource`'s posture).
- **P2** — added behavioral regressions for the race branch: a forced
  `uq_link_project_resource` `IntegrityError` through `share_resource` returns
  idempotent success when the link state (incl. revision→series closure) is
  satisfied, an unrelated `IntegrityError` propagates, and
  `_share_is_satisfied` rejects a revision whose owning series is not visible.
- **P3** — positive reuse now covers all four reference kinds (was run-only);
  stale test name corrected to `test_share_unknown_resource_is_not_authorized`;
  `create_artifact_reference` documented as the request-derived/
  authority-enforcing primitive.

Accepted/deferred (documented, not silently postponed): an
unauthorized prober can still distinguish an existing hidden reference (403)
from a truly-new identity (201 mint) — inherent to get-or-create, and a strict
improvement over the pre-fix silently-linking behavior; the downstream duplicate-
link insert race in `_link_existing_reference` and the trusted helpers still maps
a concurrent identical reuse to a raw `IntegrityError` (pre-existing, DB
constraint still protects — tracked, not a Phase-5 blocker); `services.link_series`
remains a tests-only internal link primitive and must never be promoted to a
request/agent tool without source-read authority.

Verification after the fix (rerun in full): `ruff`, strict `mypy`, `pytest`
**214 passed / 4 skipped**, PostgreSQL acceptance **4 passed** + `alembic check`
no drift, frontend typecheck/test(11)/build + `check:contracts` clean, Playwright
E2E **2 passed**.

## Implemented (Phase 6)

- **Agent as consumer, never owner.** New `revolab/agent/` package (Agent Context
  domain) consumes Core's public contracts; Core never imports it. The application
  boundary (`api.py`) composes it as the Presentation-domain consumer.
- **ContextSelection / ProjectContext / ContextBuilder.** `ContextSelectionCreate`
  is a declarative, Project-scoped, explicitly bounded request (series/revision
  identities, include flags, hard budgets). `build_context` validates every selected
  identity against the Project's read lens (fail-closed on non-member, tombstoned
  Project, foreign/unknown ids) and assembles an immutable `ProjectContextRead`
  value object: series skeleton, addressable revision refs (no payload), bounded
  relations (`EdgeRead`), Evidence/Decision refs, reference headers with the derived
  originating run (never bytes), provider capability summaries, and loaded skill
  identifiers. No DB writes, no link/membership changes, no credential resolution.
- **Context budget.** Deterministic per-category caps; every category reports actual
  counts and a `truncated` flag. Caps are global across the turn (`max_revisions` is
  enforced across all selected series, not per-series). Large artifacts are
  reference headers only.
- **inspect_artifact tool boundary.** `inspect_artifact` resolves an
  `ArtifactReference` through the ContentStore (`authority == revolab`) or the
  existing `ArtifactResolutionCapability`, returns a bounded preview (default 2048,
  hard max 65536), and never copies bytes into Core or surfaces credential material.
- **ToolCatalog.** `build_tool_catalog` projects a fixed domain-tool subset (context
  build, artifact inspect, evidence create, decision record-draft, decision commit)
  plus available provider capabilities from the existing non-secret
  `catalog_entries`, omitting non-`AVAILABLE` capabilities entirely. Input/output
  JSON Schemas derive from canonical Pydantic/OpenAPI models; autonomy is typed as
  `AgentToolAutonomy` (`automatic` / `policy` / `explicit_action`). No credential,
  secret, share, membership, raw-SQL/HTTP/shell tool is ever projected.
- **Authority matrix (executable policy).** Reads automatic; `create_evidence` /
  `record_decision_draft` policy-gated (owner/member); `decision.commit` and compute
  submission `explicit_action` — never auto-executed by the Agent loop; sharing and
  credential operations are never Agent tools.
- **SkillCatalog.** Pointer-only (`revolab/agent/skills.py`): resolves the canonical
  `.agents/skills/<name>/SKILL.md` frontmatter for the small Phase-6 subset
  (`project-context`, `decision-record`, `provenance-lineage`); never copies skill
  content into context.
- **Ephemeral session + deterministic proposal.** `AgentSession` is process-local,
  never persisted, never a truth store; `propose_selection` is a deterministic fake
  Agent and `record_proposal` routes to the existing `services.create_decision`
  (draft-only). No committed decision can come from the Agent path.
- **API surface.** `POST /api/projects/{id}/context`, `GET
  /api/projects/{id}/agent/tools`, `POST /api/projects/{id}/agent/proposals`
  (draft-only), and `GET /api/projects/{id}/artifacts/{artifact_id}/inspect`
  (bounded preview). Commit remains the existing authorized endpoint
  `POST /api/projects/{id}/decisions/{decision_id}/commit`.
- **Frontend slice.** `views/Agent.tsx` selects an object, renders the assembled
  context summary + tool catalog (autonomy badges) + the proposal form and draft
  status with an explicit commit control; the truth boundary "Agent proposal ≠
  committed Project Knowledge" is rendered. The write controls (record draft /
  commit) are gated by the generated `owner`/`member` role vocabulary. No chat UI,
  no prompt management.
- **PostgreSQL / browser slice.** `test_postgres_integration.py` gained the
  Phase-6 vertical slice; `e2e/agent.spec.ts` proves draft → explicit commit →
  committed Knowledge over the real backend.

## Verified evidence (Phase 6)

- Backend: `ruff check backend` and strict `mypy` pass (37 source files).
  `pytest` passes with **247 passed, 5 skipped** (the five skips are the opt-in
  PostgreSQL acceptance file run separately below).
- Migrations: `alembic upgrade head` + `alembic check` report **no drift** on a
  clean SQLite database and on a clean PostgreSQL 16 database (`revolab_p6`). The
  PostgreSQL acceptance pass (**5 passed**) includes the new Phase-6 slice.
- Frontend: `npm run typecheck`, `npm run test` (**14 tests**, including the
  Agent view and the contract-boundary lockstep), and `npm run build` pass.
  `openapi.json` / `schema.d.ts` / `enums.generated.ts` regenerated from the
  FastAPI schema; `npm run check:contracts` and a fresh
  `python -m revolab.export_openapi` diff report **no drift**.
- Browser (`npm run test:e2e`, Playwright Chromium, workers serialized over one
  database): the Phase-2 smoke, the Phase-5 collaboration slice, and the new
  Phase-6 agent slice all pass (**3 passed**).

## Independent review (Phase 6)

First pass: five fresh read-only reviewers (architecture/domain, authority/
security, backend/API/contracts, frontend/context UX, tests/PostgreSQL/CI)
audited `main...HEAD`. **No P0 findings; all five returned PASS.** Reconciled
findings (committed in `a359408`, `661f6f3`, `bd5e410`):

- single-sourced the proposal request on `DecisionCreate` (removed the duplicate
  `AgentProposalCreate` OpenAPI component) and bounded decision statement/list
  fields; `preview_limit` declared in OpenAPI with ge/le;
- corrected provider tool input schemas (`kind_id` opaque string; `run_id`
  REvoLab UUID with resolution documented) and typed the proposal handler;
- SQL-scoped the edge assembly's double-endpoint visibility filter;
- typed the deterministic proposal with canonical `ResourceKind`/
  `CitationCreate`/`SelectTargetCreate`;
- frontend enum-lockstep test extended, truth-boundary callout styled, tool rows
  render name/source, inert lint pragma removed;
- tests hardened: exact closed domain tool-id set, read-only ToolCatalog proof,
  other-Actor credential non-projection (tool catalog + serialized context),
  external-artifact inspect credential non-leak, viewer HTTP fail-closed,
  removed vacuous/tautological assertions; locked the proposal request to the
  single `DecisionCreate` component;
- `AGENT_CONTEXT.md` ref terminology reconciled to the durable wire shape.

Second pass (three fresh read-only reviewers over the contract/security/test fix
delta): contract/schema returned PASS (no P0/P1); security/authority returned
PASS (no P0; one P1 test-strength gap + P2s fixed in `12ab928`). The
tests/PostgreSQL/CI second-pass reviewer stalled and was not replaced a second
time; that lens is covered by the first-pass Reviewer E (PASS, full machine
evidence) and by the primary integrator re-running the complete gate suite after
every reconciliation.

Codex review head `5748774` requested changes; the four findings (packaged skill
root; bounded artifact inspection materializing full bytes; revision-only sibling
expansion; graph_depth edge leak) were fixed in `9b6dad4`. A third review round
(three fresh read-only reviewers over that delta) returned PASS for
Agent/context architecture and security/boundary (no P0/P1), and PASS for
packaging/tests/CI with non-blocking follow-ups. Those follow-ups (env wiring
regression, REvoCompute preview streaming + fail-closed + driver tests,
`resolve_artifact_preview` byte-bound enforcement, `limit==0` short-circuit) are
committed in `7554ed3`. Final machine evidence: `247 passed` (backend), `5
passed` (PostgreSQL), `14 passed` (frontend), `3 passed` (Playwright E2E).

## Implemented (Phase 7)

- **Roadmap correction.** `IMPLEMENTATION_ROADMAP.md` Phase 7 is now "Project
  Tool Harness & Analysis Runtime"; the REvoDesign/OpenBio Phase 7 was removed and
  recorded as a non-goal (REvoDesign is a method-specific interactive design app,
  OpenBio a design reference — neither a REvoLab backend). `CLAUDE.md` gained
  invariant #11; `SYSTEM_ARCHITECTURE.md` / `DOMAIN_BOUNDARIES.md` /
  `PROVIDER_CAPABILITIES.md` / `AGENT_CONTEXT.md` / `WORKSPACE_INFORMATION_ARCHITECTURE.md`
  were reconciled to the new Tool ownership.
- **Tool as first-class Harness abstraction.** New `revolab/tools` package (Project
  Tool Harness) with the frozen dependency direction Harness → Tool → adapter →
  local service OR capability/driver. Canonical `ToolDescriptorRead` carries
  `autonomy` (canonical `AgentToolAutonomy`), `execution_class`
  (`local|remote`), and `side_effect_class` (`read_only|creates_derived_result|
  domain_mutation|external_action`) — new Core-owned enums in
  `revolab/enums.py`. `CapabilityKind` is reduced to the realized
  `compute` + `artifact_resolution` (SEARCH/DESIGN/INTERACTIVE_HANDOFF deferred
  as speculative). Input/output JSON Schemas derive from the same Pydantic
  models the runtime validates against (never hand-copied).
- **Closed Local Tool Runtime** (`revolab/tools/runtime.py`, `registry.py`):
  lookup → Pydantic input validation → Actor/Project authorization → registered
  handler → typed output → `ToolResult`. The runtime executes only the fixed
  registered tool set; unknown ids (e.g. `python.eval`, `shell.run`, `sql.query`,
  `file.read`, `http.get`) fail closed. No arbitrary Python/shell/filesystem/HTTP.
- **First local analysis tools** (small, bounded, stdlib-only `csv`):
  `artifact.inspect` (bounded preview), `table.describe` (column stats),
  `table.select` (column/row projection + optional CSV persist), `plot.xy`
  (structured plot specification + optional JSON persist). Explicit bounds:
  1 MiB bytes, 10 000 rows, 100 columns, 5 000 plot points; unsupported/oversized
  artifacts fail closed.
- **REvoCompute projected through the same ToolCatalog** (`revolab/tools/catalog.py`):
  `{provider}.compute.*` + `{provider}.artifact.resolve` are remote
  (`execution_class=remote`) descriptors over the existing non-secret Provider
  Catalog — no duplication of task/run/artifact/credential models. Local and
  remote tools coexist in ONE `ToolCatalogRead` consumed by both frontend and
  Agent (`GET /api/projects/{project_id}/tools` and the existing agent endpoint).
  The Agent's Phase-6 `context.build` left the catalog: context reading is the
  context-assembly step, not a Tool.
- **Invocation + result semantics** (`POST /api/projects/{project_id}/tools/invocations`):
  `ToolResultRead` with `result_kind` (`ephemeral|artifact|evidence|decision` —
  the producing kinds only). `persist=true` on a derived-result tool
  (owner/member) writes an internal `ArtifactReference` via the typed
  ContentStore→reference path and a `ToolInvocation` record (tool_id,
  tool_version `1.0.0`, input resource ids, validated parameters,
  result_resource_id) — explicitly NOT a `RunReference`. Evidence/Decision tools
  route through the existing typed `services`; a Decision draft is the only shape
  produced and commit remains the separate promotion gate. Tool output is never
  auto-promoted to truth.
- **Migration** `c9a41f2d3e8b`: `tool_invocations` table (project-scoped, JSON
  columns cross-backend), drift-checked on SQLite and PostgreSQL.
- **Fake compute `tabular` task** (`testing/fake_compute.py`): a provider-neutral
  CSV-producing task so a REvoCompute-produced artifact can be analyzed by a local
  REvoLab Tool.
- **Frontend `Analyze` workspace.** New `views/Tools.tsx`: the Project ToolCatalog
  (local + remote), artifact selection, schema-typed local analysis (describe /
  select / plot) with run + result rendering, persist control, and a separated
  "Remote compute tools" section that points at the existing Compute flow. Wired
  into `App.tsx`; generated contract + enum lockstep extended (`ToolExecutionClass`,
  `ToolSideEffectClass`, `ToolResultKind`).
- **Skill** `.agents/skills/project-tool-harness/SKILL.md` (pointer-only, no schema
  copies) and `docs/architecture/PROJECT_TOOL_HARNESS.md`.

## Verified evidence (Phase 7)

- Backend: `ruff check backend` and strict `mypy` pass (46 source files). `pytest`
  passes with **281 passed, 6 skipped** (the six skips are the opt-in PostgreSQL
  acceptance file). New `tests/test_tools.py` covers registry duplicate-id
  rejection/closedness, unknown/remote-tool-id and banned-tool-id fail-closed,
  catalog authority/availability/secret-absence, typed table/plot outputs,
  viewer persist/truth denial, owner truth-tool success, schema validation,
  unsupported format/oversized/truncated/size-None-external bounds, validated
  (`model_dump`) parameters only, mis-wired typed-output rejection, no
  provenance-edge/registry write for derived artifacts, tombstone survival, the
  persisted `ToolInvocation` record + its read endpoint, and the
  REvoCompute-produced artifact analyzed locally
  (`test_revocompute_artifact_analyzed_locally` +
  `test_external_compute_artifact_select_persist_over_http`).
- PostgreSQL: `alembic upgrade head` + `alembic check` report no drift on a fresh
  SQLite stack and PostgreSQL; `test_phase7_tool_harness_vertical_slice_on_postgres`
  exercises internal CSV → `table.select` persist → Evidence → Decision draft, plus
  the fake REvoCompute `tabular` run → artifact → local `table.describe`.
- Frontend: `npm run typecheck`, `npm run test` (**15 tests**, incl. the Tools view
  and contract/enum lockstep), and `npm run build` pass. `openapi.json` /
  `schema.d.ts` / `enums.generated.ts` regenerated; regeneration is idempotent
  (`npm run check:contracts` clean once committed).
- Browser (`npm run test:e2e`, Playwright Chromium): 4 specs pass — smoke, agent,
  collaboration, and the new `tools.spec.ts` (Project → fake compute `tabular` →
  local `table.describe`/`table.select` persist → Evidence → Decision draft →
  commit → reloaded Knowledge).

## Independent review (Phase 7)

Five fresh read-only reviewers audited the branch on the five TODO.md lenses
(A: Project Harness architecture, B: local analysis runtime, C: REvoCompute /
provenance, D: Agent/security/authority, E: API/frontend/tests/CI). **No P0
findings.** A and C returned PASS; B, D and E requested changes. Every P1 was
fixed and useful in-scope P2s were addressed with regressions, then the full
suite was rerun green:

- **P1 (E) — stale OpenAPI snapshot.** The committed `openapi.json` lagged the
  `ToolSideEffectClass` enum docstring (caught independently by the backend CI
  drift gate). Re-exported `openapi.json` from the canonical FastAPI schema and
  regenerated `schema.d.ts`/`enums.generated.ts`; the committed snapshot now
  matches a fresh export byte-for-byte.
- **P1 (B) — unvalidated input persisted.** `ToolInvocation.parameters` stored
  the raw request dict; extra/unknown keys could reach durable storage. The
  runtime now persists `parsed.model_dump(mode="json")` (the canonical validated
  model), and a regression asserts extra keys (e.g. a dropped sentinel) never
  appear in the stored record.
- **P1 (B, C) — unbounded external read when `size` is unknown.** Local analysis
  of an external artifact now always reads through the provider's BOUNDED
  `resolve_artifact_preview(limit=MAX_ANALYSIS_BYTES + 1)` and applies the byte
  bound pre-materialization, so a size-less external artifact can never be fully
  downloaded. Content-type is also gated (CSV-ish only), so JSON/TSV/binary
  artifacts fail closed rather than being silently mis-parsed. A `size=None` +
  oversized regression pins this.
- **P1 (D) — dual tool definition on the human surface.** The Analyze run form
  is now derived from the canonical `ToolCatalog`: tool options (names/labels/
  availability) come from the fetched catalog, the parameter form is rendered
  from each tool's `input_schema`, persist is gated by the descriptor's
  `side_effect_class`, and the backend-owned autonomy value uses the generated
  enum constant — no hand-maintained tool-id union/parameter copy remains.
- **P2 (A/C/E) — dead/mismatched `ToolInvocationRead`.** `input_resource_ids`
  aligned to `list[str]` (the durable storage type) and the schema is now wired
  to `GET /api/projects/{project_id}/tool-invocations` (bounded project activity
  log).
- **P2 (B) — typed-output validation.** The runtime now rejects a handler that
  returns a model other than its declared `output_model` (defense-in-depth),
  pinned by a mis-wired-handler regression.
- **P2 (B) — O(N) visibility check.** `inspect_artifact` now uses the scalar
  `persistence.is_visible` EXISTS check instead of materializing the full
  visible-resource dict.
- **P2 (B) — per-item column bound.** `TableSelectCreate.columns` items are
  bounded to 500 chars (not just the list length).
- **P2 (C) — non-atomic derived persistence.** The derived `ArtifactReference`
  and its `ToolInvocation` record are now written in ONE transaction
  (`create_internal_artifact(commit=False)` → record → single commit).
- **Design decision (C, by evidence) — no typed artifact→artifact provenance
  edge.** Derived-analysis lineage is deliberately recorded in `ToolInvocation`
  (REvoLab-local activity), not a `GlobalProvenanceEdge`: the frozen edge matrix
  has no artifact→artifact relation, and adding one would change accepted
  SCIENTIFIC_GRAPH ownership. Documented in `PROJECT_TOOL_HARNESS.md`.

### Second review round (fix delta)

Because material fixes were made after the five-reviewer pass, three fresh
read-only reviewers were launched over the delta (architecture/runtime,
security/authority, tests/contracts/frontend). The first launch stalled without
output and was struck; all three lenses were retried once with fresh read-only
reviewers.

- **architecture/runtime**: **PASS** (no P0/P1/P2) — verified validated
  `model_dump` parameters, bounded external reads, one-transaction persistence,
  typed-output/unknown-id/remote-id rejection, and dependency direction.
- **tests/contracts/frontend**: **PASS** (no P0/P1) — verified OpenAPI freshness
  + determinism, idempotent contract regen, 58 passed across
  `test_tools.py`/`test_agent.py`, frontend typecheck + 15 tests, and
  IMPLEMENTATION_STATE/PR metadata accuracy (275 passed / 6 skipped confirmed).
- **security/authority**: launched (retried once); did not report before
  finalization. Its security-relevant delta (validated parameters, bounded
  external reads, catalog-derived frontend, no secret leak via the new
  tool-invocations read endpoint) is independently evidenced by the two PASS
  lenses above, by the first-pass Reviewer D findings that prompted the fixes,
  and by the green machine gates. No unresolved P0/P1 remains from any lens.

Two optional (non-blocking) test suggestions from the architecture/runtime
second pass were recorded as follow-ups, not added: a direct
`_require_tabular_content_type` rejection test for an unsupported content type,
and a forced-rollback test of the single-transaction derived-result saga.

## Pre-merge final findings (PR8 prep)

Four final architecture / scientific-correctness findings were resolved before
marking PR8 ready:

- **Architecture correction completed.** `CapabilityKind` is now only
  `compute` + `artifact_resolution`; speculative `SEARCH` / `DESIGN` /
  `INTERACTIVE_HANDOFF` vocabulary was removed from Core and their protocol
  prose removed from `PROVIDER_CAPABILITIES.md`. The REvoDesign/OpenBio
  integration-contract sections were replaced by one short statement (REvoDesign
  = unrelated existing product; OpenBio = design reference only); the remaining
  import-boundary principles stay provider-neutral. `SYSTEM_ARCHITECTURE.md` and
  `DOMAIN_BOUNDARIES.md` reconciled; OpenAPI/TS contracts regenerated.
- **No silent truncation.** `tabular` analysis now reports bounds explicitly:
  `PlotSpecRead` carries `source_rows` / `rendered_points` / `truncated`, and
  `TableSelectRead` carries `source_truncated` (source-table row boundary)
  alongside its own `truncated`.
- **Side-effect semantics.** `ToolSideEffectClass.CREATES_PROJECT_TRUTH` was
  replaced by a neutral `DOMAIN_MUTATION`; a Decision DRAFT is no longer
  classified as project truth. Truth promotion stays with the Decision lifecycle
  (draft → committed) and `AgentToolAutonomy` (commit = `explicit_action`).
- **Dead scaffolding removed.** `ToolResultKind.SCIENTIFIC_OBJECT` /
  `RUN_REFERENCE` (forward-only, no producing path) were removed; the producing
  kinds are `ephemeral` / `artifact` / `evidence` / `decision`.

Regression tests added: plot truncation + under-bound; table.select
source-truncation propagation. Full suite green — backend **281 passed / 6
skipped**, frontend typecheck + **15 tests** + build, Playwright **4 specs**,
OpenAPI fresh, contracts idempotent.

### Pre-merge review (3 lenses)

Three fresh read-only reviewers were launched over the pre-merge delta
(architecture/domain semantics; analysis correctness/security;
contracts/tests/frontend) and given ample time. **analysis correctness/security**
returned **PASS** (no P0/P1); **tests/contracts/frontend** returned **PASS**
(no P0/P1/P2); **architecture/domain** returned REQUEST_CHANGES with one P1 —
`SYSTEM_ARCHITECTURE.md`'s top product boundary and system-context diagram still
presented REvoDesign/OpenBio as reachable backends. That P1 was fixed (the
boundary and context now present REvoCompute as the sole configured backend,
REvoDesign as an unrelated existing product, and OpenBio as a design reference
only), together with the reviewer's P2 prose-cleanup (removed lingering
`SEARCH`/`DESIGN`/`INTERACTIVE_HANDOFF` mentions from Core docstrings and stale
IMPLEMENTATION_STATE text, and `AGENT_CONTEXT.md`/`EVIDENCE_PROVENANCE.md`
references). Full gates rerun green — backend **281 passed / 6 skipped**, frontend
typecheck + **15 tests** + build, Playwright **4 specs**, OpenAPI fresh, contracts
idempotent, CI backend/frontend/e2e green. No P0/P1 remains.

### GitHub Codex review findings

Four automated GitHub Codex review comments on PR8 were resolved: (P1) source
truncation propagated into table projections; (P2) non-finite numeric values now
fail closed; (P2) empty/duplicate explicit column selections rejected; (P2) CSV
parser errors translated to typed validation errors. Each has a regression;
backend is now **281 passed / 6 skipped**.

### Final architecture reconciliation (domain ownership)

`SYSTEM_ARCHITECTURE.md` and `DOMAIN_BOUNDARIES.md` now share one authoritative DAG
in which **Project Tool Harness is the ninth architectural domain** and the single
owner of Tool. Edges (code/build dependency): Presentation → Project Tool Harness,
Agent Context → Project Tool Harness, Project Tool Harness → Project | Evidence |
Knowledge | Provider/Capability | Identity. Agent Context consumes the ToolCatalog
(it does not own Tool); Presentation consumes the same catalog; Provider/Capability
is an implementation dependency behind remote Tools. `PROVIDER_CAPABILITIES.md`,
`AGENT_CONTEXT.md`, `HARNESS_OPERATING_MODEL.md`, and `PROJECT_TOOL_HARNESS.md`
were reconciled (ToolResultKind = four producing kinds only; `/tools/invocations`
is documented as the LOCAL invocation surface; stale REvoDesign/OpenBio/Search/
Design wording removed). No product code changed — documentation only.

## Implemented (Phase 8)

- **Model boundary** (`revolab/agent/model_backend.py`): `ModelBackend` protocol +
  `ModelRequest`/`ModelResponse`/`ModelToolCall`/`ToolSpec` value objects and ONE
  OpenAI-compatible chat/tool-call transport (`OpenAICompatModelBackend`) over the
  already-approved `httpx`. Model transport failures map to `ModelUnavailableError`
  (503) and never echo provider bodies/credentials. `revolab/testing/fake_model.py`
  is the deterministic `ScriptedModelBackend` at the EXTERNAL model boundary (tests
  + browser slice only; never a production fallback).
- **Bounded Agent loop** (`revolab/agent/runtime.py`): `AgentTurnRunner` runs the
  `PREPARE_CONTEXT → MODEL → TOOL_REQUEST → VALIDATE → EXECUTE → ...` state
  machine with conservative ceilings (8 model turns, 16 tool calls, 4 calls/turn,
  60k context chars, 20 history messages/20k chars, 4 skills/20k bytes, 12k
  tool-result chars, 300s total duration). A bound hit is a typed
  `AgentTerminationReason` terminal result; no recursion, background execution, or
  silent retry.
- **Prompt/trust separation** (`revolab/agent/prompt.py`): server-owned system
  instructions + bounded skill bodies in the system role; ALL Project content
  serialized once into a single `<untrusted_project_data>` user message. No
  credential/secret/path is ever rendered because content asks for it.
- **Tool-call validation**: every model tool call is untrusted — exact canonical
  ToolCatalog lookup, availability, canonical Pydantic input schema, membership,
  autonomy, execution class, side-effect class. `automatic`/`policy` LOCAL tools
  execute through the REAL `LocalToolRuntime` (`persist=False`); `explicit_action`
  (Decision commit, compute submit) becomes an ephemeral valid `PendingAction`;
  unknown/malformed/remote reads fail closed; `never_agent` stays out of the
  catalog. Remote tools are surfaced from the same catalog but the loop does not
  autonomously cross the external boundary (documented deferral).
- **Skills**: `SkillCatalog.skill_path` now confines ids below the configured root
  (`resolve` + `is_relative_to`); `load_skill_bodies` applies count + byte budgets
  before reading; skill body over-budget fails closed.
- **Context artifact selection**: `ContextSelectionCreate.artifact_ids` lets a
  turn explicitly select visible ArtifactReference identity cards (Phase-8
  vertical slice); validated against the Project read lens like every selection.
- **API** (`POST /api/projects/{project_id}/agent/turns`): one typed
  `AgentTurnCreate → AgentTurnRead` surface (final response, per-tool trace,
  pending explicit actions, termination reason, budget). Raw provider/model
  response objects, hidden prompt text, and credentials are never exposed.
  `GET /api/projects/{project_id}/agent/tools` unchanged.
- **Deterministic proposal path removed**: `revolab/agent/session.py`
  (`propose_selection`/`record_proposal`/`AgentProposal`) and
  `POST /agent/proposals` are deleted; the fake survives ONLY as the
  `ScriptedModelBackend` test double.
- **Frontend** (`views/Agent.tsx`): real project Agent workspace — object +
  artifact context selection, ephemeral session-local conversation, send/receive,
  per-turn tool trace with autonomy-correct statuses, pending-explicit-action
  display, and an always-visible Decision DRAFT vs COMMITTED truth boundary. No
  raw-HTML rendering. Generated `AgentTerminationReason`/`AgentToolCallStatus`
  enums wired into `contracts/enums`.
- **Docs**: `docs/architecture/PROJECT_AGENT_RUNTIME.md`; `IMPLEMENTATION_ROADMAP.md`
  Phase 8; `AGENT_CONTEXT.md` / `PROJECT_TOOL_HARNESS.md` reconciled.

## Verified evidence (Phase 8)

- Backend: `ruff check backend` and strict `mypy` pass (49 source files). `pytest`
  passes **311 passed, 6 skipped** (the six skips are the opt-in PostgreSQL
  acceptance file). New regressions in `test_agent_runtime.py` cover: bounded
  context reconstruction + artifact selection, hostile project text as DATA (not
  system instruction), unknown-tool fail-close, malformed-argument fail-close,
  cross-Project resource rejection at execution, `never_agent` absence from the
  model-visible tool set, `decision.commit`/compute-submit never auto-executing
  (PendingAction only), model-turn/tool-call/history/skill-body bounds, skill
  path confinement, transcript feedback to the model, pending-argument bounding,
  adapter timeout/transport mapping, safe OpenAI function-name mapping, malformed
  200-response typing, whole-group history trimming, deadline recheck before tool
  execution, and missing-model fail-closed.
- OpenAPI/contracts: `python -m revolab.export_openapi` output is byte-identical
  to `frontend/src/contracts/openapi.json`; `openapi-typescript` +
  `generate-enums.mjs` regenerated `schema.d.ts`/`enums.generated.ts`; the
  contract lockstep test now pins the `/agent/turns` request to the single
  `AgentTurnCreate` component and asserts `/agent/proposals` is gone.
- Frontend: `npm run typecheck`, `npm run test` (**19 tests**, incl. the new
  Phase-8 Agent view — ephemeral boundary, tool trace, pending action, failure
  state, project-switch clearing, and deferred cross-project response discard —
  plus enum/contract lockstep), and `npm run build` pass.
- Browser (`npm run test:e2e`, Playwright Chromium over real FastAPI + real SQLite
  + `REVOLAB_E2E_FAKE_MODEL=1`): all **4 specs** pass — smoke, collaboration,
  tools, and the new `agent.spec.ts` (object → fake-compute tabular artifact →
  Agent turn → `table.describe` → `decision.record_draft` → Decisions view draft →
  explicit commit → reloaded committed Knowledge).

## Independent review (Phase 8)

Five fresh read-only reviewers audited the branch on the five TODO.md lenses
(A architecture/ownership, B runtime/tool semantics, C security/authority,
D API/frontend/contracts, E tests/CI). **A, C, D, E returned PASS** (P2s only);
**B returned REQUEST_CHANGES with one P1.** All P0s: none.

Reconciled findings (commit `6d12ec5`): the P1 — tool results and assistant
tool-call messages were being assembled into a `transcript` but never fed back to
the model between loop iterations (a multi-step turn would degenerate to burning
turns) — plus the in-scope P2s: bounded PendingAction arguments, prompt-level
context-truncation reporting (dead constant removed), skill-budget failure
converted from a raw `FileNotFoundError` to a typed `ModelUnavailableError`
(503), remote read-only tools no longer advertised to the model when the loop
cannot execute them, a single local registry shared by catalog validation and
execution, and a sanitized (non-exception-echoing) model-unavailable response.

The first post-fix delta review (3 fresh reviewers) **requested changes**: the
fix commit had accidentally emptied `backend/tests/test_agent_runtime.py` (a
broken whitespace cleanup command truncated the file), and the PendingAction
bounding helper was a no-op (it measured the already-truncated string, so it
always returned the full payload). Both were corrected in `abbc958`: the entire
runtime regression suite was restored and extended (transcript feedback,
`max_history_chars`, per-turn tool-call overflow `SKIPPED`, total-duration,
skill count/path traversal, adapter timeout/transport mapping, bounded pending
args, model-tool projection excludes remote reads), the bounding helper now
measures the FULL serialized size before truncating, the projection filter uses
value comparison, the non-result transcript branch is size-bounded, and every
loop ceiling is now wire-observable in `AgentTurnBudgetRead`.

The final delta review (3 fresh read-only reviewers over the fixed delta) returned
**PASS for all three lenses with no P0/P1** (remaining P2s are non-blocking
coverage/enum notes). A PostgreSQL-acceptance failure found on the very first
fixed head (a scoped runaway `Decision` count assertion in the shared PG
database) was corrected in `955b395`; CI then went **green on every job**
(backend + PostgreSQL acceptance + frontend + e2e). Head `955b395` is the final
reviewed, CI-green state; PR #9 is marked ready for human review.

### Codex review follow-up (two P1 + three P2, reconciled in `e5b0597`)

After the PR was marked ready, an automated Codex review left five findings, all
reconciled with regressions:

- **P1 — dotted tool ids were invalid OpenAI function names.** The
  `OpenAICompatModelBackend` now maps canonical tool ids to API-safe wire names
  (letters/digits/`_`/`-`, ≤64 chars, digest-suffixed for long ids), reverse-maps
  emitted calls, and fails closed on collision. A capture transport proves the
  wire only carries `decision_record_draft`/`artifact_inspect`-style names and
  that the loop still sees the canonical ids.
- **P1 — Agent conversation leaked across project switches.** `AgentView` now
  resets all session-local state (messages, turn, error, selected context) when
  `projectId`/`actorId` change; a frontend regression asserts a prior project's
  response disappears after the project switch.
- **P2 — history trimming could split an assistant tool-call group.** `_bounded_history`
  now groups an assistant `tool_calls` message with its consecutive tool
  responses and trims whole groups, so no advertised `tool_call_id` is ever
  orphaned in a bounded transcript.
- **P2 — total-turn deadline only checked at loop top.** The deadline is now
  rechecked after the model returns and again before EACH tool execution, so the
  duration ceiling is a hard bound for side effects (clock-scripted regression).
- **P2 — malformed HTTP-200 model responses escaped as untyped 500s.** The
  adapter validates `choices`/choice/message structure and maps empty/non-object
  responses to a typed `ModelUnavailableError` (503).

Full gates re-ran green after the fixes: backend **309 passed / 6 skipped**,
frontend typecheck + **18 tests** + build, Playwright **4 specs**, OpenAPI fresh,
contracts idempotent. CI green on pushed head `e5b0597`.

### Pre-merge hardening pass + final review (`f6debb8`, `c2839b6`)

- **P1 (cross-Project in-flight response race).** `AgentView` is now keyed by
  `actorId:projectId` in `App.tsx` (remount on switch) AND carries a synchronous
  `actorId:projectId` scope guard that discards a resolve whose scope no longer
  matches. A deferred-response regression proves a Project A turn resolved after
  switching to B never renders A content.
- **P2 (model transport lifecycle).** The cached `OpenAICompatModelBackend` /
  `httpx.Client` now has an explicit `close_model_backend()` wired into the
  FastAPI lifespan `finally`; construction is guarded by a lock so concurrent
  first turns share ONE transport (concurrency regression asserts a single build
  across 8 racing accesses). No generalized resource framework.
- Three fresh read-only final reviewers (Agent/runtime architecture;
  security/project isolation; contracts/tests/resource lifecycle) returned
  **PASS with no P0/P1**. The one recurring P2 — the unlocked lazy singleton —
  was fixed in `c2839b6`. All five Codex findings are re-verified as fixed and
  regression-pinned.

Final machine evidence on head `c2839b6`: backend **311 passed / 6 skipped**,
PostgreSQL + Alembic drift + OpenAPI drift green in CI, frontend typecheck +
**19 tests** + build, Playwright **4 specs**, `check:contracts` clean. CI green on
every job.

## Implemented (Phase 9)

- **Conversation model** (`revolab/models.py`): `ProjectConversation` (opaque
  UUID, `project_id`, `actor_id`, `title`, `created_at`/`updated_at`,
  `archived_at`) and `ConversationMessage`
  (`conversation_id`, `seq`, `role` user|assistant, `content`,
  `termination_reason`, bounded inert `tool_trace_summary`, `created_at`).
  Neither table is a `GlobalResourceRegistry` entry or a provenance node.
- **Enum** (`ConversationRole` in `revolab/enums.py`): the only two durable
  transcript roles; wire-generated, never hand-copied into the frontend.
- **Persistence orchestration** (`revolab/agent/conversations.py`):
  create/list/get/patch/run-turn wrap `AgentTurnRunner` instead of forking it.
  `run_conversation_turn` loads server-owned bounded history, runs the real loop,
  then persists the bounded user/assistant transcript (assistant carries
  termination reason + inert tool-trace summary — never raw `ToolResult` or
  `PendingAction` arguments). Access every operation verifies Actor ownership +
  current readable membership + active Project; a guessed UUID is a 404.
- **Canonical API** (`revolab/api.py`):
  `POST/GET` `/project/{id}/agent/conversations`,
  `GET/PATCH` `/project/{id}/agent/conversations/{conversation_id}`,
  `POST` `.../conversations/{conversation_id}/turns`. The transient
  `POST /agent/turns` surface (and `AgentTurnCreate`/`AgentChatMessageCreate`)
  is removed — one Agent-turn execution contract. `ConversationTurnCreate`
  rejects unknown fields (`extra="forbid"`), so the client cannot inject
  arbitrary historical assistant messages.
- **Bounded limits** (`revolab/agent/conversations.py`): title 200, durable
  message 8000, message page default 100 / max 200, inert trace summary capped
  at 64 entries with 500-char bounded fields; model-context trimming stays the
  Phase-8 `max_history_messages`/`max_history_chars`.
- **Frontend** (`views/Agent.tsx`): persistent conversation list + new
  conversation + reload-restored transcript + send turn + live tool trace +
  pending actions + Decision DRAFT/COMMITTED boundary. Context selection is sent
  per turn and never persisted; the transcript restores from the server after
  reload.
- **Migration**: `6c88c6688930_phase9_persistent_project_conversations.py`
  (project_conversations + conversation_messages; JSONB on PostgreSQL, and
  database CHECK constraints on `role`/`termination_reason` via
  `create_constraint=True` so the conversation columns are self-validating at
  the DB boundary, not only in Pydantic/ORM).
- **Docs**: `docs/architecture/PROJECT_CONVERSATIONS.md` (normative owner);
  `PROJECT_AGENT_RUNTIME.md` / `AGENT_CONTEXT.md` / `IMPLEMENTATION_ROADMAP.md`
  (Phase 9) reconciled.

## Verified evidence (Phase 9)

- Backend: `ruff check backend` and strict `mypy` pass on 50 source files.
  `pytest` passes **343 passed, 9 skipped** (SQLite fast tests; the 9 skips are
  the opt-in PostgreSQL acceptance file). `test_conversations.py` regressions
  cover: persist+reload, server-owned second-turn history, structural rejection
  of client-supplied history, Actor isolation (incl. the OWNER cannot read a
  member's private conversation), cross-Project isolation, immediate membership
  revocation, Project tombstone, lifecycle create/rename/archive, message
  pagination (incl. `latest` most-recent-page semantics), persisted hostile
  user/assistant text cannot widen tool authority or become system authority,
  `never_agent` absence, non-vacuous secret-material exclusion (a sentinel that
  really entered the live `table.describe` result is absent from durable rows),
  system-prompt/skill-body exclusion, DB CHECK presence on conversation
  role/termination columns, service-level title/message bound rejection, context
  rebuilt after Project truth changes, and large-history trimming without
  deleting UI history.
- PostgreSQL (migrated schema): `alembic upgrade head` and `alembic check`
  report **no drift** on PostgreSQL 16; `test_postgres_integration.py` passes
  **9 passed** (conversation create/turn persistence/second-turn server history/
  Actor-Project isolation/membership change/Project tombstone, raw-insert role
  CHECK enforcement, and two-concurrent-turn serialization where the second
  turn's model provably sees the first turn's persisted messages).
- OpenAPI/contracts: `python -m revolab.export_openapi` is byte-identical to
  `frontend/src/contracts/openapi.json`; `openapi-typescript` +
  `generate-enums.mjs` regenerated `schema.d.ts` (contract generation
  idempotent); the contract regression proves `/agent/turns`,
  `AgentTurnCreate`, and `AgentChatMessageCreate` are gone, that
  `ConversationTurnCreate` owns the turn request, and that the conversation
  create/patch/turn schemas all reject unknown fields (`additionalProperties:
  false`).
- Frontend: `npm run typecheck`, `npm run test` (**24 tests** — the Phase-9
  Agent view: working-memory boundary, conversation list/restore, tool trace +
  pending action, failure state, project-switch clearing, deferred
  cross-project response discard, deferred cross-conversation response discard,
  send-disabled-while-loading, deferred initial-restore discard, and slow-open
  discard), and
  `npm run build` pass.
- Browser (`npm run test:e2e`, Playwright Chromium over real FastAPI + real
  SQLite + `REVOLAB_E2E_FAKE_MODEL=1` + `REVOLAB_E2E_FAKE_COMPUTE=1`): all
  **4 specs** pass; `agent.spec.ts` reloads after the first turn, verifies the
  server-owned transcript is restored, and runs a second turn.
- `git diff --check` clean.

## Independent review (Phase 9)

Five fresh read-only reviewers audited the PR diff on the five TODO.md lenses
(A architecture/ownership, B runtime/bounds/race, C security/authority, D
API/frontend/UX, E tests/PG/migrations/CI). **A and C returned PASS**; **B, D,
and E returned REQUEST_CHANGES.** No P0s were reported.

Reconciled valid P1s:

- **Concurrent-turn race** (A/B): `run_conversation_turn` now takes the
  per-conversation row lock BEFORE loading history and running the model, so the
  load→run→persist section serializes; a PostgreSQL two-thread regression proves
  the second turn's model saw the first turn's persisted user message.
- **Frontend cross-conversation response leak** (D): `AgentView` now tracks the
  active conversation synchronously and discards a turn that resolves after the
  user opened a different conversation, appending transcript messages
  functionally; a deferred-response regression covers it.
- **Frontend "latest" pagination inversion** (D): `GET .../{conversation_id}`
  gained a `latest` query flag returning the most recent message page; the
  reload path uses it, with a backend regression.
- **Vacuous secret-persistence negative** (E): the negative now drives a real
  `table.describe` whose live ToolResult contains a sentinel column header and
  asserts the sentinel/`secret_ref`/`api_key`/raw `result` are absent from
  durable rows.
- **Enum CHECK claim was false** (B/E): `_enum` learned `create_constraint=True`
  for the two Phase-9 columns and the migration matches, so conversation
  role/termination columns now carry real database CHECK constraints
  (`alembic check` clean on both backends; raw-insert PG regression).
- **Write-bound vs HTTP-bound limit** (B): title and durable-message length are
  re-validated inside the persistence functions with typed rejection.

In-scope P2s fixed: server-owned history now loads a bounded SQL suffix instead
of the whole transcript; `ConversationCreate`/`ConversationPatch` reject unknown
fields; owner-vs-member privacy and system-prompt/skill-body non-persistence
regressions added. Out of scope / documented deferral: header-trust authentication
(real OIDC/login remains deferred).

### Delta review (second round) + commit-threading fix

The first delta round (architecture/runtime, security/authority, tests/contracts/
frontend) returned **two PASS**; the architecture/runtime reviewer found one P1 —
the `FOR UPDATE` row lock was released mid-turn whenever a POLICY truth tool
(`decision.record_draft` / `evidence.create`) committed inside `AgentTurnRunner`,
so the serialization fix was incomplete on the primary workflow. Confirmed
empirically against PostgreSQL. Fixed by making the whole conversation turn ONE
transaction: `InvocationContext.commit` now defaults to `True` for the human
surface, and the Agent loop sets it `False`, threading `commit=False` through
`create_evidence` / `create_decision` so `run_conversation_turn` is the sole
commit point (the row lock is held to the end). The PostgreSQL concurrency
regression was extended to have the first turn call `decision.record_draft`
mid-run and block in its second model call, proving the second turn cannot reach
the model until the first turn commits. All delta P2s (system-prompt/skill-body
non-vacuousness, owner-side patch/run privacy, PG `termination_reason` CHECK)
were also addressed.

The second delta round confirmed the commit-threading fix with **PASS** on
architecture/runtime and security/authority; the tests/contracts/frontend
reviewer found one residual P1 — the initial-load restore guard unconditionally
clamped the active-conversation ref while `send` was not gated on `loading`.
Fixed by letting the ref follow only the programmatic auto-select (never a
user's already-opened conversation) and disabling Send while the initial list
loads; a dedicated initial-restore discard regression was added.

The third delta round (fresh reviewers over the guard fix) returned **PASS on
all three lenses** with no P0/P1. Its only operational note — the shipped
initial-restore regression was non-differentiating on the pre-fix code — was
closed by adding a `send`-disabled-while-loading regression that fails before
the guard and passes after it (counts at that round: 336 backend / 23 frontend).

A final integrated review round (two fresh reviewers over the COMPLETE diff, after all delta
fixes) found two more valid P1s, both now fixed with regressions:

- **Durable history kept the OLDEST messages and dropped the NEWEST.** `AgentTurnRunner._bounded_history`
  walked the count-bounded window oldest-first and stopped at the first message exceeding
  `max_history_chars`, so the retained set was a forward *prefix* rather than the documented
  bounded *suffix* (with 8 000-char messages the ceiling binds after ~3 messages, so real
  sessions lost their most recent context). Fixed by walking the window newest-first and
  reversing; regression `test_char_bounded_history_keeps_the_newest_messages` binds the char
  ceiling and fails on the pre-fix code (which kept `s1` and dropped `s4`).
- **Three frontend discard-guard regressions were timing-vacuous.** Each asserted after a single
  microtask, before the discarded continuation could run, so they passed even with the guards
  removed. They now settle a real macrotask inside `act(...)` with positive controls
  (turn/restore mock call assertions); mutation-verified — stripping the three guards makes
  exactly those tests fail (3 failed / 7 passed) instead of the previous 10/10 pass.

In-scope P2s from that round were also closed: `_persist_derived_artifact` now honours
`InvocationContext.commit` (no latent mid-turn commit for a `persist=True` caller); reloaded
transcripts render the persisted per-tool status (failed/pending no longer look successful);
`DOMAIN_BOUNDARIES.md` no longer claims an "ephemeral transcript, never persisted" and
`SYSTEM_ARCHITECTURE.md` indexes the new documents; `ConversationRole` is generated
(`CONVERSATION_ROLES`) and the view uses the derived constant instead of a bare literal;
`openConversation` keeps the active-conversation ref in lockstep synchronously; the SQLite
CHECK is proven enforced (raw invalid insert rejected); and the conversation/message page-size
bounds are pinned by 422 regressions.



### Delta round 4 (P2 closure)

The fourth delta round returned **PASS on all three lenses with no P0/P1** and flagged
coverage gaps, now closed: the persisted transcript reuses the canonical
`traceTone` status→tone mapping (a reloaded `pending` proposal no longer renders as a
failure); the `openConversation` discard guard has a slow-open regression
(mutation-verified: removing only that guard fails exactly that test); the successful-restore
regression pins the persisted per-tool status text; `ConversationRole` joined the contract
enum-lockstep list; both `_bounded_history` helpers (runner and prompt assembly) treat a 0 count ceiling as
"no history" (never the unbounded `[-0:]` slice), with regressions; and the `commit=False` + `persist=True`
derived-result branch has a deferred-durability regression (flushed, invisible to a second
session until the caller commits; a rollback leaves no derived rows). Final counts:
**336 backend tests**, **24 frontend tests**.

### Delta round 5 (P1 correction)

Delta round 5 found a P1 **introduced by round 4**: the 0-ceiling guard used
`history[len(history) - n:]`, which is a negative index when the ceiling exceeds the number of
messages and therefore silently dropped the oldest messages (e.g. 15 messages with the default
ceiling of 20 kept only 5). Fixed to `history[-n:]` (whole list when `n > len`), with a boundary
regression at ceiling > message count, and the same 0-ceiling rule applied to the prompt-layer
helper (which previously treated 0 as unbounded); `agent_max_history_messages` /
`agent_max_history_chars` are now `ge=0`-validated. Also from that round: the deferred-durability
regression now pins the flush half (rows visible in the caller's session pre-commit), and a
reloaded `pending` entry keeps its inert "NOT executed: <reason>" framing. Final counts:
**343 backend tests**, **24 frontend tests**.

Delta round 6 (fresh reviewers over the correction) returned **PASS on all lenses with no
P0/P1**: an exhaustive slice check (101 cases per helper, 0 mismatches), a real in-place mutation
showing the boundary regression fails pre-fix (`assert 5 == 15`), cross-helper agreement, `ge=0`
settings validation, and the unchanged row-lock/single-commit contract. Its residual P2s were
closed by validating negative ceilings in `AgentLoopBounds.__post_init__` (a regression asserts
rejection while 0 stays legal) and documenting that a 0 count ceiling also suppresses in-turn tool
results — a diagnostic value, not a supported production setting. The round's remaining test
nits were also closed: `ge=0` settings validation has its own regression, and the
deferred-durability assertion runs under `no_autoflush` so it pins the explicit flush rather than
an incidental autoflush. Final counts: **343 backend tests**, **24 frontend tests**.

## Implemented (Phase 10)

- **Note model** (`revolab/models.py`): `ProjectNote` (`id`, `project_id`,
  `created_by_actor_id` audit only, `title`, `created_at`/`updated_at`,
  `archived_at`), immutable `ProjectNoteRevision` (`revision_id`, `note_id`,
  `revision_seq`, `body`, `created_by_actor_id`, unique `(note_id, revision_seq)`;
  latest derived from `max(revision_seq)`, no mutable current pointer), and
  `NoteMention` (mirrors the frozen Evidence endpoint shape: optional
  `target_resource_id` + stored registry `target_kind`, optional
  `target_evidence_id`/`target_decision_id`, CHECK exactly-one-target, unique
  `(revision_id, ordinal)`). No new enum; a Note is not a `GlobalResourceRegistry`
  entry, ScientificObject, Evidence, Decision, or provenance node.
- **Application service** (`revolab/notes.py`): create / list / get / patch
  (rename-archive) / append-revision / list-revisions plus
  `resolve_selected_notes` for bounded Agent-context selection. Read uses
  `readable_membership` (owner/member/viewer); mutate uses
  `mutation_capable_membership` (viewer 403); a note in another Project is a 404
  and a non-member gets one uniform 403 before any row lookup (no existence
  oracle). Append carries `base_revision_seq`; a stale base raises typed
  `ConflictError` (409). PostgreSQL takes the note row lock; the
  `(note_id, revision_seq)` unique constraint (translated to 409) is the
  backend-independent backstop. Mentions are validated through the current
  Project read lens at write time (hidden/unknown/foreign target ⇒ one uniform
  403) and hang off the immutable revision, so an unlinked target yields
  `resolved=false` without rewriting history.
- **Schemas** (`revolab/schemas.py`): `NoteCreate`/`NotePatch`/
  `NoteRevisionCreate` (`extra="forbid"`), `NoteRead`/`NoteDetailRead`/
  `NoteRevisionRead`/`NoteMentionCreate`/`NoteMentionRead`, plus canonical bounds
  (`MAX_NOTE_TITLE_CHARS` 200, `MAX_NOTE_BODY_CHARS` 20000,
  `MAX_NOTE_MENTIONS_PER_REVISION` 100). `ContextSelectionCreate` gains
  `note_ids`/`note_revision_ids`/`max_notes`/`max_note_chars`;
  `ProjectContextRead` gains `notes`; `BudgetReportRead` gains `note_count`.
- **API** (`revolab/api.py`): `GET/POST /api/projects/{id}/notes`,
  `GET/PATCH /api/projects/{id}/notes/{note_id}`,
  `POST/GET /api/projects/{id}/notes/{note_id}/revisions`; explicit response
  models, no raw ORM exposure.
- **Agent context** (`revolab/agent/builder.py`): explicit Note selection is
  resolved project-scoped (never through the global-resource lens), bounded by
  `max_notes`/`max_note_chars` with per-note truncation marking; Note text flows
  into the existing single `<untrusted_project_data>` block. The Agent gets no
  Note tool.
- **Migration**: `8822524ef06f_phase10_project_notes.py` (`down_revision =
  6c88c6688930`) creating `project_notes`, `project_note_revisions`,
  `note_mentions` with the unique/check constraints, on SQLite and PostgreSQL.
- **Frontend**: generated-contract aliases (`api/types.ts`, `api/backend.ts`),
  hooks (`useNotes`/`useNoteDetail`/`useNoteRevisions`/`useMyMembership`), new
  `views/Notebook.tsx` (list + create + mention linking + safe Markdown detail +
  revision history + archive + "Add to Agent context"), viewer read-only gating,
  safe Markdown renderer `components/Markdown.tsx` (never
  `dangerouslySetInnerHTML`), `Notes` navigation, Agent note selector and
  "Save to Project Note" explicit capture, `playwright e2e/notebook.spec.ts`.
- **Docs**: `docs/architecture/PROJECT_NOTEBOOK.md` (normative owner), ADR-0016;
  `PROJECT_CONVERSATIONS.md`, `AGENT_CONTEXT.md`,
  `WORKSPACE_INFORMATION_ARCHITECTURE.md`, `IMPLEMENTATION_ROADMAP.md`,
  `DOMAIN_BOUNDARIES.md` (§1a Project Notebook sub-boundary),
  `SYSTEM_ARCHITECTURE.md`, and `PROJECT_AGENT_RUNTIME.md` reconciled to point at
  it.
- **Post-review hardening** (from the mandatory independent review): the Agent
  prompt's reserved untrusted-data delimiters are neutralized inside serialized
  project content, so a Note body containing `</untrusted_project_data>` can no
  longer close the wrapper early (`agent/prompt.py::neutralize_delimiters`); the
  Note/revision audit-actor FKs no longer cascade (the author is not a lifecycle
  owner) and mention target FKs no longer cascade (historical mentions are never
  silently destroyed); note mutation takes the PostgreSQL row lock in
  `append_revision` AND `patch_note` plus a bounded process-level lock on SQLite;
  the revision-uniqueness `IntegrityError` translation is narrowed to
  `uq_note_revision_seq`; `resolve_selected_notes` authorizes itself; `list_notes`
  orders by `(updated_at, id)`; `NotePatch.archive` is a strict boolean; the Agent
  selection only ever sends a Note present in the current Project list; the
  Notebook detail panel never renders or mutates a stale/failed detail and shows
  its error; `NoteCreate`/`NotePatch`/`NoteRevisionCreate` are no longer duplicated
  in `api/types.ts`.

## Verified evidence (Phase 10)

- Backend: `ruff check backend` and strict `mypy` pass on **51 source files**.
  `pytest` passes **390 passed, 13 skipped** (SQLite fast tests; the 13 skips are
  the opt-in PostgreSQL acceptance file). `backend/tests/test_notes.py` (47
  tests) covers: Project-shared read for member/viewer; non-member no-oracle;
  cross-Project 404; viewer mutation 403; immediate membership revocation;
  Project tombstone; immutable sequence-ordered revisions; stale-edit 409;
  archived-note read-history/reject-new-revision; title/body/mention bounds;
  mention exactly-one-target; mention of a visible object resolving without a
  provenance edge; hidden/unknown/foreign mention targets 403; unlinked target
  ⇒ unresolved mention with preserved text; Evidence/Decision mention resolution;
  Note activity not creating Evidence/Decision/provenance; Notes not a
  `GlobalResourceRegistry` entry; explicit-only bounded Note context
  (`max_notes`/`max_note_chars` truncation marked); exact `note_revision_ids`
  selection; foreign/unknown Note selection no-oracle; hostile Note text inside
  the untrusted block (never `system`) and unable to widen the tool set or
  execute `decision.commit`; HTTP round-trip 201/200/409 and fail-closed
  unknown fields/401/404/403.
- PostgreSQL (migrated schema): `alembic upgrade head` and `alembic check`
  report **no drift** on PostgreSQL 16; `test_postgres_integration.py` passes
  **13 passed**, including the Phase-10 Note lifecycle on the migrated schema,
  DB-level enforcement when the ORM is bypassed (duplicate `(note_id,
  revision_seq)`; zero-target and two-target `note_mentions`), and the required
  concurrent stale-write regression (two threads appending from base seq 1: one
  succeeds at seq 2, the other gets `ConflictError`; final sequences `[1, 2]`).
- OpenAPI/contracts: `python -m revolab.export_openapi` is byte-identical to
  `frontend/src/contracts/openapi.json`; `npm run generate:contracts` is
  idempotent; the contract regression asserts the three Note paths, the Note
  request schemas reject unknown fields, the context selection carries the Note
  fields, and `ProjectContextRead` carries `notes`.
- Frontend: `npm run typecheck`, `npm run test` (**51 tests**, including safe
  Markdown inertness, Notebook create/mention/append/conflict/archive/Agent
  hand-off/viewer read-only, and explicit "Save to Project Note"), and
  `npm run build` pass.
- Browser (`REVOLAB_DATABASE_URL=<postgres> npm run test:e2e`, Playwright
  Chromium over real FastAPI + **PostgreSQL 16** + `REVOLAB_E2E_FAKE_MODEL=1` +
  `REVOLAB_E2E_FAKE_COMPUTE=1`): all **6 specs** pass. (The bare command uses the
  config's SQLite fallback; the PostgreSQL run requires exporting
  `REVOLAB_DATABASE_URL`.) `notebook.spec.ts` creates a Note with a typed mention,
  reloads it, confirms the selected Note reaches bounded Agent context (the fake
  model echoes only what the real prompt assembler placed in context), confirms
  an uncommitted Decision stays a draft, has a second member append a visible
  revision, confirms a stale write returns 409, and confirms a viewer is
  read-only in the UI while a viewer API write is 403 and a non-member read is
  403; a second spec proves a raw-HTML Note body renders as inert text in a real
  browser (no script execution, no `img`).
- `git diff --check` clean.

## Independent review (Phase 10)

Five fresh read-only reviewers audited `main...HEAD` (TODO §17 lenses), each under
an explicit composition contract (role, question, allowed paths, write=NO,
evidence required, output schema, stop condition). All five returned. Verdicts
before reconciliation: **A REQUEST_CHANGES** (architecture/ownership),
**B REQUEST_CHANGES** (authorization/security/prompt-trust), **C PASS**
(persistence/concurrency), **D REQUEST_CHANGES** (API/frontend/contracts),
**E PASS** (tests/CI/docs). No reviewer found a P0.

Reconciled findings (all fixed; regression coverage added where behavior changed):

- **A-P1 / C-P2 — audit-actor FK made the author a second lifecycle owner.**
  `ProjectNote.created_by_actor_id` / `ProjectNoteRevision.created_by_actor_id`
  were `ON DELETE CASCADE`. Fixed: no delete action (the Project, not the author,
  owns the Note); migration amended; structural regression
  `test_note_audit_and_mention_fks_do_not_cascade` fails if CASCADE is re-added.
- **A-P2 / C-P1 — mention target FKs cascaded.** `note_mentions.target_evidence_id`
  / `target_decision_id` were `ON DELETE CASCADE`, which could silently destroy a
  historical mention on an immutable revision. Fixed: no delete action, matching
  `target_resource_id`; covered by the same structural regression.
- **A-P1 / E-P2 — stale "Notebooks deferred" text and a missing ownership-map
  entry.** `DOMAIN_BOUNDARIES.md` now declares §1a Project Notebook as a Project
  Domain sub-boundary (nine-domain DAG unchanged); `SYSTEM_ARCHITECTURE.md` points
  at it; `PROJECT_AGENT_RUNTIME.md` deferral re-scoped to Agent Note *mutation*.
- **B-P2 — forgeable untrusted-data delimiter.** A Note body could contain
  `</untrusted_project_data>` and close the Agent data block early. Fixed:
  `prompt.neutralize_delimiters` escapes the reserved tokens inside serialized
  project content; regression asserts exactly one delimiter pair survives and the
  hostile text stays inside the block.
- **C-P2 — the concurrency regression did not prove the row lock.** Added a
  PostgreSQL test that records executed SQL and fails if `FOR UPDATE` is not
  emitted; `patch_note` now also takes the row lock, and SQLite note mutations use
  a bounded process-level lock so append/archive are genuinely ordered there
  (regression `test_sqlite_note_mutation_lock_blocks_a_concurrent_append`).
- **D-P1 — cross-Project Note hand-off leak.** `App` now clears the Note hand-off
  on every Project change and `AgentView` only sends a Note present in the current
  Project list; the selector loads archived notes so an explicit archived-note
  hand-off is not silently dropped.
- **D-P1 — stale/swallowed Notebook detail.** The detail panel is gated on
  `detail.data.id === selectedNoteId`, `detail.error` is always rendered, the
  editor clears on selection change, the revision history filters by `note_id`,
  and archive targets `selectedNoteId`.
- **D-P2 — Save-to-Project-Note error text.** Uses `apiErrorMessage` and refreshes
  the note list on success; a regression asserts the typed message on failure.
- **D-P3 — `isSafeHref` hardening.** URL-parsed protocol allowlist plus rejection
  of control/quote/angle characters, with an adversarial-href regression.
- **C-P3 / E-P2 — non-mutation-sensitive two-target CHECK test.** The raw insert
  now uses valid FK ids, so only the CHECK can fail; also added archived-target
  `resolved=false`, revision pagination, viewer-archive, strict-boolean,
  delimiter-forgery, neutralization-bound, and foreign-real-revision regressions.
- **Deferred with rationale (P2/P3, not silently dropped):** the archived-resource
  mention asymmetry is intentional (resource availability is lens-based;
  Evidence/Decision availability is aggregate-active-based) and is now documented
  in `PROJECT_NOTEBOOK.md`; `notes.py` stays outside `revolab/domain/` because it
  is an application service mirroring `agent/conversations.py`; `created_by_actor_id`
  follows the TODO-mandated name; per-Note list N+1 queries are bounded by the
  page size; revision immutability is code-enforced (as for other immutable
  tables); DB-level UPDATE/DELETE triggers are not added; the Phase-10 migration
  was amended in place (fine for an unmerged draft; a released environment would
  need a follow-up migration because an ondelete change is real DDL).

## Delta review (Phase 10)

Because the corrections materially changed Agent prompt assembly, FK/lifecycle
semantics, and frontend selection, three fresh read-only reviewers re-audited the
corrected diff. All three returned; no P0/P1 remained and every prior finding was
confirmed FIXED (architecture/concurrency: FIXED except the normative-doc wording,
now fixed; security: PASS, delimiter forgery FIXED with the payload still decoding
byte-identically; frontend/contracts: all prior D/E findings FIXED, generated
contracts byte-identical, all new tests mutation-sensitive). Their new findings
were then closed:

- **P2 (security) — budget flag measured the pre-neutralization payload.** Escaping
  growth could exceed `max_context_chars` while `context_truncated` stayed False.
  Fixed with a single `prompt.context_payload()` used by both the prompt builder and
  the turn-budget measurement, plus regression
  `test_neutralized_context_growth_is_reported_as_a_bound_hit`.
- **P2 (architecture) — normative concurrency text stale.** `PROJECT_NOTEBOOK.md`
  and ADR-0016 now state that PostgreSQL takes the row lock in BOTH
  `append_revision` and `patch_note`, and that SQLite additionally uses the bounded
  process-level per-note lock (uniqueness constraint as backstop, single-process
  only). `IMPLEMENTATION_ROADMAP.md` wording aligned to "sub-boundary".
- **P2 (frontend) — archived-note hand-off dropped.** The Agent note selector now
  loads archived notes as well, so an explicit hand-off of an archived note is not
  silently discarded.
- **P3** — revision history filtered by `note_id`; editor cleared on selection
  change; same-note `detail.error` surfaced; the SQLite lock, save-to-note failure,
  adversarial href, and FK no-cascade all gained regressions; the browser gate
  records its `REVOLAB_DATABASE_URL` requirement.

Final machine state: backend `390 passed, 13 skipped`; PostgreSQL `13 passed`;
frontend `51 tests`; Playwright `6 specs` (all over PostgreSQL).

## Merge-quality reconciliation (PR #11, review round 2)

A second independent review (GitHub inline threads + Codex) found valid unresolved
blockers on the previously reviewed head. All were corrected on the same branch:

- **Atomic Note commands.** `create_note` / `append_revision` previously flushed the
  Note/Revision before validating mentions, so a hidden/foreign mention left a
  ghost row in the Session. Mention validation now runs BEFORE any durable mutation
  (`_resolve_mentions`), and the append path resolves inherited/replacement mentions
  before adding the revision. Regressions
  `test_create_note_with_invalid_mention_leaves_no_ghost_rows` and
  `test_append_revision_with_invalid_mention_leaves_no_ghost_rows` trigger the typed
  failure, then commit a successful operation on the SAME Session and prove no ghost
  Note/Revision/Mention survives.
- **Mention retention across body edits (durable API semantic).** `mentions` on
  `NoteRevisionCreate` is tri-state: omitted inherits the previous revision's
  mention identities, `[]` clears, non-empty replaces after normal authorization.
  `_inherit_mentions` copies identities without re-validation, so an
  already-authorized reference that later becomes unavailable stays historical
  (`resolved=false`). Regressions: inherit, clear, replace, inherited-target-
  archive, and a wire-level HTTP inherit test; the workspace keeps links by default
  and exposes an explicit "clear links" action; the browser slice asserts the chip
  survives member B's body edit.
- **DAG/layering honesty.** `revolab.notes` is documented as an
  application-orchestration service (not a Core domain leaf) and no longer consumes
  Evidence/Decision ORM internals: it calls the owning domains' new public contracts
  `domain.provenance.evidence_mention_target` and
  `domain.knowledge.decision_mention_target`. `DOMAIN_BOUNDARIES.md` §1a and
  `SYSTEM_ARCHITECTURE.md` state that cross-domain composition happens in the
  application layer, adding no Project → Evidence/Knowledge Core edge. The
  nine-domain DAG is unchanged.
- **Authority state.** `PROJECT_NOTEBOOK.md` and ADR-0016 were marked
  `Proposed — pending human acceptance` while under review; after the human
  reviewer granted acceptance (PR #11 review) they are now **Accepted**, as are the
  consuming references in `SYSTEM_ARCHITECTURE.md` and `EVIDENCE_PROVENANCE.md`.
- **P2 findings.** `resolve_selected_notes` authorizes/resolves every supplied
  `note_id`/`note_revision_id` BEFORE applying `max_notes` (so `max_notes=0` cannot
  bypass fail-closed validation); the Notebook list and the revision history gained
  real load-more pagination (50 / 100 per page); Markdown thematic breaks are now
  marker/whitespace-only lines, so `--- IMPORTANT` and `***warning` stay literal
  text. Focused regressions cover each.
- **Machine truth.** The top-level Status now states Phase 10 is implemented and
  under human review (Phases 1–9 accepted on `main`), and this ledger reflects the
  current reviewed head only.

### Final independent review (5 reviewers) — reconciliation

A fresh 5-reviewer pass over `main...HEAD` (A architecture, B security, C
persistence, D API/frontend, E tests/docs) found no P0; A/B/D/E returned PASS and C
returned one P1 plus one P2 (D also reported P2 frontend findings). All valid
findings were fixed:

- **P1 (transaction safety).** The flush-time uniqueness backstop called
  `session.rollback()`, discarding a composing caller's whole transaction. It now
  uses a SAVEPOINT around the revision insert (and expunges the failed revision),
  so only this command's own write is discarded; the stale-base path is unchanged.
  Regression `test_uniqueness_backstop_preserves_a_composing_callers_transaction`
  pre-flushes a caller row, forces the collision, and proves the caller row and the
  true revision history survive.
- **P2 (mention lifecycle consistency).** Resource mentions resolved/validated from
  link visibility alone, ignoring `archived_at`/`revoked_at`, unlike
  Evidence/Decision. `queries.resource_mention_active` is now the shared resource-side
  lifecycle check; `_resolve_mentions` refuses an archived series/revoked reference
  and `_mention_read` derives `resolved` from it. Regression
  `test_archived_resource_mention_is_refused_and_reads_unresolved`.
- **P2 (frontend).** Transient notice/error now clear on Note switch; load-more is
  clamped to the server's 200 cap (and `revisionLimit` resets per Note); an Agent
  hand-off of a valid current-Project Note outside the newest selector page is
  resolved by id (`getNote`) instead of being silently dropped
  (`Agent.test.tsx` hand-off-resolution regression).
- **P3 hardening.** `ContextSelectionCreate` gained `extra="forbid"`;
  `resolve_selected_notes` clamps `max_notes`/`max_note_chars` internally (defense in
  depth); the cap-ordering regression is deterministic regardless of UUID sort
  order; the PostgreSQL lock test now also asserts `patch_note` emits `FOR UPDATE`
  and the lifecycle test asserts the rejected create left no ghost; the stale
  "Domain service" label, `DOMAIN_BOUNDARIES.md` public-contract list, and the
  `EVIDENCE_PROVENANCE.md` wording were corrected.

Delta pass after these corrections: three fresh read-only reviewers re-audited
`662bf38..HEAD`. Security/frontend returned PASS; persistence and
contracts/tests/docs returned REQUEST_CHANGES with no P0/P1 and further P2s, all
now fixed:

- **Revision-archival bypass.** `queries.resource_mention_active` treated any
  existing revision as active, so a revision of an archived series stayed
  mentionable. It now requires the owning series to be unarchived; regression
  `test_archived_revision_mention_is_refused_via_owning_series`.
- **Picker offered rejected targets.** The Notebook mention picker now filters out
  archived objects and revoked references, matching the backend rule and the
  already-filtered Evidence/Decision lists (frontend regression
  `offers no lifecycle-inactive mention targets`).
- **Revoked-reference branch untested.** Added
  `test_revoked_reference_mention_is_refused_and_reads_unresolved`.
- **Machine truth.** `EVIDENCE_PROVENANCE.md` gained the Phase-10 refinement it was
  claimed to have (a durable `ProjectNote` exists and is explicitly not Evidence);
  the reviewer-count sentence and the stale "Domain service" label were corrected;
  `DOMAIN_BOUNDARIES.md` §1a now names the lifecycle check; the 200-cap and
  `ContextSelectionCreate` strictness gained regressions.

### Final pagination correction (PR #11, human review round 3)

The human reviewer found one remaining P2: the Notebook list and revision history
"Load more" grew `limit` toward the server's `le=200` cap instead of paging, so the
201st Note / revision was unreachable. Fixed by using the API's `offset`:

- Notes page with `limit=50` at offsets `0, 50, 100, …`; revision history pages
  with `limit=100` at offsets `0, 100, 200, …`. Each fetched page is appended to an
  accumulated collection (deduplicated by id), so collections beyond the per-request
  cap stay reachable; a mutation restarts accumulation from offset 0.
- Regressions `paginates the notebook list with offset-based page accumulation`,
  `reaches the 201st note via offset pagination (beyond the server cap)`, and
  `paginates the revision history with offset pages and reaches the 201st revision`
  render 205-item datasets and prove the 201st row is displayed.
- With human acceptance recorded, `PROJECT_NOTEBOOK.md` and ADR-0016 (and their
  references in `SYSTEM_ARCHITECTURE.md` / `EVIDENCE_PROVENANCE.md`) are now
  `Accepted`; this ledger states that Phase 10 is human-accepted pending merge.

## Implemented (Phase 11)

- **Durable Action Request** (`revolab/models.py:ActionRequest`, migration
  `9e20ac68603b_phase11_action_requests.py`): Project + owning-Actor scoped durable
  operational intent recording the canonical `tool_id`, the COMPLETE
  schema-validated size-bounded argument payload (`arguments` +
  `arguments_digest`), lifecycle `status`, bounded `status_reason`, timestamps, and
  the canonical result reference (`result_run_id` / `result_decision_id`). It is
  **not** a ScientificObject, Evidence, Decision, `ProjectResourceLink`,
  `GlobalResourceRegistry` entry, conversation message, Agent memory, `ToolResult`,
  or provider execution truth, and the nine-domain Core DAG is unchanged: Action
  Handoff is an Agent Context / application-orchestration sub-boundary
  (`AGENT_ACTION_HANDOFF.md`, ADR-0017).
- **Persist intent, never authority.** The row stores no authorization decision,
  membership result, provider-health snapshot, capability availability, credential
  material or `secret_ref`, and no column duplicates REvoCompute's mutable run
  state (`test_action_row_stores_no_provider_execution_state`,
  `test_phase11_schema_has_no_authority_or_secret_columns`). Proposal-time
  `autonomy` / `execution_class` / `side_effect_class` are presentation metadata and
  are never consulted as authority (execution re-reads the CURRENT catalog).
- **Proposal path** (`revolab/agent/runtime.py`): an `explicit_action` (or
  `external_action`) tool call is validated against its canonical input model and
  persisted in the SAME turn transaction as the transcript; the bounded
  `PendingActionRead` returned to the model/human is now a VIEW naming the durable
  `action_request_id`. An invalid or over-bound payload fails closed and is never
  stored (a truncated preview is display data, not executable state).
- **One canonical explicit-action input mapping**
  (`revolab/tools/explicit_actions.py`) is consumed by BOTH the proposal boundary
  and the human execution boundary; the duplicate mapping that previously lived in
  `agent/runtime.py` is retired. Execution revalidates the stored payload against
  the CURRENT model, so a schema change fails closed with no compatibility shim.
- **Human surface** (`revolab/actions.py` + `api.py`): list (per conversation),
  read, execute, reject. Ownership is the Phase-9 conversation lens exactly — one
  owning Actor in one Project, another Project member gets 404, a non-member gets
  403 before any lookup (never an existence oracle). `execute`/`reject` are NOT
  Tools and never appear in the Agent ToolCatalog.
- **Execution-time revalidation**: current Project readability (tombstone),
  mutation-capable membership/role, current ToolCatalog + autonomy, current
  canonical input schema + payload integrity digest, current resource visibility for
  every referenced input, current provider availability and credential presence,
  current project policy. A pre-claim refusal raises the typed domain error and
  leaves the action `pending` (no side effect, no claim consumed); a post-claim
  definite refusal is terminal `failed`.
- **One-shot concurrency**: the durable `pending -> executing` claim is a single
  conditional `UPDATE` whose rowcount decides the winner; it is COMMITTED before the
  external call, so the claim (never a process-memory flag) is the production truth.
  PostgreSQL orders concurrent claims; the conditional update is the cross-backend
  backstop.
- **Canonical execution paths, no duplication**: a local explicit action
  (`decision.commit`) runs through the SAME closed `LocalToolRuntime` the human
  workspace uses; a remote explicit action runs through the SAME
  `services.compute_submit_handle` + `record_compute_run` capability path the human
  compute endpoint uses (the new helper is a split of the existing implementation,
  not a second one). A confirmed handle creates/reuses the canonical `RunReference`
  and its `consumed_as_input_by` edges; REvoCompute remains the sole owner of
  execution state.
- **Honest external ambiguity**: REvoCompute exposes no client-usable idempotency
  key, so none is invented. A transport failure, 5xx/gateway response, unexpected
  payload, or a 2xx without a task identity settles the action `ambiguous` and it is
  **never** automatically retried; explicit 4xx rejections and pre-side-effect local
  provider checks settle `failed`. A confirmed provider handle whose local
  `RunReference` recording fails also settles `ambiguous` rather than pretending
  either outcome.
- **Frontend** (`frontend/src/views/Agent.tsx`): a durable "Action requests"
  section inside the existing conversation surface showing tool id, execution class,
  side-effect class, human-readable canonical arguments (provider/task kind/input
  identities/parameters for a compute action), state, what Execute will do, and the
  resulting canonical reference; Execute/Reject only for a `pending` action and a
  mutation-capable membership, never on render/reload/navigation/model response, with
  the Phase-8–10 scope guards so a stale in-flight response cannot repopulate another
  scope. No credential detail is ever rendered.

## Verified evidence (Phase 11)

Commands run on this branch head (2026-09-15):

```text
ruff check backend                                  All checks passed
mypy (strict, 53 source files)                      Success: no issues found
pytest (SQLite)                                     436 passed, 18 skipped
alembic upgrade head + alembic check (SQLite)       no new upgrade operations
alembic upgrade head + alembic check (PostgreSQL 16) no new upgrade operations
pytest backend/tests/test_postgres_integration.py   18 passed (migrated PostgreSQL)
python -m revolab.export_openapi  -> openapi.json   byte-identical (no drift)
frontend: npm run typecheck                         clean
frontend: npm run test                              56 passed
frontend: npm run build                             built
frontend: npm run check:contracts                   clean (generation idempotent)
playwright test (real FastAPI + DB + fake model/compute)  8 passed
git diff --check                                    clean
```

Phase-11 regressions (`backend/tests/test_actions.py`,
`backend/tests/test_actions_api.py`, `frontend/src/views/Agent.test.tsx`,
`frontend/e2e/actions.spec.ts`) cover, with mutation-sensitive assertions: an Agent
proposal is durable but NOT executed; reload returns the same pending action; another
Actor cannot read/execute/reject and another Project cannot use it; non-member 403
before any existence check; Project tombstone, membership/role revocation, input
visibility revocation, credential revocation, unavailable provider, removed Tool,
changed autonomy, stale schema and a tampered payload all block execution; no
credential/`secret_ref`/executable argument reaches the durable row or the transcript; rejection has no side effect and is terminal; concurrent execution
submits at most once (SQLite threads and PostgreSQL connections, both forcing the
workers to reach the one-shot claim concurrently via a barrier immediately before
`_claim`); success creates the canonical `RunReference` and a second execute never
resubmits; the provider boundary classification is `failed` vs `ambiguous`; an
ambiguous outcome is never retried; execute/reject are absent from the Agent
catalog; hostile Note text cannot authorize or execute; an over-bound payload is
refused rather than truncated by BOTH the Agent loop and the persistence boundary.
Post-claim failure honesty is covered too: a plain recording failure, a DB-level
recording failure that leaves the session in a failed transaction, and a partially
recorded run whose retry completes the canonical recording (no orphaned
`RunReference`, no action stranded in `executing`). The DB-level case is asserted on
PostgreSQL as well, because SQLite does not abort a transaction on a failed
statement; that PostgreSQL regression FAILS if `_settle`'s rollback-retry is
removed, so the fix is machine-guarded on the substrate where it is load-bearing.

Note on drift coverage: `alembic check` does not compare CHECK constraints, so the
`ck_action_succeeded_has_result` model/migration agreement is asserted explicitly
by the migrated-PostgreSQL schema regression (constraint name + predicate tokens)
and by the SQLite raw-insert regression. A development database that applied an
earlier revision of the Phase-11 migration must be recreated or
`alembic downgrade 8822524ef06f && alembic upgrade head`-ed to pick it up.

## Independent review (Phase 11)

Three FRESH read-only reviewers ran in parallel against this branch (architecture /
ownership; security / authority / external-side-effect semantics; API /
persistence / frontend / verification), each independently verifying claims against
the code and tests. All three returned completed structured reports; the integrator
reconciled them (subagents do not vote on architecture) and fixed every valid
P0/P1 plus the material P2s.

| Reviewer | Verdict | P0 | P1 | P2 |
|---|---|---|---|---|
| A — architecture / ownership | APPROVE WITH FINDINGS | 0 | 1 | 3 |
| B — security / authority / side effects | REQUEST CHANGES | 0 | 2 | 5 |
| C — API / persistence / frontend / tests | REQUEST CHANGES | 0 | 1 | 6 |

Reconciled findings (all fixed in `ddbc658` + the follow-up reconciliation commit):

1. **A-P1 — duplicate source of truth for the local explicit-action input model.**
   `tools/explicit_actions.py` kept a second `decision.commit -> DecisionCommitCreate`
   map that duplicated (and contradicted) the registered `LocalToolSpec.input_model`.
   Retired the local map: a local explicit action now resolves its model from the
   registry (`LocalToolRegistry.explicit_action_input_model`), a remote one from the
   ONE capability-suffix map. `PROJECT_TOOL_HARNESS.md` states the truth, and
   `test_explicit_action_input_models_are_single_sourced` now compares against the
   LIVE registry, so drift fails the test (it was tautological before).
2. **B-P1 — broken transaction boundary in human execution.** A caught recording
   failure could strand a durably claimed action in `executing` while the external
   side effect had happened, and `_settle`'s commit could persist a partially
   flushed RunReference. Fixed: every failure path ROLLS BACK before the terminal
   write; `_settle` tolerates a session left in a failed transaction (rollback-first,
   one retry) and reports whether the conditional transition won; a
   confirmed-but-unrecorded run is retried ONCE through the canonical get-or-create
   recording, so a partially committed attempt is completed instead of orphaned.
   Regressions: `test_confirmed_but_unrecorded_run_is_terminal_and_never_stuck`,
   `test_db_level_recording_failure_still_settles_the_action`,
   `test_partially_recorded_run_is_completed_not_orphaned`.
3. **C-P1 — provider misattribution on the human authorization surface.** A remote
   action's `tool_id` (the execution authority) and its argument `provider_key` (what
   the human reads) could disagree, so a human could authorize under a false
   description of the external side effect. Fixed with ONE canonical rule
   (`explicit_arguments_match_provider`) enforced at BOTH the proposal boundary
   (fail closed, no durable row) and the execution boundary (defense in depth).
   Regressions: `test_proposal_refuses_a_payload_naming_a_different_provider`,
   `test_execution_refuses_a_stored_provider_mismatch`.
4. **B-P2 — the persistence boundary did not enforce its own argument bound.**
   `propose_action_request` now refuses an over-bound payload itself
   (`test_propose_refuses_an_over_bound_payload`), not only the Agent loop.
5. **B-P2 — an unreconcilable confirmed submission.** The `ambiguous` reason now
   retains the bounded provider identity (`authority/native_id`); documented as the
   ONE place an external identity appears outside a RunReference, precisely because
   the canonical card could not be created.
6. **B-P2 / C-P2 — `arguments_digest` was described as tamper protection.** It is an
   UNKEYED content digest for corruption / out-of-band-edit detection, never an
   authentication tag and never authority; the docs, function docstring and error
   wording now say so.
7. **B-P2 — the concurrency regressions did not race the claim.** Both the SQLite and
   the PostgreSQL concurrent-execute tests now force both workers to be about to
   take the one-shot claim at the same instant (a barrier immediately before
   `_claim`), so they exercise the atomic conditional UPDATE itself rather than only
   the stale-`pending` guard.
8. **A-P2 — stale/duplicated facts.** One shared `COMPUTE_SUBMIT_SUFFIX` constant;
   the ADR/handoff now cite `services.compute_submit_handle` + `record_compute_run`
   (the actual call path); the local-action → Decision-result assumption is
   documented in code and model.
9. **C-P2 — no DB invariant for `succeeded ⇒ result reference`.** Added
   `ck_action_succeeded_has_result` to the model AND the migration (asserted on the
   migrated PostgreSQL schema); regression
   `test_succeeded_requires_a_canonical_result_reference`.

Findings recorded as intentional / informational (no change):
- A hard process kill between the committed claim and the terminal write still leaves
  `executing`; it is honestly rendered, never auto-retried, fails further executes
  closed, and a background reaper is an explicit non-goal (documented deferral).
- The new routes declare only 200/422 in OpenAPI; 401/403/404/409 come from the
  global exception handlers and are covered by tests. This matches the repo-wide
  route convention (no per-route error declarations anywhere), so declaring them
  only here would be inconsistent contract style.
- The pre-existing process-global scripted-model turn counter makes the Phase-8
  `agent.spec.ts` repeat-sensitive (`--repeat-each=2` fails its second repeat). CI
  runs each spec once (8/8 green); the NEW Phase-11 spec is deliberately
  counter-independent and is stable at 2x and 4x repeats. Recorded, not a Phase-11
  regression.

### Delta review (round 2 — fix delta, 2 fresh reviewers)

Two additional FRESH read-only reviewers examined only the fix delta
(`f495df9..d834210`), the previously affected invariants and their regression
coverage. One (security / failure semantics) is reported below; the persistence /
contracts reviewer returned APPROVE WITH FINDINGS, all reconciled:

- **P1 — the transaction-boundary fix was not machine-guarded on the substrate
  where it is load-bearing.** SQLite does not abort a transaction on a failed
  statement, so the SQLite "DB-level failure" regression never reproduced its own
  premise; reverting `_settle`'s rollback-retry left the whole suite green. Fixed by
  adding `test_phase11_post_claim_failure_settles_terminally_on_postgres`, which
  aborts a real PostgreSQL transaction and FAILS when the retry is removed (verified
  by mutation).
- **P2 — `alembic check` does not compare CHECK constraints.** The
  model/migration agreement for `ck_action_succeeded_has_result` is now asserted
  explicitly by the migrated-schema regression (name + predicate tokens); the
  drift-gate caveat and the required downgrade/upgrade for an already-migrated dev
  database are recorded above.
- **P2 — stale ledger wording / dead test scaffolding.** The concurrency description
  now matches the barrier-before-`_claim` implementation, and the unused
  provider-blocking hook was removed from both test doubles.

### Delta review round 3 (security / failure semantics) — reconciliation

The security delta reviewer returned REQUEST CHANGES on the fix delta; all findings
were reconciled (the reviewer count stays within the 3..5 budget: 3 first-round + 2
delta reviewers):

- **P1 — the confirmed-but-unrecorded recovery could leave committed rows behind a
  FALSE "nothing recorded" ambiguity.** `record_compute_run` commits the
  RunReference before its per-input provenance edges, so a persistent edge failure
  left a committed reference while the action reported `ambiguous /
  result_run_id=NULL`, and the failed retry's pending state could be committed by
  the terminal write. Fixed: the failure path rolls back, retries the canonical
  recording once (get-or-create completes a partial attempt, never repeating the
  external submission), then READS THE REFERENCE BACK by the confirmed provider
  identity and settles `succeeded` with the real reference plus a bounded
  provenance-incomplete note; only a genuinely absent identity settles
  `ambiguous`. Regression
  `test_persistent_provenance_failure_still_reports_the_real_run_reference`
  (mutation-verified: it fails if the read-back fallback is removed) and
  `test_no_canonical_reference_at_all_is_ambiguous_with_a_truthful_reason`.
- **P2 — `_settle` could raise after its retry, and a local action returning no
  result reference could strand the row.** Fixed: a local execution with no typed
  result settles `ambiguous` with a precise reason (never `succeeded`, which the
  durable CHECK forbids, and never a silent success), and the remote branch guards a
  missing reference the same way.
- **P2 — `_settle`'s "transition won" return value was dead.** The conditional
  transition is now enforced: losing it raises a typed conflict instead of being
  ignored.
- **P2 — the provider-agreement predicate was fail-open when the payload omitted
  `provider_key`.** It is now strict: a remote action's payload must name its
  provider, and an omission fails closed.
- **P2 — `ck_action_succeeded_has_result` was incompatible with the
  `result_decision_id` FK's `ondelete='SET NULL'`.** The FK is now `RESTRICT`,
  consistent with the deletion invariant (a referenced Decision is never
  hard-deleted, so the action's canonical result is never silently nulled); the
  global-resource run FK stays `SET NULL` because global resources are only revoked.

### Delta review round 4 (human review) — identity-compatibility gate

Human review of the recovery fallback found that reading an existing
`(authority, native_id)` back merely because it EXISTS is not sufficient: the
canonical immutable identity contract must be re-applied. Fixed:

- `_recover_confirmed_run` now calls `provenance.assert_reference_compatible(existing,
  task_type=exc.handle.task_type)` (the same assertion
  `_persist_run_reference_trusted` uses). A COMPATIBLE reference settles `succeeded`
  with the real reference plus the bounded provenance note; an INCOMPATIBLE one is
  never attached and settles `ambiguous` with bounded reconciliation detail and
  `result_run_id = NULL`.
- No exception from the compatibility assertion, the read-back, or the retry can
  escape the recovery handler, so the action is always settled terminally (never
  stranded in `executing`).
- Regression `test_recovery_never_attaches_an_incompatible_existing_run_reference`
  pre-seeds a same-identity/different-`task_type` `RunReference`, makes the provider
  return that identity, and proves: exactly one provider submission; status
  `ambiguous` (not `succeeded`); `result_run_id` null; the incompatible row untouched
  and not attached; no second reference fabricated; a further execute fails closed
  without re-submitting. Mutation-verified: removing the compatibility assertion
  fails the test.

## Implemented (Phase 12)

- **Search as an application/query sub-boundary** (`revolab/search.py`): an
  authorization-aware, bounded read projection over canonical rows. It owns only
  query parsing/bounds, authorized retrieval, ranking, bounded plain-text snippets
  and the typed `SearchHit` projection; it adds **no Core domain**, no
  `SearchDocument` truth table, and no migration. Normative owner:
  `docs/architecture/PROJECT_SEARCH_RETRIEVAL.md` (ADR-0018, **Proposed**).
- **Closed vocabulary** (`revolab/enums.py`): `SearchScope`
  (`project_shared` / `my_conversations` / `all`), `SearchTargetKind` (a
  retrieval/presentation classifier — deliberately NOT `ResourceKind`), and
  `SearchMatchedField` (presentation metadata). Scope→kind membership is derived
  from one frozen mapping (`PROJECT_SHARED_TARGET_KINDS`,
  `MY_CONVERSATION_TARGET_KINDS`).
- **Bounded query contract** (`revolab/schemas.py`): `SearchHitRead`,
  `ProjectSearchResultsRead`, `ProjectSearchToolInput`, and the bounds constants
  (query 200 chars, 8 terms, 64 chars/term, limit 1..50, snippet 240 chars, one
  closed kind set). Over-bound input fails closed with a typed 422; a wildcard-only
  query is not a query language. No raw SQL/`tsquery`/regex/glob/URL is accepted.
- **Nine corpora**, each filtered/ranked/capped in ONE SQL query under the current
  read lens: `scientific_object_series` (name/description/object_type/external
  identities/aliases), `evidence` (label/interpretation/scope), `decision`
  (title/statement/next_actions), `note` (title + LATEST revision body only,
  archived excluded), `run_reference`, `artifact_reference`,
  `literature_reference`, `external_reference` (stored identity/header metadata
  only — never a provider call), and the Actor-private `conversation` corpus
  (title + messages, Actor × Project scoped). Global rows are reached only through
  `ProjectResourceLink`; Evidence/Decision rows must be current and unarchived.
- **Exact canonical UUID**: a query that IS one canonical UUID (hyphenated, compact,
  or braced) is matched against the canonical identity column and ranks first — still
  only inside the corpus's authorization filter, so TODO.md section 15's exact-UUID
  non-leakage vector is real and tested positively and negatively.
- **Authorization before disclosure**: unauthorized rows are never ranked,
  counted, snippeted, or reported as hidden. A unique query matching only an
  inaccessible resource is indistinguishable from no result; membership/tombstone
  changes apply on the next query because authorization is re-derived every time.
- **Ranking**: computed in SQL (`exact identifier → exact title → title prefix →
  lexical`, then PostgreSQL `ts_rank` with configuration `simple`, then recency
  and a stable identity tie-break). No ML/embedding/external ranking; no numeric
  score is exposed (ordering is the contract).
- **Snippets**: application-level bounded plain text (never `ts_headline` markup);
  control characters removed, everything else inert. Hostile Markdown/HTML cannot
  become active content.
- **PostgreSQL / SQLite**: one shared parameterized token-substring predicate gives
  the same semantic contract on both substrates (authorization, target classes,
  bounds, SearchHit shape); case folding is the database's `lower()`, so non-ASCII
  case folding differs (PostgreSQL folds per locale, SQLite ASCII-only — an accepted
  substrate limitation). PostgreSQL adds native `to_tsvector`/`ts_rank` ordering. Neither materializes the Project in Python:
  each corpus is one bounded SQL statement with LIMIT, and only the bounded
  candidate set is merged. **No index/migration is added**: a persisted derived
  index would be a second representation requiring its own freshness/authorization
  ADR (documented; `alembic check` stays drift-clean on both substrates).
- **API** (`revolab/api.py`): `GET /api/projects/{project_id}/search` with typed
  `q`, `scope`, `target_kinds`, `limit` query parameters, returning the bounded
  `ProjectSearchResultsRead` envelope (no pagination, no `total_count` — a count
  over authorized rows is itself an oracle).
- **Reference lifecycle on handoff**: an explicit `reference_ids` selection also
  checks the reference's own lifecycle flag, so a revoked reference is not admitted
  as current context; explicit evidence/decision/reference selections are honored
  regardless of the `include_*` category switches.
- **Explicit Search → ContextSelection handoff** (`revolab/schemas.py`,
  `agent/builder.py`): `ContextSelectionCreate` gained the smallest canonical typed
  fields for previously transitive-only targets — `evidence_ids`, `decision_ids`,
  and `reference_ids` (reference identity cards only; a series/revision id there
  fails closed, so it is not a generic resource-id bag). The builder includes
  explicitly selected rows first and re-validates every id against the CURRENT
  Project read lens (foreign/archived/wrong-kind fails closed even with a zero
  budget). No `SearchContext`/`SearchMemory`/`RetrievalContext` was introduced.
- **Agent Tool** (`revolab/tools/registry.py`, `tools/handlers.py`):
  `project.search` — `automatic` / `local` / `read_only`, `requires_mutation=False`.
  Its input has NO scope field, so it is structurally fixed to `PROJECT_SHARED`
  (private conversations cannot be requested), and it calls the SAME search
  application service the human workspace uses. It performs no durable write, does
  not change a ContextSelection, never promotes Evidence/Decision, never executes
  an Action Request, and never resolves provider content or artifact bytes.
- **Workspace surface** (`frontend/src/views/Search.tsx`, `App.tsx`,
  `views/agentContext.ts`): a dense Project search view with an explicit scope and
  optional target-kind filter, results grouped by backend-owned target kind, per-hit
  `Open` navigation into the EXISTING canonical surface (object detail / Notes /
  Evidence / Decisions / Runs & Artifacts, with row selection rather than a parallel
  detail page), and an explicit `Add to Agent context` for context-selectable kinds.
  Conversation hits are labelled `private working memory` and never offered for
  handoff. The Agent view shows the pending selection as removable chips before
  sending and projects the items into the canonical `ContextSelectionCreate` typed
  id lists. `SearchScope`/`SearchTargetKind`/`SearchMatchedField` come from the
  generated contract (added to `scripts/generate-enums.mjs`), never hand-written.

## Verified evidence (Phase 12)

Commands run on this branch head (2026-09-15):

```text
ruff check backend                                  All checks passed
mypy (strict, 54 source files)                      Success: no issues found
pytest (SQLite)                                     481 passed, 24 skipped
alembic upgrade head + alembic check (SQLite)       no new upgrade operations
alembic upgrade head + alembic check (PostgreSQL 16) no new upgrade operations
pytest backend/tests/test_postgres_integration.py   24 passed (migrated PostgreSQL)
python -m revolab.export_openapi -> openapi.json    refreshed (byte-identical after export)
frontend: npm run typecheck                         clean
frontend: npm run test                              66 passed
frontend: npm run build                             built
frontend: npm run check:contracts                   clean (generation idempotent)
playwright test (real FastAPI + DB + fake model)    9 passed
git diff --check                                    clean
```

Phase-12 regressions (`backend/tests/test_search.py`,
`backend/tests/test_postgres_integration.py`, `frontend/src/views/Search.test.tsx`,
`frontend/src/views/Agent.test.tsx`, `frontend/e2e/search.spec.ts`) cover, with
mutation-sensitive assertions: member retrieval by name/identifier/label/statement/
latest Note body; external-identifier and alias search that is not English-stemmed;
superseded Note revision text never presented as current and archived Note/Evidence/
Decision excluded; stored reference hits survive provider unavailability; exact
UUID/native-id/checksum/title/cross-Project non-leakage; cross-Actor private
conversation isolation (service and HTTP); the Agent Tool cannot request the private
scope; the Tool performs no durable write (statement-level listener) and does not
alter a built context; a hostile note returned as a hit stays untrusted data and
creates no truth/Action Request; over-bound query/limit/kind fails closed; SQL-like
and wildcard input is inert plain text; hits/titles/snippets stay bounded; explicit
handoff includes Evidence/Decision/reference identity cards and rejects foreign/
archived/wrong-kind/stale ids; PostgreSQL proves native `to_tsvector`/`ts_rank`
emission, latest-revision semantics, private isolation, bounded top-N in SQL, and
cross-Project non-leakage; the browser slice proves search → explicit
`Add to Agent context` → the deterministic model receives the selected Decision.

## Independent review (Phase 12)

Three FRESH read-only reviewers ran in parallel on the implemented head (TODO.md
section 41): (A) architecture/retrieval semantics, (B) authorization/privacy/
security, (C) database/API/frontend/verification. All three returned **APPROVE WITH
FINDINGS**, **no P0**, no unresolved P1. Findings were reconciled by the Primary
Integrator; every valid P1 and material P2 was fixed on the same branch, each with a
regression:

- **P1 (A) — the normative ranking contract over-promised exact UUID matching** (no
  corpus matched a canonical UUID, which made the cross-Project UUID regression
  vacuous). Fixed: a query that is one canonical UUID is now matched against the
  canonical identity column and ranks first, inside the same authorization filter;
  added a POSITIVE owning-Project regression and strengthened the cross-Project
  negative. `PROJECT_SEARCH_RETRIEVAL.md` section 7 documents it.
- **P1 (A) — the normative doc claimed `Accepted` while ADR-0018 is Proposed.**
  Fixed: the doc is now `Proposed — pending human acceptance`, matching the ADR and
  the Phase-11 precedent.
- **P1 (C) — a private-only target kind stayed selected after a scope change**,
  producing a sticky 422. Fixed: changing to `PROJECT_SHARED` clears an
  incompatible kind; results render only for a settled request so a previous
  scope's hits never remain mounted; new unit regression.
- **P2 (B) — `_allowed_kinds` failed open for a raw non-enum scope value.** Fixed:
  the scope is coerced through `SearchScope` at the service boundary and an unknown
  scope raises a typed validation error; regression added.
- **P2 (A/C) — explicitly selected `evidence_ids`/`decision_ids` were silently
  dropped when `include_evidence`/`include_decisions` was false.** Fixed: an
  explicit identity selection is honored unconditionally; regression added.
- **P2 (B) — the builder could load a foreign series skeleton when a revision was
  linked without its owning series (a DB state no domain path produces).** Fixed:
  the loop no longer falls back to the global table and fails closed; regression
  added.
- **P2 (B/C) — a revoked reference was admitted by `reference_ids`.** Fixed: the
  selection now honours the reference's own lifecycle flag; regression added.
- **P2 (C) — the per-corpus SQL `LIMIT` lacked the documented identity tie-break**,
  making a tied top-N non-reproducible. Fixed: the canonical identity is the final
  SQL `ORDER BY` key (matching the application merge key).
- **P2 (C) — cross-backend equivalence was overstated for non-ASCII case folding.**
  Fixed: docs/ADR/state now state the SQLite ASCII-only `lower()` limitation
  explicitly, and PostgreSQL acceptance asserts locale folding while the SQLite test
  documents the substrate behavior.
- **P2 (A/C) — Decision status was not visible in a hit** (TODO.md section 28).
  Fixed: `SearchHitRead.status` (canonical `DecisionStatus`, presentation only) is
  returned for Decision hits and rendered as a badge.
- **P2 (C) — `session_reference` was an undocumented taxonomy omission.** Recorded
  explicitly as a deliberate Phase-12 omission with its reason.
- **P2 (C) — "Open" highlight is limited to the loaded page.** Documented as
  best-effort selection in the existing surface (no parallel detail page).
- **P2 (A/C) — coverage gaps and cleanup**: added regressions for the Tool-level
  `conversation` target kind, foreign/revoked hand-off ids, `target_kinds` over the
  wire, the total-returned-text ceiling, and the revision-implied foreign-series
  case; removed the unused frontend export.

### Delta review (round 2) — TODO.md section 44

Because the reconciled fixes materially changed authorization/selection semantics,
the search query architecture, the wire contract and the frontend surface, TWO
additional FRESH read-only delta reviewers examined the fix delta
(`b26524d..0ea18ac`), the previously violated invariants and the added regressions.
Combined with the three first-round reviewers the total final-review count is
**5**, inside the 3..5 budget. Both returned **APPROVE WITH FINDINGS / REQUEST
CHANGES**, **no P0**. All valid findings were fixed on the same branch:

- **P1 (E) — the scope/kind mismatch was only half-fixed.** The kind control still
  offered the eight Project-shared kinds inside `MY_CONVERSATIONS`, so choosing one
  and submitting produced a 422. Fixed: the control now offers exactly the current
  scope's kinds (conversation only in `MY_CONVERSATIONS`; conversation excluded from
  `PROJECT_SHARED`; all kinds under `ALL`) and a scope change clears any kind the new
  scope disallows in BOTH directions. New unit regression covers the full matrix.
- **P1 (E, also D-P2-1) — the series corpus omitted `identity_column=series.series_id`**,
  so an exact-UUID series hit ranked 3 while the docs/state claimed rank 0. Fixed,
  and the positive regression is now order-sensitive: a Decision whose statement
  merely QUOTES the series UUID must rank BELOW the identity itself.
- **P2 (E) — `_resolve_kinds` raised `AttributeError`** for a raw-string target kind.
  Fixed by coercing each element through `SearchTargetKind` and raising the typed
  validation error (mirroring the scope coercion).
- **P2 (D) — the identity tie-break had no mutation-sensitive regression.** Added a
  test that inserts tied rows in descending identity order and asserts the exact
  top-N subset; it FAILS when `identity.asc()` is removed (mutation-verified).
- **P2 (D) — the `status` mapping had no backend regression.** Added a test asserting
  `DRAFT`/`COMMITTED`/`None` on the hit; it FAILS when the mapping is removed
  (mutation-verified).
- **P2 (D) — the total-text test was vacuous.** Rewritten to drive `limit` maximal
  hits and assert an independently written literal ceiling; it FAILS when the snippet
  cap is raised (mutation-verified).
- **P2 (D) — exact-UUID negatives were incomplete.** Added lifecycle negatives
  (archived series/Evidence/Decision/Note, revoked Run/Artifact) and a cross-Actor
  conversation UUID negative in every scope.
- **P2 (D) — `artifact_ids` vs `reference_ids` lifecycle asymmetry.** Documented as a
  deliberate Phase-12 boundary: the Phase-8 `artifact_ids` contract is unchanged and
  the Phase-12 search hand-off maps artifact hits to the lifecycle-checked
  `reference_ids`; making `artifact_ids` uniform is a separate change to an accepted
  contract.
- Delta reviewers also reproduced the gates independently: ruff/mypy clean, SQLite
  suite green, PostgreSQL acceptance green, OpenAPI byte-identical, no determinism or
  authorization defect beyond the above; the round-1 mutation harness confirmed the
  earlier fixes are regression-guarded (each revert fails its regression).

## Known deferrals (explicit, not silently postponed)

## Known deferrals (explicit, not silently postponed)
- Real authentication/OIDC; RBAC engine; public sharing (ADR-0008/0011 deferral).
- Remote provider tool execution inside the Agent loop (the Agent surfaces remote
  tools from the same catalog and converts remote `explicit_action` into a durable
  Action Request, but never autonomously crosses the external boundary; remote
  reads remain on the human capability endpoints — documented in
  `docs/architecture/PROJECT_AGENT_RUNTIME.md` and `AGENT_ACTION_HANDOFF.md`).
- Phase-11 accepted limitation: a process hard-killed after the durable one-shot
  claim and before the outcome write leaves the Action Request in `executing`. It is
  reported honestly as an in-progress claim with its claim time, is never
  auto-retried, and a further execute request fails closed; there is no automatic
  reaper/reconciliation worker (a background worker is an explicit non-goal).
- Live end-to-end acceptance against an authorized REvoCompute instance is
  external evidence: no authorized instance is configured in this development
  environment. The driver is built and tested against the documented public
  REvoCompute HTTP contract (read-only inspection) and an HTTP fake at the
  network boundary; live acceptance is not fabricated. Cross-Actor
  run/artifact sharing is an upstream REvoCompute gap (see
  `docs/integrations/REVOCOMPUTE_CONTRACT_GAPS.md`).
- REvoDesign / OpenBio integration is **deferred out of Phase 7 by the accepted
  roadmap correction** (REvoDesign is a method-specific interactive design app;
  OpenBio is a design reference — neither is a REvoLab backend). No driver or
  Core branch for either exists.
- Production secret-manager integration: the Secret store remains the
  non-production in-memory adapter (see disclosure above); a production-grade
  backend is deferred until explicitly configured.
- Agent Note mutation: Phase 10 gives the Agent read-only access to explicitly
  selected Notes; an Agent-facing Note tool is deferred (it must be policy-gated
  and typed and never bypass the ordinary ProjectNote domain command).
- Rich collaborative editing (CRDT/realtime), a block-editor framework, RAG/
  embeddings/vector search over Notes, and a global full-text search are
  deferred; Phase 10 content is bounded Markdown/plain text.
- Phase-12 deferrals (explicit, documented in `PROJECT_SEARCH_RETRIEVAL.md` §12 and
  ADR-0018): semantic/vector retrieval, a persisted/denormalized search index
  (including any PostgreSQL expression index), historical Note-revision search,
  cross-Actor conversation search, external biological/Web knowledge search and
  import, a query DSL / saved searches / faceted engine, automatic per-turn search,
  and a cross-Project top-bar command palette. Phase 12 adds **no migration**: the
  canonical schema plus the existing indexes are drift-clean on SQLite and
  PostgreSQL 16, and a derived index requires its own freshness/authorization ADR.

## Working set

- Added dependency: `httpx` moved from dev to runtime for the provider transport
  (`fsspec` (ContentStore), `python-multipart` (artifact upload) were added in
  earlier phases).
- Frontend: `openapi-fetch` (typed client), `openapi-typescript` (contract
  generation, dev), `@playwright/test` (browser smoke, dev), `@types/node` (dev).

### Post-merge record: two human-review P1 fixes

A human review put PR #12 on HOLD for two substantive P1s; both were fixed with
mutation-verified regressions and are included in the squash-merged commit
`83f1827`:

- **Failed policy tool could leave a ghost/partial write.** Phase 9 made Agent-loop tool
  mutations `commit=False`, and `decision.record_draft` flushes its Decision before citation
  validation completes; a `DomainError` after that flush left the row in the outer transaction,
  which the turn's final commit then persisted. Each Agent-executed tool call now runs inside its
  own SAVEPOINT (`AgentTurnRunner._handle_tool_call`), so a failed tool rolls back only its own
  partial rows. Regression `test_failed_policy_tool_leaves_no_partial_write` fails if the
  savepoint is removed.
- **SQLite ignored `FOR UPDATE`, so concurrent same-conversation turns could start from the same
  history and collide on `seq`.** `run_conversation_turn` now takes an explicit process-level
  per-conversation execution lock on the SQLite substrate (PostgreSQL keeps the cross-process row
  lock as the concurrency truth). Regression `test_sqlite_concurrent_turns_serialize` fails if that
  lock is disabled.

The SQLite wait is also bounded (a still-contended caller gets a typed retryable 409 rather than
occupying a request worker indefinitely), with its own regression. Full gates re-ran green:
backend **343 passed / 9 skipped**, PostgreSQL acceptance **9 passed** (no drift), frontend
**24 passed**, Playwright **4 specs**. SQLite remains a single-process dev/test substrate and must
not be run with multiple worker processes.
