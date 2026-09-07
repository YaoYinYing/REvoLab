# ADR-0003: Relational Evidence Graph

## Context
The first product slice needs typed relationships and evidence, not graph infrastructure.

## Decision
Use PostgreSQL tables for objects, relations, evidence, references, and decisions; assemble graph queries at the application layer.

## Consequences
Deployment and migrations remain conventional, with a clear path to optimized queries later.

## Rejected alternatives
Neo4j, Kafka, event-stream infrastructure, or event sourcing without demonstrated requirements.
