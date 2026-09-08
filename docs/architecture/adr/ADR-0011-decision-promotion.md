# ADR-0011: Decision Promotion Boundary

## Context
The bootstrap lets any `POST /decisions` immediately create a durable truth record.
The design brief requires distinguishing generated suggestion from accepted project
truth, and preventing chat from becoming project truth.

## Decision
- **A Decision starts as `draft`**, authored by whoever (including an Agent), with its
  cited Evidence and selected targets as **mutable draft state** (not persisted graph
  edges yet). Status is exactly `draft | committed` (+ derived `superseded`) — there is
  no separate `proposed`/`open`/`concluded` stored state. A draft is *in the project but
  not project truth*.
- **`commit` atomically freezes the statement and materializes the immutable
  `ProjectKnowledgeEdge` rows** (`selects`, `cites`); before commit a draft's
  statement/cites/selects may be edited freely.
- **`commit` is a distinct, auditable domain operation** (`draft → committed`) by an
  authorized human actor or a policy-governed actor with commit authority. **This is
  the promotion gate.**
- **Promotion is scoped to this Decision boundary only** (reviewer finding #10): it is
  NOT a gate on ordinary domain writes. Creating an object or attaching evidence is a
  typed domain operation (automatic or policy-gated per ADR-0013), not "promotion".
- **A committed Decision is immutable** once it influences later work; it is
  superseded by a newer Decision via a `supersedes` edge, never edited or deleted.
- **"Current scientific conclusion" is a derived query**: the most recently
  committed, not-superseded Decision on a question.
- Invariant (adopted verbatim): **Agent output is conversation until explicitly
  promoted into project knowledge** — where "promote" means committing a knowledge
  assertion (a Decision), not creating objects/evidence.

## Consequences
- Chat history can never become project truth except through a typed, validated,
  authority-checked promotion.
- The authority matrix (see ADR-0013) governs which operations are automatic,
  policy-controlled, human-approved, or never-agent.

## Rejected alternatives
- Immediate-create-as-truth (current bootstrap; no promotion).
- Editing a committed Decision in place (destroys rationale/history).
- Deleting a Decision to correct it (loss of documented position).
