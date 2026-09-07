# ADR-0009: Typed Scientific Object Model (Base Record + Extension Tables)

## Context
The bootstrap models one `ScientificObject` table with `object_type` enum + free
`metadata_json`, plus a `parent_id` self-tree with destructive cascade. This is the
God-object / everything-is-JSON failure mode, and it conflates UI organization with
scientific semantics.

## Decision
- **Base record + typed extension tables.** A universal core `scientific_objects`
  spine (id, type, label, description, timestamps, created_by, organization anchor)
  plus one typed extension table per registered scientific type. No unvalidated JSON
  payload for scientific metadata.
- **Core owns the type registry.** `object_type → {typed model, view schema,
  validator, versioned?}`. New types are deliberate Core schema decisions, not
  runtime plugins and not JSON blobs.
- **Separate organization from scientific relation.** Folders/container-membership
  carry navigation; typed `Relation` edges are the sole scientific-semantics carrier.
- **First-class `ExternalId` and `Alias` registries.** Canonical identity is the
  UUID; external IDs are namespaced; aliases are search synonyms.
- **Content-immutable + opt-in per-type versioning** at the domain-service layer.
- **Remove destructive `delete-orphan`** from scientific objects.

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
