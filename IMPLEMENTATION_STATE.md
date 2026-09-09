# Implementation State

Last verified: 2026-09-09

This file records actual, machine-verified repository state — not future plans.

## Status

The accepted architecture (merged into `main`, commit `a58e8f4`) is implemented as
Phase 1 ("Scientific context core + minimal authority substrate"). The earlier
backend/frontend prototype has been replaced; obsolete prototype paths were
removed, not shimmed.

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
  (`services.project_policy_permits`: read-only `SEARCH`/`ARTIFACT_RESOLUTION`
  accept any readable membership; action capabilities require `owner`/`member`)
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

## Verified evidence (Phase 4)

- Backend: `ruff check backend` and strict `mypy` pass (31 source files).
  `pytest` passes with **158 passed, 3 skipped** (the three skips are the opt-in
  PostgreSQL acceptance file). New tests cover: the REvoCompute driver against
  an HTTP fake at the network boundary (discovery/schema translation, submit
  redirect + multipart + artifact-reference input, failed-on-404 run state,
  artifact identity encoding, resolve, auth/not-found/invalid-param/
  unavailable/network/malformed error mapping, and the sentinel
  only-in-X-API-Key assertion), Core/domain compute with a provider-neutral fake
  (submit → RunReference + consumed input, artifact discovery + produced edge,
  resolve bytes, provider-disappearance leaves references intact, and
  invalid-input-kind / missing-credential / not-authorized / input-not-visible
  all fail before the external side effect), the project-scoped compute HTTP
  surface, and the bootstrap/lifecycle + explicit authority-resolution guards
  (production refuses the fake provider; the real driver requires a base URL).
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

## Known deferrals (explicit, not silently postponed)

- Real authentication/OIDC; RBAC engine; public sharing (ADR-0008/0011 deferral).
- Live end-to-end acceptance against an authorized REvoCompute instance is
  external evidence: no authorized instance is configured in this development
  environment. The driver is built and tested against the documented public
  REvoCompute HTTP contract (read-only inspection) and an HTTP fake at the
  network boundary; live acceptance is not fabricated. Cross-Actor
  run/artifact sharing is an upstream REvoCompute gap (see
  `docs/integrations/REVOCOMPUTE_CONTRACT_GAPS.md`).
- REvoDesign / OpenBio drivers remain Phase-5+ (not expanded here).
- Production secret-manager integration: the Secret store remains the
  non-production in-memory adapter (see disclosure above); a production-grade
  backend is deferred until explicitly configured.

## Working set

- Added dependency: `httpx` moved from dev to runtime for the provider transport
  (`fsspec` (ContentStore), `python-multipart` (artifact upload) were added in
  earlier phases).
- Frontend: `openapi-fetch` (typed client), `openapi-typescript` (contract
  generation, dev), `@playwright/test` (browser smoke, dev), `@types/node` (dev).
