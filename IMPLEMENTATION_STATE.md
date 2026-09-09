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
  `capability_availability(actor, project)` = `driver health READY` AND all
  required credential kinds present for the Actor AND Phase-1 project membership
  permits (`owner`/`member`) — a query, never stored. Credential presence is a
  derived query over the binding set. `build_credential_lease` is the last-mile
  materialization layer.
- **Real Project-scoped Provider Catalog**:
  `GET /api/projects/{project_id}/providers` returns non-secret, Actor-contextual
  entries (identity, display metadata, required credential kinds, realized
  capability kinds, runtime health, the caller's own per-kind presence, derived
  availability). With zero configured providers the catalog is the honest empty
  set — no production fixture provider state.
- **Actor-scoped credential management** (`/api/credentials`):
  create (`POST`), explicit replace/rotation (`PUT`), explicit revocation
  (`DELETE`), presence-only list (`GET`); routed through `X-Actor-Id`, never a
  spoofable path actor_id. Responses never echo submitted secrets; a
  `RequestValidationError` handler strips the offending input so a failed
  credential request cannot reflect secret material.
- **Frontend**: the static Phase-2 provider surface is replaced by the real
  Project-scoped Provider Catalog rendered from the generated contract
  (`useProviders` → `ProvidersView`), showing the derived availability and the
  caller's own credential presence; no credential-provisioning UI, no
  hand-written enum value lists, no provider vocabulary in generic UI.

## Verified evidence (Phase 3)

- Backend: `pytest` passes with **118 passed, 2 skipped** (the two skips are the
  opt-in PostgreSQL acceptance file, run explicitly in CI with a migrated PG).
  New regressions cover credential-binding CRUD/uniqueness/rotation/revocation,
  secret-boundary sentinel absence (DB rows, reprs, logs, error envelopes),
  ephemeral/unpicklable lease, plural-kind lease materialization, the full
  availability matrix (incl. two Actors observing different availability in one
  Project and revocation flipping the next query with no stored repair), the
  two-state registry + capability projection + lazy health probe, catalog
  Actor-scoping, cross-Actor credential isolation, and the 422 no-echo gate.
  `ruff check backend` and strict `mypy` pass.
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

## Known deferrals (explicit, not silently postponed)

- Real authentication/OIDC; RBAC engine; public sharing (ADR-0008/0011 deferral).
- Real REvoCompute / REvoDesign / OpenBio drivers and the per-kind Capability
  method protocols (Phase 4). Phase 3 defines only the closed `CapabilityKind`
  vocabulary and the base `Capability(provider_key, kind)` shape.
- Production secret-manager integration: the Secret store remains the
  non-production in-memory adapter (see disclosure above); a production-grade
  backend is deferred until explicitly configured.

## Working set

- Added dependencies: `fsspec` (ContentStore), `python-multipart` (artifact upload).
- Frontend: `openapi-fetch` (typed client), `openapi-typescript` (contract
  generation, dev), `@playwright/test` (browser smoke, dev), `@types/node` (dev).
