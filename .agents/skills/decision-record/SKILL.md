---
name: decision-record
version: 0.2.0
description: Record a project decision with evidence and next actions.
---

# Decision Record

## Canonical sources
- `docs/architecture/EVIDENCE_PROVENANCE.md` — Decision lifecycle and the
  promotion boundary.
- `ADR-0011` — the draft → committed gate.
- `backend/src/revolab/services.py` — `create_decision`, `update_decision`,
  `commit_decision`, `supersede_decision`.
- `backend/src/revolab/models.py` — `Decision`, `DecisionEvidence`,
  `DecisionTarget`, `DecisionSupersedes`.

## Rules
- A Decision starts as `draft`; only `commit` makes it project truth. A committed
  Decision is immutable and is corrected by a superseding Decision, never edited.
- `cites` carry a per-citation `cited_as`; `selects` target a Series or Revision
  explicitly (with target kind).
- Every cited Evidence must belong to the same Project; every selected target must
  already be visible through that Project's links (no ghost knowledge).

Do not encode executable authorization or provider behavior in a Decision.
