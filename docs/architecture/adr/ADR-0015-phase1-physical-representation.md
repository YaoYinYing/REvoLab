# ADR-0015: Phase-1 Physical Representation of Typed Payload and Edges

## Context

The top-level architecture deliberately deferred two *physical* storage questions
to the Phase-1 executable spike (`SCIENTIFIC_GRAPH.md`, `SCIENTIFIC_OBJECT_MODEL.md`,
`COLLABORATION_IDENTITY.md`):

1. The typed scientific payload: a validated `JSONB` column + `schema_version`
   versus joined per-type extension tables.
2. The provenance/knowledge edges: one physical edges table shared by
   `GlobalProvenanceEdge` + `ProjectKnowledgeEdge` versus separate edge-family
   tables.

Phase 1 must implement, not re-defer, these choices against real query,
validation, migration, and extension needs.

## Decision

### Typed payload: one validated, schema-versioned JSONB column

The typed scientific payload hangs on `ScientificObjectRevision.payload` as a
single JSONB column (SQLite: JSON; PostgreSQL: JSONB) with a `schema_version`
column. It is **only ever written through the Core type registry**
(`revolab/domain/types_registry.py`), whose per-type Pydantic models reject any
unknown or wrongly-typed field (`extra="forbid"`) at the domain boundary. There
is therefore no unvalidated JSON in the database.

Rejected: joined per-type extension tables. They duplicate the discriminator on
the revision spine, require a migration per changed field, and multiply
command/query paths across ten near-identical tables for no Phase-1 query need;
the identity/governance spine (`series` + `revision`) and the edge tables already
carry every field Phase 1 queries filter or join on. Adding a field remains a
Pydantic change on the owning type, not a schema migration — consistent with the
fixed vocabulary of Core-owned types (a new *type* or a needed *indexed field*
is still a deliberate, reviewed Core migration).

Evidence: `backend/tests/test_scientific_objects.py::test_typed_payload_validated_against_per_type_schema`
proves unknown fields and missing required fields are rejected, and that a valid
payload round-trips through the registry.

### Edges: one table per frozen ownership class, registry-FK endpoints

`GlobalProvenanceEdge` (#1-7) and the project knowledge edges (#8-10) map to the
two frozen ownership classes as **two physical families**:

- `global_provenance_edges` — one table for #1-7. Endpoints are
  `source_id`/`target_id` FKs to `GlobalResourceRegistry.resource_id` (the single
  non-polymorphic FK target) plus non-null `source_kind`/`target_kind` columns; a
  CHECK rejects self-edges; `superseded_by_id` provisions immutable correction.
  No `project_id`, never Project-archived.
- Project knowledge edges use the natural per-edge tables: #10 `cites` is the
  `decision_evidence` composite-key join (KEEP, now with `cited_as`); #8
  `selects` is `decision_targets`; #9 `supersedes` is `decision_supersedes` with
  `UNIQUE` on both directions (0..1-out / 0..1-in). These are archived with their
  Decision/Evidence on Project tombstone.

Rejected: fully separate per-relation edge-family tables (nine tables). The edge
matrix itself has polymorphic endpoints (#5, #7, #8), so per-family tables could
not avoid a registry FK or nullable per-kind FKs anyway; they would only move the
same pattern into nine command/query paths. The registry-FK + kind-column shape
keeps no nullable columns, preserves one query/immutability shape for all #1-7,
and matches the matrix's requirement that the endpoint kind be stored. Endpoint
kind validity per `relation_type` is a domain-service invariant (the frozen
contract already requires it in schema/domain validation regardless of physical
shape); the registry FK guarantees each endpoint is a real global resource, and
the "registry row ⇒ exactly one concrete row of matching kind" side is the
already-accepted domain-service invariant (integrity honesty, ADR-0008).

Evidence: `backend/tests/test_provenance.py` covers per-edge authority, endpoint
kind validation, self-edge rejection, immutable correction via `superseded_by_id`
(recorded immutability is enforced at the domain layer), and the derived
`generated_by` traversal (never a persisted column/edge).

## Phase-1 authority mapping for #5/#6 (no provider yet)

The frozen creator authority for `consumed_as_input_by` (#5) is "task-submission
authority + readable input" and for `produced` (#6) is "run/artifact import
authority". No task/providers exist in Phase 1, so the command boundary maps
these to the closest executable anchor: the acting Project must **steward the
input** (for #5, the source revision's series or the source artifact) or
**steward the producing run/session** (for #6), plus the target/artifact must be
visible through that Project. The service-layer architecture keeps this mapping
at the command boundary (`revolab/services.py`), not inside the Evidence /
Provenance domain module, so when real task/providers land (Phase 3-4) the
authority rule is replaced without touching domain code.

## Consequences

- `alembic upgrade head` and `alembic check` report no drift on both SQLite and
  PostgreSQL (verified against a clean PostgreSQL 16 database).
- One immutable-edge identity/correction path (`superseded_by_id`) for all global
  provenance edges; per-family correctness invariants live in their owning domain
  command, not in a polymorphic blob.
- Payload validation is a single Core type-registry authority; no raw JSON can
  reach the revision table.
