# Implementation State

Last verified: 2026-09-07

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
- GitHub Actions CI is green on the architecture PR (head `27ceba7`, the round-3 convergence commit; GHA runs #15–#16 all green): backend Ruff/mypy/pytest + Alembic `upgrade head`/`check` against PostgreSQL 16; frontend typecheck/test/build all pass.

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
  closed enum incl. `generated_by`/`evaluates`/`selects`; generic-Relation polymorphic
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

Deliberately NOT implemented (deferred by the design: Phases 1–6 in
IMPLEMENTATION_ROADMAP): real auth/identity tables, real REvoCompute/REvoDesign/
OpenBio drivers, agent chat UI, typed-object migration of the backend, generated
TypeScript client. Backend tests (pytest) and frontend typecheck/test/build still
pass on the prototype (unchanged this phase).
