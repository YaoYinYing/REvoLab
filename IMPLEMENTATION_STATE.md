# Implementation State

Last verified: 2026-09-08

## Exists

- Standalone repository metadata for a Python 3.12+ package with FastAPI, Pydantic Settings, SQLAlchemy, Alembic configuration, PostgreSQL driver, Uvicorn, and development tooling.
- SQLAlchemy mappings for Project, hierarchical ScientificObject, Relation, Evidence, Decision, and DecisionEvidence.
- Pydantic request/response models with object-tree parent references, provider/external-ID pairing validation, and relation self-reference validation.
- FastAPI health endpoint and project/object/relation/evidence/decision creation/list/graph endpoints.
- Application-scoped driver protocol, lifecycle states, collision handling, entry-point discovery, and explicit stop behavior.
- Focused backend API and driver lifecycle tests for the project graph, hierarchy boundaries, negative relation/reference cases, and startup rollback: 10 tests pass.
- Backend Ruff, mypy, Alembic drift check, SQLite migration apply, and Python wheel build pass in the local `.venv`.
- Alembic `upgrade head` and `check` verified against PostgreSQL 16 (via docker compose) with zero drift; the full project/object/relation/evidence/decision vertical slice runs end-to-end on PostgreSQL, and duplicate decision–evidence pairs are rejected by the composite primary key. This uncovered and fixed a schema drift: the initial migration/graph mismatch on PostgreSQL (original `DecisionEvidence` declared a redundant separate `UniqueConstraint` over its composite primary key columns, which autogenerate saw as an added constraint on PostgreSQL but not SQLite); the redundant constraint was removed from both the model and the root migration so `alembic check` reports no drift on both SQLite and PostgreSQL.
- React/Vite frontend prototype with a hierarchical object tree, scientific workspace, relation/evidence trail, decision inspector, responsive layout, and one component test; frontend typecheck, test, and production build pass.
- PostgreSQL Docker Compose service, Alembic environment configuration, GitHub Actions CI configuration, architecture documentation, ADR directory, agent conventions, and Apache-2.0 licensing files.
- Harness operating model control plane: `docs/agents/HARNESS_OPERATING_MODEL.md` defines the four loops (agent/goal/subagent-workflow/Ralph), the Primary Integrator role, the skill taxonomy, the safety/permission plane (default `workspace-write` + approval), and the 7-phase ODDRIVC workflow. `CLAUDE.md` carries only the short invariants derived from it.
- Project skills relocated from the top-level `skills/` tree into `.agents/skills/` (the canonical DSH skill root) and verified to load: `project-architecture`, `scientific-object-model`, `provenance-lineage`, `decision-record`, `project-context`, `artifact-inspection`, `driver-development`, `project-plugin-development`, plus the new constitutional `engineering-workflow` skill. The `engineering-workflow` skill (v0.2.0) carries both the ODDRIVC procedure and the long-running refactor protocol (three sources of truth, persistent checklist, migration-first deletion, vertical-slice migration, executable acceptance, DESIGN/EXECUTION/MACHINE definition of done); a draft root-level `LONG_TASK_HANDLING.md` was composed into that skill and removed to avoid a second source of truth.
- GitHub Actions CI is green on the architecture PR (head `f59fae2`, the Ralph round-1 final consistency commit; CI runs #33–#34 all green): backend Ruff/mypy/pytest + Alembic `upgrade head`/`check` against PostgreSQL 16; frontend typecheck/test/build all pass.

## Not yet verified

- Generated OpenAPI TypeScript client and frontend/backend live integration; the frontend currently uses a fixture graph.
- Browser smoke testing.
- Authentication, project membership, artifact storage, provider drivers, and production deployment.

This file records actual repository state, not future architecture plans.

## 2026-09-07 — Architecture design proposal (pending human review)

Delivered a reconciled top-level architecture **proposal** (see `docs/architecture/`
and ADRs 0008–0014). **Status: PROPOSED, not approved** — per the Harness authority
model only the human (via PR review/merge) can accept or reject the top-level
architecture. The current backend/frontend remain the prototype the proposal
overturns.

Produced:
- 10 documents in `docs/architecture/`: SYSTEM_ARCHITECTURE, DOMAIN_BOUNDARIES,
  SCIENTIFIC_OBJECT_MODEL, SCIENTIFIC_GRAPH, EVIDENCE_PROVENANCE,
  PROVIDER_CAPABILITIES, AGENT_CONTEXT, COLLABORATION_IDENTITY,
  WORKSPACE_INFORMATION_ARCHITECTURE, IMPLEMENTATION_ROADMAP.
- 7 new ADRs (0008–0014): project-membership ownership; typed object model;
  provenance graph; decision promotion; provider/capability; agent-as-consumer +
  authority; generated contract.
- `CLAUDE.md` gained the 10 load-bearing architecture invariants (§ REvoLab
  architecture invariants).
- Eight parallel read-only subagent analyses (A–H) fed the reconciliation; an
  independent reviewer then audited the final documents for complexity failure modes
  and cross-document consistency. The review found 16 failure modes (15 RESOLVED,
  1 PARTIAL at the time) and 6 internal-consistency contradictions; the integrator
  resolved all six (decision promotion verb → `commit`; canonical `RelationType`
  closed enum incl. `evaluates`/`selects` — `generated_by` is now a derived traversal,
  not an enum member; generic-Relation polymorphic
  target; added `hypothesis` evidence role; OpenBio cache defined as identity +
  validated metadata with no snapshot copy; SessionReference/ExternalReference added
  to the canonical node set and import unified on the `imported_as` edge with no
  separate ImportRecord node) and made DoD-18 explicit in IMPLEMENTATION_ROADMAP.
- The superseded bootstrap docs (`overview.md`, `domain-model.md`, `drivers.md`,
  `evidence-and-lineage.md`, `agent-and-skills.md`) were reduced to thin pointers to
  the proposed documents to avoid dual sources of truth; `reference-study.md` remains
  the external-reference analysis.

### 2026-09-07 — Human review REQUEST CHANGES (PR #1), being addressed

A human reviewer returned **REQUEST CHANGES** on the proposal (not merge). The review
confirmed ~80% of the top-level direction but required converging the remaining
"two-truths" before Phase 1 writes a migration. Its findings (architecture
authority/status language; global-resource vs project-scope persistence mapping;
object conceptual-identity vs revision-identity; a single canonical edge endpoint
matrix incl. the `produced`/`imported_as` direction contradiction; Evidence as
source+target with one polarity truth; freezing only the logical graph contract and
deferring physical Relation persistence to a Phase-1 spike; Relation uniqueness vs
append-only correction; authority/namespace vs resolver/provider split;
Actor-contextual provider availability; narrowing promotion to the Decision
draft→commit gate; unifying lifecycle/API command model; checksum-vs-origin
semantics; restoring short Engineering principles to CLAUDE.md; making Ralph
conditional) are being folded into the documents on this branch. Status remains
PROPOSED until the human accepts.

### 2026-09-07 — Human review round 2 (PR #1), convergence addressed; PROPOSED pending human review

### 2026-09-08 — Human review round 3 (PR #1), final convergence: four P1 constitution blockers closed; PROPOSED pending human review

The third human review rated the PR 9.3/10 and said the remaining work is the final
convergence — close four constitution blockers, clear stale text, delete the spike
brief, refresh CI evidence. All four blockers are now closed on this branch: (1)
organization state is fully project-local — the ScientificObject universal spine no
longer carries `organization_anchor`; placement is a folder/container annotation on
`ProjectResourceLink`, owned by the Project domain; (2) `ProjectResourceLink`
referential identity is realizable — a thin `GlobalResourceRegistry(resource_id PK,
resource_kind)` spine provides the single-column FK target (no polymorphic FK);
(3) graph edge ownership/lifecycle is fixed — `GlobalProvenanceEdge` (#1–7, owned by
Evidence/Provenance, never Project-archived) vs `ProjectKnowledgeEdge` (#8–10, owned by
Knowledge/Decision, archived with Decision/Evidence); (4) the canonical edge matrix
freezes Series vs Revision endpoints — conceptual semantic edges address Series,
content/provenance edges address Revision, Decision targets are explicitly typed.
Also cleared: stale Provider-owned Tool/Credential text in DOMAIN_BOUNDARIES; the
container guardrail now requires `docker compose config` inspection (policy inspects the
effective config, not the filename); the package-install boundary is now
"trusted-manifest-baseline unchanged vs changed" (not "existing dep vs new dep");
`SPIKE_PR1_BRIEF.md` deleted. CI evidence refreshed to head `18d1bc9` (runs #19–#20
green). A fresh independent architecture review found the four P1 blockers CLOSED with
no new P1. Status remains **PROPOSED — pending human review**.

### 2026-09-08 — Human review round 4 (PR #1), internal contradictions closed; PROPOSED pending human review

The fourth review rated the PR ~9.5/10 and found no new subsystems needed; it flagged
seven internal contradictions the round-3 fixes had introduced, and said they must close
before approval. All seven are now closed: (1) `ProjectResourceLink` no longer stores
`resource_kind` (the kind is joined from the registry) and every concrete global table
PK is also an FK to `GlobalResourceRegistry.resource_id`; (2) the one-directional
revision⇒series visibility closure is frozen (revision visible ⇒ owning series visible;
series visible ⇏ revisions visible; no sibling auto-exposure); (3) a write-time
project-context visibility invariant forbids ghost knowledge in
Evidence/Decision/ProjectKnowledgeEdge; (4) provenance single truth — RunReference is an
identity card and input/output/originating-run are derived aggregate fields over the
edges, not persisted columns; (5) the compute input contract is closed
(`consumed_as_input_by` accepts `ScientificObjectRevision | ArtifactReference`;
`ComputeCapability.submit` takes `inputs: [InputBinding]`); (6) the roadmap is reordered
so an Identity foundation precedes Provider integration (7 phases); (7) `ExternalIdentity`
is the single external-identity registry and `ExternalReference` holds an
`external_identity_id` FK plus cache metadata. Also: `version` removed from the series
spine, and the approved dependency baseline is defined as a digest (future policy note).
A fresh independent architecture review found all seven CLOSED with **no new P0/P1**.
Status remains **PROPOSED — pending human review**.

### 2026-09-08 — Human review round 5 (PR #1), resource authority / provenance single-truth / harness enforcement; PROPOSED pending human review

The fifth review (9.5/10, REQUEST CHANGES) found the design had matured enough to expose
second-layer governance issues, and asked for a narrow convergence: resource authority,
provenance single-truth, and harness enforcement boundaries. Landed on this branch: (1)
**write authority** — `ResourceStewardship` (Identity-owned) separates mutation from
read visibility; `ProjectResourceLink` is read-only context, and global provenance edges
are created **only** by typed authoritative domain operations (no generic global relation
writer); (2) **`current_revision_id` removed** from the series — "current" is derived
(`max(revision_seq)`, per-Project over visible revisions, with an optional
`preferred_revision_id` pin on `ProjectResourceLink`); (3) **`generated_by` demoted to a
derived traversal** (`Revision ←imported_as← Artifact ←produced← Run/Session`), and the
matrix renumbered to `GlobalProvenanceEdge` #1–7 + `ProjectKnowledgeEdge` #8–10; (4)
**Harness** — two-layer architecture authority (implementation decisions within accepted
ADRs = Primary; constitutional changes = human) and an explicit `SubagentGrant` that must
be mapped by the subagent provider (read-only/reject otherwise); (5) **project-scoped
API lens** as the ordinary workspace surface; (6) **CredentialLease/InvocationContext**
last-mile secret model (ephemeral lease, plural credential kinds); (7) **`ContentStore`**
byte-ownership boundary (fsspec; internal artifacts = `authority=revolab`);
(8) `GlobalResourceRegistry` declared a Core shared identity primitive with honest
subtype-integrity wording. P2s: ScientificObject physical storage deferred to the Phase-1
spike (typed JSONB vs joined tables), ExternalIdentity mapping is a global assertion vs
project interpretation, AGENT_CONTEXT converged to SeriesRef/RevisionRef, ledger/PR body
swept to 7 phases. A fresh independent review follows. Status remains **PROPOSED —
pending human review**.

---

A second human review again returned **REQUEST CHANGES** (still "don't merge yet",
~80–85% of direction right). Its 12-plus findings have all converged on this branch:
(1) project-scoped authorization projection (`Actor → ProjectMembership →
ProjectResourceLink → visible resource → visible edge`) with global-identity !=
global-readability, and `ProjectObjectMembership` renamed `ProjectResourceLink`
(ADR-0008, COLLABORATION_IDENTITY); (2) SQL-valid Project deletion via tombstone
(`deleted_at`), hard-deleting active links and archiving Evidence/Decision, never
hard-deleting a Project row; (3) `SCIENTIFIC_GRAPH.md` as the single source of graph
truth (wire value standardized to `consumed_as_input_by`, SYSTEM diagram direction
fixed, and IMPLEMENTATION_ROADMAP no longer claims the stale `UNIQUE(project,…)` or a
"Decided" Generic-Relation target — physical relation shape deferred to the Phase-1
spike); (4) a frozen Evidence association contract (legal source/target kinds incl.
experiment/note as source, 1:1 cardinality, immutability); (5) one dependency DAG with
`A --> B` meaning "A imports/consumes B's public contract", shared by
DOMAIN_BOUNDARIES and SYSTEM_ARCHITECTURE; (6) `ProviderRuntimeHealth`
(READY/DEGRADED/UNREACHABLE) split from derived, never-stored
`CapabilityAvailability(actor,project)` (AVAILABLE/CREDENTIAL_MISSING/NOT_AUTHORIZED/
PROVIDER_UNAVAILABLE); (7) Harness step 7 renamed `INDEPENDENT REVIEW` with Ralph only
on explicit human request; (8) container-engine guardrails (docker = privileged
external capability, not a workspace write); (9) frozen `ScientificObjectSeries.id`
(`series_id`) / `ScientificObjectRevision.id` (`revision_id`), no ambiguous
`ScientificObject.id`; (10) `.agents/skills/` now matches the documented skill list by
adding `architecture-review` and `security-boundaries`; (11) clean-dependency-install
and full hosted-CI moved to verified (GHA green); (12) the five superseded pointer
files deleted. A fresh independent architecture review follows. Status remains
**PROPOSED — pending human review**.

Deliberately NOT implemented (deferred by the design: Phases 1–7 in
IMPLEMENTATION_ROADMAP): real auth/identity tables, real REvoCompute/REvoDesign/
OpenBio drivers, agent chat UI, typed-object migration of the backend, generated
TypeScript client. Backend tests (pytest) and frontend typecheck/test/build still
pass on the prototype (unchanged this phase).

### 2026-09-08 — Ralph consistency loop round 1 (PR #1): zero P0/P1 remain; PROPOSED pending human review

A fresh-agent (Ralph) consistency loop audited the proposed architecture against its
canonical documents with four independent read-only auditors (object/graph/provenance;
Project/registry/visibility; Provider/Identity/Agent/credentials; harness safety +
roadmap). No new subsystem or concept was added — the round only removed contradictions
so each concept has one canonical answer. Zero P0 and eight P1 findings were closed,
plus the cheap P2s:

- `supersedes` is an edge (`ProjectKnowledgeEdge` #9), not a stored `supersedes_id`
  self-FK; `superseded` is a derived status (COLLABORATION_IDENTITY).
- Series vs Revision endpoint wording: conceptual semantic relations address
  `series_id`, content/provenance relations address `revision_id`; an observation maps
  to `kind=observation` (SCIENTIFIC_OBJECT_MODEL, SCIENTIFIC_GRAPH).
- Ownership wording: Project owns `ProjectResourceLink`, not membership
  (SYSTEM_ARCHITECTURE, DOMAIN_BOUNDARIES); the credential binding is
  `(actor_id, provider_key, kind, secret_ref)` and Actor-scoped, not project-local
  (COLLABORATION_IDENTITY).
- Provider callability formula now includes "for the calling Actor" and "project policy
  permits" in every document that states it (SYSTEM_ARCHITECTURE, DOMAIN_BOUNDARIES,
  IMPLEMENTATION_ROADMAP), matching CLAUDE.md invariant #10.
- Cheap P2s: `ArtifactReference --consumed_as_input_by--> Run/Session` added to the
  SYSTEM_ARCHITECTURE diagram; Evidence `cited_as` vs `polarity` value sets pinned;
  ADR-0008 Project-deletion step now archives `ProjectKnowledgeEdge`;
  "import/promotion boundary" renamed "import boundary"; IMPLEMENTATION_ROADMAP final
  invariants re-synced to CLAUDE.md; the durable-record/node-category wording aligned
  with SCIENTIFIC_GRAPH's nine-node list.

Two follow-up fresh verification passes then caught and closed two further P1s and the
remaining cheap P2s: `derived_from` is Revision→Revision only (never a Series endpoint)
and `imported_as` is `ArtifactReference | ExternalReference → Revision` (the
"why does this object exist" gloss no longer reads `imported_as` as Revision→Revision);
the GlobalResourceRegistry off-by-one is "seven" global tables; the decision-log facet is
"draft / committed / superseded"; and the roadmap final invariant #8 now carries the
global-vs-project-scoped split.

A final fresh audit pass (two independent read-only verifiers) found **NO P0 and NO P1**
on the pushed consistency head `f59fae2`. CI green on that head (runs #33–#34). Status
remains **PROPOSED — pending human review**.
