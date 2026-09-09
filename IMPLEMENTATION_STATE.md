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

## Known deferrals (explicit, not silently postponed)

- Real authentication/OIDC; RBAC engine; public sharing (ADR-0008/0011 deferral).
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

## Working set

- Added dependency: `httpx` moved from dev to runtime for the provider transport
  (`fsspec` (ContentStore), `python-multipart` (artifact upload) were added in
  earlier phases).
- Frontend: `openapi-fetch` (typed client), `openapi-typescript` (contract
  generation, dev), `@playwright/test` (browser smoke, dev), `@types/node` (dev).
