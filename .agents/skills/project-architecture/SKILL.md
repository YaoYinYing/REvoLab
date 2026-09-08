---
name: project-architecture
version: 0.2.0
description: Keep REvoLab project context, execution, and provider ownership boundaries explicit.
---

# Project Architecture

## When to use
Use when changing domain ownership, adding a provider integration, or reviewing
dependency direction.

## Canonical sources
Read `CLAUDE.md` (the short invariants), `docs/architecture/SYSTEM_ARCHITECTURE.md`
and `docs/architecture/DOMAIN_BOUNDARIES.md` (the single dependency DAG), the ADRs
under `docs/architecture/adr/`, and the backend OpenAPI contract at `/openapi.json`.

## Procedure
1. Identify the owner of each proposed concept (exactly one owning domain).
2. Keep scientific relationships in Core and external capabilities behind drivers.
3. Keep the Core dependency DAG acyclic: `Scientific Object` and
   `Identity / Collaboration` are global leaves; Core never depends on the Agent.
4. Verify the change with an executable focused test.

## Boundary
Do not add scheduler, task, storage, or provider-specific execution models to
Core. Do not introduce a second source of truth for a schema/enum/contract.
