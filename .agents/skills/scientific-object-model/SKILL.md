---
name: scientific-object-model
version: 0.2.0
description: Create and inspect typed scientific objects as Series/Revision in a project.
---

# Scientific Object Model

## Canonical sources
- `docs/architecture/SCIENTIFIC_OBJECT_MODEL.md` — the semantic contract.
- `backend/src/revolab/models.py` — `ScientificObjectSeries` / `ScientificObjectRevision`.
- `backend/src/revolab/domain/types_registry.py` — the Core type registry (the
  validation authority for the typed payload).
- `backend/src/revolab/enums.py` — the canonical `ObjectType` enum.
- `backend/src/revolab/services.py` — `create_object`, `append_revision`,
  `update_series`, `archive_series`.

## Rules
- A ScientificObject has a durable conceptual identity (`series_id`) distinct from
  its immutable revision identity (`revision_id`). Content/provenance edges address
  revisions; conceptual semantic relations address series.
- Content change is always a new revision, never an in-place edit.
- The typed payload is validated by the Core type registry; never write raw JSON
  or invent an unvalidated metadata blob.
- Project-local organization (folders/placement) lives on `ProjectResourceLink`,
  never as a column on the global series.

Do not duplicate server enums or schemas into agent text, and do not invent
biological ontology tables — point at the canonical backend.
