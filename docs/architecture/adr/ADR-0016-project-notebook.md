# ADR-0016: Project Notebook — Project-scoped Notes with Immutable Revisions

> **Status: Accepted** (Phase 10; human-accepted during PR #11 review).

## Context

Phase 9 added persistent, Actor × Project scoped Conversations as durable **working
memory**. Phase 10 must add the missing rung between private conversation and formal
Evidence/Decision truth: a Project-shared working document that a team can edit
together without any of its text becoming scientific truth.

The architecture already fixes the surrounding boundaries (`SCIENTIFIC_GRAPH.md`,
`EVIDENCE_PROVENANCE.md`, `PROJECT_CONVERSATIONS.md`, `AGENT_CONTEXT.md`):

- Persistence is always a typed domain command, never a raw write.
- The promotion gate applies only to `Decision draft → committed`.
- A Note must not enter the scientific graph or duplicate external execution truth.
- Project content read by the Agent is untrusted data, never authority.

What was not yet decided is the durable physical shape of a shared, editable Note
and how concurrent edits are ordered without a mutable current pointer.

## Decision

### Project-scoped `ProjectNote` + immutable `ProjectNoteRevision`

- `ProjectNote` is Project-scoped working knowledge owned by the Project namespace,
  not by its author. The creating Actor is audit metadata; there are no per-note
  ACLs and no sharing outside the Project. Read = readable membership
  (owner/member/viewer); mutate = owner/member.
- `ProjectNoteRevision` is INSERT-only with named uniqueness on
  `(note_id, revision_seq)`. The latest revision is **derived** from
  `max(revision_seq)`; no mutable `current_revision_id` pointer is introduced.
- A Note is not a `GlobalResourceRegistry` entry, a ScientificObject, Evidence, a
  Decision, or a provenance node. Project tombstone or membership revocation takes
  effect immediately; archiving a Note is non-destructive.

Rejected: an `updated_body` column on the Note (loses history and is not
revision-safe); a mutable `current_revision_id` (a second source of truth for the
same fact); per-note ACLs (membership is already the single authorization unit).

### Optimistic concurrency as a typed conflict

Appending a revision carries the `base_revision_seq` the client edited.
PostgreSQL takes the `ProjectNote` row lock in **both** `append_revision` and
`patch_note` (rename/archive), so concurrent appends and archiving order across
processes. SQLite ignores `SELECT ... FOR UPDATE`, so it additionally uses a
bounded process-level per-note lock (weak-value keyed, 30-second wait, then a
typed retryable 409) that orders append/archive within one process; SQLite is a
single-process dev/test substrate and must not run multi-worker. The
`(note_id, revision_seq)` uniqueness constraint is the backend-independent
backstop. A stale base fails closed with a typed 409 rather than overwriting
another member's work.

### Mentions as typed, non-semantic references

`NoteMention` mirrors the frozen Evidence endpoint pattern: an optional
global-registry target (`target_resource_id` + stored registry kind) and optional
Project-scoped Evidence/Decision FKs, with a CHECK enforcing exactly one target.
It reuses neither `RelationType` nor a polymorphic graph framework, carries no
scientific semantics, and creates no provenance edge. Every target is validated
through the current Project read lens at write time; an invisible or foreign target
fails closed with the same 403 as an unknown id. Mentions hang off an immutable
revision, so a target that later becomes unavailable leaves an unresolved
historical mention (`resolved=false`) instead of rewriting the note.

Cross-domain mention composition lives in the application orchestration layer: the
Notebook service consumes `domain.provenance.evidence_mention_target` and
`domain.knowledge.decision_mention_target` public contracts rather than those
domains' ORM internals, so the Project-domain sub-boundary adds no Project →
Evidence/Knowledge edge to the accepted nine-domain Core DAG. All mention
validation runs before any durable mutation (atomic commands).

Editing semantics for `mentions` are tri-state: omitted inherits the previous
revision's mention identities (already-authorized references are preserved even if
they later resolve as unavailable), explicit `[]` clears them, and a non-empty list
replaces them after normal authorization.

### Bounded content, safe rendering

The content format is bounded Markdown/plain structured text; there is no block
editor or CRDT in this phase. The frontend renders Note bodies through a minimal,
closed Markdown renderer that never uses `dangerouslySetInnerHTML`, so raw
HTML/script cannot execute. Note text is always untrusted Project data.

### Explicit capture and explicit interpretation

Conversation → Note ("Save to Project Note") is an explicit human action that
copies visible message content into an ordinary Note body; there is no live
synchronization and no second copy of conversation authority. A Note never becomes
Evidence or a Decision automatically; any such action invokes the canonical
Evidence domain command with explicit interpretation/target fields. `Evidence(kind=note)`
remains the canonical representation of a note-like scientific claim, and Note is
not added to the scientific-graph source-kind matrix.

## Consequences

- The ladder `Conversation → Note → Evidence → Decision` has one durable physical
  owner per rung and one explicit human action between each pair.
- `alembic upgrade head` and `alembic check` report no drift on SQLite and on
  PostgreSQL 16; the concurrency regression runs on PostgreSQL.
- The Agent can read explicitly selected Notes only, inside the single untrusted
  data block; a Note cannot widen the ToolCatalog, autonomy, or `explicit_action`
  gating.
- Note semantics live in one normative document (`PROJECT_NOTEBOOK.md`); consuming
  documents point at it.
