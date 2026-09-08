# REvoLab

REvoLab is the scientific project and context workspace of the REvo ecosystem. It answers what is happening in a research project, what is known, which evidence supports a decision, and what should happen next.

REvoLab is independent from REvoCompute and REvoDesign:

- REvoLab owns projects, typed scientific objects (Series/Revision), relationships, evidence, decisions, and context.
- REvoCompute owns execution: Runner, Task, Slurm, Apptainer, and mutable run state.
- REvoDesign owns interactive protein design and structural analysis.

## Current slice (Phase 1)

The backend implements the accepted Phase-1 architecture: a minimal authority
substrate (`Actor`, `ProjectMembership`, `ResourceStewardship`, `MutationGrant`
issuance), the `GlobalResourceRegistry` referential spine, typed
`ScientificObjectSeries`/`ScientificObjectRevision`, reference identity cards
(run/session/artifact/literature/external), the frozen edge matrix (#1-7 global
provenance, #8-10 project knowledge), Evidence with the interpreted-claim model,
Decision with the `draft → committed` promotion gate, and a content-addressed
`ContentStore` (fsspec local backend). The ordinary API is project-scoped.

The React/Vite frontend is still the prototype fixture workspace; it is replaced
by generated-contract-driven UI in Phase 2.

## Development

Backend:

```bash
cd backend
uv sync --extra dev
uv run pytest
uv run ruff check backend
uv run mypy
uv run uvicorn revolab.main:app --app-dir src --reload
cd ..
```

Frontend:

```bash
cd frontend
npm install
npm run test
npm run build
npm run dev
```

PostgreSQL development services:

```bash
docker compose up -d postgres
```

The default local backend database is SQLite. Set `REVOLAB_DATABASE_URL` to a PostgreSQL URL for deployment-like development.

Schema migrations live under `backend/migrations/` and are managed from `backend/`:

```bash
cd backend
alembic upgrade head
alembic check
```

## Architecture

REvoLab owns scientific context and relationships; REvoCompute, REvoDesign, and
external providers own their capabilities and execution truth. The design is a
relational scientific graph over typed scientific objects, evidence/provenance,
and decisions, accessed through typed domain services behind a project-scoped API.

The accepted architecture lives in `docs/architecture/` — start with
`SYSTEM_ARCHITECTURE.md` (domains + diagrams) and `DOMAIN_BOUNDARIES.md`; see
`IMPLEMENTATION_ROADMAP.md` for the staged implementation plan, `SCIENTIFIC_GRAPH.md`
for the single edge matrix, and the ADRs under `docs/architecture/adr/`.
`IMPLEMENTATION_STATE.md` records verified implementation state only.

## License

REvoLab is Apache-2.0. Reference projects are documented as conceptual sources only; their implementation code is not vendored here.
