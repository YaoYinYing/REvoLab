# ADR-0014: Generated API / Frontend Contract

## Context
The frontend currently hardcodes `object_type`/`relation_type` strings and consumes a
fixture graph. The design brief requires the frontend to consume generated contracts
so domain enums are never duplicated between backend and frontend.

## Decision
- **FastAPI owns all domain schemas/enums.** The frontend consumes **only** a
  generated TypeScript client.
- **Generation is a build step** with a **CI drift check** that fails if the committed
  generated output differs from a fresh regeneration.
- **Split the bootstrap `GET /projects/{id}` whole-graph payload** into project
  metadata + paginated collections + an explicit bounded graph-traversal query.
- **Object-detail aggregate** served by a dedicated `GET /api/objects/{id}` endpoint
  (object + provenance + evidence + decisions) assembled by the backend domain service
  in one query batch; `?mode=summary` for list views.
- **Separate CRUD from domain commands** (e.g. `POST /decisions/{id}/commit`,
  `/supersede`, `POST /runs/{id}/refresh`).
- **Pagination** on every collection; **ETag/If-Match** for optimistic concurrency on
  mutable resources.

## Consequences
- No manually duplicated enums anywhere in the frontend.
- The frontend can render any provider schema and any object type it is sent.
- First real frontend integration surfaces drift immediately (the invariant becomes
  executable in Phase 2).

## Rejected alternatives
- Hand-maintained TypeScript types mirroring the backend (duplicated schemas).
- Returning the whole project graph as the default read (O(project), unpaginated).
