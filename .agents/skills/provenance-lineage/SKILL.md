---
name: provenance-lineage
version: 0.2.0
description: Link project context to external evidence without copying execution truth.
---

# Provenance And Lineage

## Canonical sources
- `docs/architecture/SCIENTIFIC_GRAPH.md` — the single canonical edge matrix and
  per-edge creator authority.
- `docs/architecture/EVIDENCE_PROVENANCE.md` — reference vs evidence vs decision.
- `backend/src/revolab/models.py` — `GlobalProvenanceEdge`, `DecisionEvidence`,
  `DecisionTarget`, `DecisionSupersedes`.
- `backend/src/revolab/services.py` — the typed edge commands
  (`add_variant_of`, `add_derived_from`, `add_represents`, `add_evaluates`,
  `add_consumed_input`, `record_produced`, `import_revision`).
- `backend/src/revolab/queries.py` — `generated_by` (derived, never persisted).

## Rules
- A reference is a fact; Evidence is an interpreted claim. Do not conflate them.
- Global provenance edges (#1-7) are created only by their typed domain command
  with the per-edge authority — never a generic relation writer.
- `generated_by`, `input_objects`, `output_artifacts`, and `originating_run` are
  derived aggregates over the edges, never persisted columns.
- Durable identity is `(authority, native_id)` + an opaque UUID; never a path, a
  mutable tag, or a resolver/provider name.

Record provenance, warnings, and unresolved assumptions explicitly.
