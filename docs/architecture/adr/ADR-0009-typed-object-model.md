# ADR-0009: Typed Scientific Object Model (Series/Revision + Validated Payload)

## Context
The bootstrap models one `ScientificObject` table with `object_type` enum + free
`metadata_json`, plus a `parent_id` self-tree with destructive cascade. This is the
God-object / everything-is-JSON failure mode, and it conflates UI organization with
scientific semantics.

## Decision
- **Frozen semantic contract; physical payload representation deferred to the Phase-1
  spike.** A universal `scientific_object_series` spine (`series_id`, `object_type`,
  label, description, timestamps, created_by) and an immutable
  `scientific_object_revision` (`revision_id`, `series_id`, `revision_seq`, `checksum`,
  `object_type`, `schema_version`) carrying a **schema-validated typed payload** (per-type
  Pydantic/JSON-Schema; **no un-validated JSON**). Whether the typed payload is stored
  as **typed `JSONB` + generated columns** or **joined per-type extension tables** is
  decided by the Phase-1 spike — PR1 does **not** freeze it. **No organization column on
  the spine**: project-local placement is a folder/container annotation on
  `project_resource_link`, owned by the Project domain. **No `current_revision_id`:** the
  current revision is derived (`max(revision_seq)`; per-Project visible current uses the
  Project's visible set + optional `preferred_revision_id` pin).
- **Core owns the type registry.** `object_type → {typed model, view schema,
  validator, versioned?}`. New types are deliberate Core schema decisions, not
  runtime plugins and not untyped JSON.
- **Separate organization from scientific relation.** Project-local folders (the
  `project_resource_link` folder/container annotation) carry navigation; typed
  `Relation` edges are the sole scientific-semantics carrier.
- **First-class `ExternalIdentity` registry (+ series↔external-identity global
  assertion) and `Alias` registries.** Canonical internal identity is
  the `series_id`; **durable external identity is `(authority, native_id)`** with an
  identity authority (uniprot, pdb, doi, ...) — never a resolver/provider; aliases are
  search synonyms. The series↔identity mapping is a **global assertion**
  (`UNIQUE(external_identity_id, qualifier)`), not a per-Project opinion.
- **Content-immutable + opt-in per-type versioning** at the domain-service layer.
  **Conceptual identity (`series_id`) is distinct from immutable revision identity
  (`revision_id`)**: each new content revision is an independent immutable record
  with its own UUID; provenance edges address `revision_id`, never `series_id`.
- **Remove destructive `delete-orphan`** from scientific objects.
- **Objects are a global resource** (no `project_id` ownership): they are bound to
  Projects only through `project_resource_link` and mutated only under
  `ResourceStewardship` (see COLLABORATION_IDENTITY / ADR-0008), so deleting a Project
  never deletes objects.

## Consequences
- Validation is frozen; physical payload layout is re-openable in the spike without
  changing any domain contract.
- Organization no longer implies scientific meaning or destructive ownership.
- Type extension is a deliberate, reviewed schema change; per-field evolution can use
  `schema_version` without a migration per field (if the spike adopts typed JSONB).

## Rejected alternatives
- enum + untyped JSON blob (un-validated payload, unqueryable).
- Registry-driven runtime column plugins (can't own physical storage without
  migrations; fragile base class).
- Hybrid typed + fallback JSON (two silent storage mechanisms).
