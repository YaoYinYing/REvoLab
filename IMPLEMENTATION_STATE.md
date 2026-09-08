# Implementation State

Last verified: 2026-09-08

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
  never silently duplicated. `ExternalReference` remains an append-per-cache
  metadata record keyed to the (already unique) `ExternalIdentity` — it is not the
  durable identity card.
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

- `pytest` (69 tests) passes: authority/roles/stewardship/tombstone/MutationGrant
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
- Frontend gates still pass: `npm run typecheck`, `npm run test`, `npm run build`
  (the fixture frontend is untouched and remains a Phase-2 replacement target).

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

## Known deferrals (explicit, not silently postponed)

- Real authentication/OIDC; RBAC engine; public sharing (ADR-0008/0011 deferral).
- Provider/Driver/Capability/credential material (Phase 3); generated
  OpenAPI→TypeScript client + real frontend integration (Phase 2). The fixture
  frontend still builds green.

## Working set

- Added dependencies: `fsspec` (ContentStore), `python-multipart` (artifact upload).
