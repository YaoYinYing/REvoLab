# REvoLab

REvoLab is the scientific project and context workspace of the REvo ecosystem. It answers what is happening in a research project, what is known, which evidence supports a decision, and what should happen next.

REvoLab is independent from REvoCompute and REvoDesign:

- REvoLab owns projects, hierarchical scientific objects, relationships, evidence, decisions, and context.
- REvoCompute owns execution: Runner, Task, Slurm, Apptainer, and mutable run state.
- REvoDesign owns interactive protein design and structural analysis.

## Current slice

The repository contains a Python/FastAPI backend with typed SQLAlchemy models and resource APIs for projects, hierarchical scientific objects, relations, evidence, and decisions. It also contains a React/Vite scientific workspace prototype centered on the object tree and context inspector, plus driver lifecycle primitives, Alembic configuration, PostgreSQL Compose setup, tests, and CI configuration.

The frontend currently uses a small fixture graph while the generated OpenAPI client is being established. It does not claim to be a production integration until contract generation and browser smoke validation are complete.

## Development

Backend:

```bash
cd backend
uv sync --extra dev
uv run pytest
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
and decisions, accessed through domain services and capability-neutral providers.

The accepted architecture lives in `docs/architecture/` — start with
`SYSTEM_ARCHITECTURE.md` (domains + diagrams) and `DOMAIN_BOUNDARIES.md`; see
`IMPLEMENTATION_ROADMAP.md` for the staged implementation plan and the ADRs under
`docs/architecture/adr/`. `IMPLEMENTATION_STATE.md` records verified implementation
state only. Note: the current backend/frontend remain the prototype the accepted
design overturns (see the roadmap's bootstrap classification).

## License

REvoLab is Apache-2.0. Reference projects are documented as conceptual sources only; their implementation code is not vendored here.
