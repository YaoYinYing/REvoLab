# ADR-0011: Decision Promotion Boundary

## Context
The bootstrap lets any `POST /decisions` immediately create a durable truth record.
The design brief requires distinguishing generated suggestion from accepted project
truth, and preventing chat from becoming project truth.

## Decision
- **A Decision starts as `proposed`/`draft`**, authored by whoever (including an
  Agent), with its cited Evidence. A proposed Decision is *in the project but not
  project truth*.
- **`commit` is a distinct, auditable domain operation** (`propose → committed`) by
  an authorized human actor or a policy-governed actor with commit authority.
- **A committed Decision is immutable** once it influences later work; it is
  superseded by a newer Decision via a `supersedes` edge, never edited or deleted.
- **"Current scientific conclusion" is a derived query**: the most recently
  committed, not-superseded Decision on a question.
- Invariant (adopted verbatim): **Agent output is conversation until explicitly
  promoted into project knowledge.**

## Consequences
- Chat history can never become project truth except through a typed, validated,
  authority-checked promotion.
- The authority matrix (see ADR-0013) governs which operations are automatic,
  policy-controlled, human-approved, or never-agent.

## Rejected alternatives
- Immediate-create-as-truth (current bootstrap; no promotion).
- Editing a committed Decision in place (destroys rationale/history).
- Deleting a Decision to correct it (loss of documented position).
