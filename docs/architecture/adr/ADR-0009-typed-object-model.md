# ADR-0009: Typed Scientific Object Model (Base Record + Extension Tables)

## Context
The bootstrap models one `ScientificObject` table with `object_type` enum + free
`metadata_json`, plus a `parent_id` self-tree with destructive cascade. This is the
God-object / everything-is-JSON failure mode, and it conflates UI organization with
scientific semantics.

## Decision
- **Base record + typed extension tables.** A universal core `scientific_object_series`
  spine (`series_id`, `object_type`, label, description, timestamps, created_by) plus
  one typed extension table per registered scientific type. **No organization column
  on the spine**: project-local placement is a folder/container annotation on
  `project_resource_link`, owned by the Project domain. No unvalidated JSON payload
  for scientific metadata.
- **Core owns the type registry.** `object_type → {typed model, view schema,
  validator, versioned?}`. New types are deliberate Core schema decisions, not
  runtime plugins and not JSON blobs.
- **Separate organization from scientific relation.** Project-local folders (the
  `project_resource_link` folder/container annotation) carry navigation; typed
  `Relation` edges are the sole scientific-semantics carrier.
- **First-class `ExternalIdentity` registry (+ series↔external-identity mapping) and
  `Alias` registries.** Canonical internal identity is
  the `series_id`; **durable external identity is `(authority, native_id)`** with an
  identity authority (uniprot, pdb, doi, ...) — never a resolver/provider; aliases are
  search synonyms.
- **Content-immutable + opt-in per-type versioning** at the domain-service layer.
  **Conceptual identity (`series_id`) is distinct from immutable revision identity
  (`revision_id`)**: each new content revision is an independent immutable record
  with its own UUID; provenance edges address `revision_id`, never `series_id`.
- **Remove destructive `delete-orphan`** from scientific objects.
- **Objects are a global resource** (no `project_id` ownership): they are bound to
  Projects only through `project_resource_link` (see COLLABORATION_IDENTITY /
  ADR-0008), so deleting a Project never deletes objects.

## Consequences
- Queryable, constrained, typed columns for scientific payload; no God-object; no
  things-everything-JSON.
- Organization no longer implies scientific meaning or destructive ownership.
- Type extension is a deliberate, reviewed migration.

## Rejected alternatives
- enum + JSON blob (weakly typed, unqueryable).
- Registry-driven runtime column plugins (can't own physical storage without
  migrations; fragile base class).
- Hybrid typed + fallback JSON (two silent storage mechanisms).
