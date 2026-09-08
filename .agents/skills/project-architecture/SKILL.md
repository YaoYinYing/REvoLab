---
name: project-architecture
version: 0.1.0
description: Keep REvoLab project context, execution, and provider ownership boundaries explicit.
---

# Project Architecture

## When to use
Use when changing domain ownership, adding a provider integration, or reviewing dependency direction.

## Canonical sources
Read `CLAUDE.md`, `docs/architecture/overview.md`, and the backend OpenAPI contract.

## Procedure
1. Identify the owner of each proposed concept.
2. Keep scientific relationships in Core and external capabilities behind drivers.
3. Verify the change with an executable focused test.

## Boundary
Do not add scheduler, task, storage, or provider-specific execution models to Core.
