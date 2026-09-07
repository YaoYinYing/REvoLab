# Implementation State

Last verified: 2026-08-27

## Exists

- Standalone repository metadata for a Python 3.12+ package with FastAPI, Pydantic Settings, SQLAlchemy, Alembic configuration, PostgreSQL driver, Uvicorn, and development tooling.
- SQLAlchemy mappings for Project, hierarchical ScientificObject, Relation, Evidence, Decision, and DecisionEvidence.
- Pydantic request/response models with object-tree parent references, provider/external-ID pairing validation, and relation self-reference validation.
- FastAPI health endpoint and project/object/relation/evidence/decision creation/list/graph endpoints.
- Application-scoped driver protocol, lifecycle states, collision handling, entry-point discovery, and explicit stop behavior.
- Focused backend API tests for the project graph and negative relation/reference cases: 6 tests pass.
- Backend Ruff, mypy, Alembic drift check, and SQLite migration apply pass in the local `.venv`.
- React/Vite frontend prototype with a hierarchical object tree, scientific workspace, relation/evidence trail, decision inspector, responsive layout, and one component test; frontend typecheck, test, and production build pass.
- PostgreSQL Docker Compose service, Alembic environment configuration, GitHub Actions CI configuration, architecture documentation, ADR directory, agent conventions, and Apache-2.0 licensing files.

## Not yet verified

- A clean dependency installation in a fresh environment.
- Alembic migration generation/application against PostgreSQL.
- Generated OpenAPI TypeScript client and frontend/backend live integration; the frontend currently uses a fixture graph.
- Browser smoke testing and full hosted CI execution.
- Authentication, project membership, artifact storage, provider drivers, and production deployment.

This file records actual repository state, not future architecture plans.
