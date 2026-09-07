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
uv sync --extra dev
uv run pytest
uv run uvicorn revolab.main:app --app-dir backend/src --reload
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

## Architecture

The logical evidence graph is relational in PostgreSQL. Projects form a hierarchical tree of scientific objects; typed relations, evidence references, and decisions provide cross-tree context. Drivers are provider-neutral Python protocols discovered through entry points and managed by an explicit application-scoped registry.

See `docs/architecture/` for the architectural overview, domain model, reference study, and ADRs. `IMPLEMENTATION_STATE.md` records verified implementation state only.

## License

REvoLab is Apache-2.0. Reference projects are documented as conceptual sources only; their implementation code is not vendored here.
