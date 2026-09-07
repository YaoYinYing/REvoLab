# ADR-0007: Skills Are Agent Knowledge

## Context
Agents need project-scoped procedures and interpretation guidance, but skills must not drift from application truth.

## Decision
Skills describe when, why, and how to use typed tools. Backend schemas and API contracts remain canonical.

## Consequences
Generated skill material can be checked for drift and authorization remains in services/drivers.

## Rejected alternatives
Skills containing business logic, authorization, executable provider behavior, or manually duplicated schemas.
