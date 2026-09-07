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

## Not yet verified

- A clean dependency installation in a fresh environment.
- Generated OpenAPI TypeScript client and frontend/backend live integration; the frontend currently uses a fixture graph.
- Browser smoke testing and full hosted CI execution.
- Authentication, project membership, artifact storage, provider drivers, and production deployment.

This file records actual repository state, not future architecture plans.

## 2026-09-07 — Architecture design phase COMPLETE

Delivered a reconciled top-level architecture (see `docs/architecture/` and ADRs
0008–0014). This is the approved target design; the current backend/frontend remain
the prototype the design overturns.

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
  the accepted documents to avoid dual sources of truth; `reference-study.md` remains
  the external-reference analysis.

Deliberately NOT implemented (deferred by the design: Phases 1–6 in
IMPLEMENTATION_ROADMAP): real auth/identity tables, real REvoCompute/REvoDesign/
OpenBio drivers, agent chat UI, typed-object migration of the backend, generated
TypeScript client. Backend tests (pytest) and frontend typecheck/test/build still
pass on the prototype (unchanged this phase).
