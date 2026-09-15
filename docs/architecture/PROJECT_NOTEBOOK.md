# Project Notebook (Structured Working Notes)

> **Status: Proposed — pending human acceptance** (Phase 10, PR #11). This is the
> normative owner of the Project Note / working-knowledge boundary once accepted.
> `PROJECT_CONVERSATIONS.md` (private working memory) and `AGENT_CONTEXT.md` (the
> consumer/authority lens) consume this document; they never re-describe Note
> semantics. The `Accepted` status is set only by a human after review; the patch
> does not self-promote it.

## Canonical principle

> **Conversation is private working memory; Note is Project-shared working
> knowledge; Evidence is a typed scientific claim; Decision is committed Project
> truth.**

Equivalently, the authority ladder is explicit and each rung is a deliberate
human action:

```text
Conversation                      private Actor × Project working memory
    ↓ explicit human capture ("Save to Project Note")
Project Note                      Project-shared working document, editable/versioned
    ↓ explicit scientific interpretation (canonical Evidence command)
Evidence                          typed scientific claim
    ↓ explicit Decision commit
Decision                          committed Project knowledge
```

A Note is useful working knowledge. It is **not** an alternate Evidence/Decision
system, and it never becomes Project truth by being written.

```text
Conversation != Project truth
Note         != Evidence
Evidence     != Decision
Decision DRAFT != committed truth
```

## Ownership and scope

```text
ProjectNote
    id                  (opaque UUID)
    project_id          (namespace + lifecycle owner; NOT the object owner model)
    created_by_actor_id (audit metadata, never exclusive ownership)
    title
    created_at / updated_at
    archived_at?        (non-destructive)
        ↓
ProjectNoteRevision     (immutable, INSERT-only)
    revision_id         (opaque UUID)
    note_id             (FK, cascade)
    revision_seq        (per-note ordering; latest = max)
    body                (bounded Markdown/plain structured text)
    created_by_actor_id
    created_at
        ↓
NoteMention             (one typed contextual reference per immutable revision)
    revision_id, ordinal
    target_resource_id? / target_evidence_id? / target_decision_id?
```

- A Note is **Project-scoped**. It is NOT a `GlobalResourceRegistry` entry, a
  ScientificObject, Evidence, a Decision, a provenance node, or Agent memory.
- The latest revision is **derived** from `max(revision_seq)`. There is no mutable
  `current_revision_id`.
- There are no per-note ACLs and no sharing outside the Project.

```text
owner/member   create · append revision · rename · archive
viewer         read only
non-member     no read, no existence oracle
```

## Revision and concurrency semantics

- Revisions are immutable. Editing a Note **appends** a revision; existing bodies
  are never rewritten.
- An append carries the `base_revision_seq` the client edited. If it is no longer
  the server's latest the write fails closed with a typed **409**; another
  member's work is never silently overwritten.
- Note mutation is ordered per note. PostgreSQL takes the `ProjectNote` row lock
  (`SELECT ... FOR UPDATE`) in **both** `append_revision` and `patch_note`
  (rename/archive), so appends and archiving serialize across processes. SQLite
  silently ignores `FOR UPDATE`, so it additionally uses a bounded process-level
  per-note lock (weak-value keyed, 30-second wait, then a typed retryable 409);
  within that one process append/archive are genuinely ordered, and SQLite must
  never be run multi-worker. The `(note_id, revision_seq)` uniqueness constraint
  is the backend-independent backstop for the append sequence itself. PostgreSQL
  is the concurrency truth and the required concurrency regression runs there.

## Content and safety

- The content format is **bounded Markdown/plain structured text**. There is no
  block-editor ontology, no ProseMirror/Tiptap, and no CRDT in this phase.
- Server bounds exist for title length, body length, mentions per revision, note
  page size, and revision page size.
- Note content is **always untrusted Project data**. The workspace renders it
  through a minimal, closed Markdown renderer that never uses
  `dangerouslySetInnerHTML`, so raw HTML/script can never execute; unsupported
  syntax stays visible as literal text.

## Mentions: typed, non-semantic, Project-visible

A `NoteMention` means only:

> this Project working document refers to this existing Project-visible entity.

- It is **not** a `RelationType`. It carries no scientific semantics
  (`supports`, `derived_from`, `selects`, …) and creates **no provenance edge**.
- The smallest physical form mirrors the frozen Evidence endpoint pattern: a
  global-registry target is named by `target_resource_id` (+ its stored registry
  kind); Project-scoped Evidence/Decision targets use their own FKs; a database
  CHECK enforces exactly one target.
- Every mention is validated through the **current Project read lens at write
  time**. Knowing a UUID is not authority to mention a hidden resource; an
  invisible or foreign target fails closed with the same typed 403 as an unknown
  id (no existence oracle).
- Mentions belong to an immutable revision. If a target is later unlinked,
  archived, or revoked, the revision text and mention row are preserved and the
  read projection reports `resolved=false`; history is never rewritten.
- Availability is consistent across target classes: a target is mentionable iff it
  is in the Project read lens (`ProjectResourceLink`) **and** its own lifecycle
  flag is active (a ScientificObjectSeries is `archived_at IS NULL`, a reference is
  `revoked_at IS NULL`; Evidence/Decision additionally require `project_id` match
  and `archived_at IS NULL`). `queries.resource_mention_active` is the shared
  resource-side check, so a newly supplied mention to an archived series or a
  revoked reference is refused exactly like an archived Evidence/Decision, and an
  existing one resolves `resolved=false`.
- The read projection always returns the opaque target UUID (`resource_id` /
  `evidence_id` / `decision_id`) but withholds `label` when `resolved=false`; the
  id is the stable identity of a reference that was already disclosed in this
  Project's immutable revision, and the workspace renders it as an unresolved
  reference.
- Cross-domain composition is an application-layer concern: the Notebook service
  resolves Evidence/Decision targets through the owning domains' public contracts
  (`domain.provenance.evidence_mention_target`,
  `domain.knowledge.decision_mention_target`), never their ORM internals, so the
  Project-domain sub-boundary adds no Project → Evidence/Knowledge edge to the
  Core DAG.
- **Mention semantics on edit** are tri-state and durable (not a UI convention):
  an **omitted** `mentions` inherits the previous revision's mention identities, an
  explicit **`[]`** clears them, and an explicit **non-empty list** replaces them
  after normal current-Project authorization. Inherited mentions are copied without
  re-validation: a reference that was already authorized stays a historical
  contextual reference (`resolved=false` if it later becomes unavailable) rather
  than silently disappearing from the latest revision.
- Command atomicity: every fallible mention validation/authorization runs before
  any durable mutation, so a rejected create/append leaves no flushed Note,
  Revision, or Mention row behind even if the caller later commits on the same
  Session.

## Working knowledge vs scientific truth

- A Note never becomes Evidence or a Decision automatically, and never creates a
  provenance edge.
- Any "Create Evidence from Note" convenience MUST invoke the canonical Evidence
  domain command with explicit interpretation/target fields. No automatic
  promotion.
- A note-like scientific claim is still represented as canonical
  `Evidence(kind=note)`; the `note` Evidence kind is unchanged. Note is **not** a
  source kind in the scientific-graph matrix.

## Conversation → Note boundary

Moving content from a Conversation to a Note is an authority/context transition
and therefore always an **explicit human action** in the workspace (for example
"Save to Project Note" on a visible message). Requirements:

```text
explicit human action           normal ProjectNote authorization
no automatic background promotion
no hidden reasoning / system prompt / raw ToolResult / credentials /
    PendingAction arguments
```

The capture is a plain copy into the new Note body. There is no live
synchronization between Conversation and Note, and no second copy of conversation
authority or history is persisted as Note metadata.

## Agent context integration

- Notes are **not** automatically injected. The ContextBuilder includes only
  explicitly selected Notes via `ContextSelection.note_ids` /
  `note_revision_ids`, under deterministic `max_notes` / `max_note_chars` bounds.
- `note_ids` resolves each note's latest revision; `note_revision_ids` pins an
  exact immutable revision. Selection is authorized through the active Project
  (a Note is not resolved through the global-resource visibility lens).
- Selected Note text is serialized inside the single
  `<untrusted_project_data>` block, alongside all other Project data. It can
  reduce the model's willingness to comply but **cannot** change authority: the
  ToolCatalog, autonomy classes, `explicit_action` gating, and `never_agent`
  exclusion are rebuilt from canonical code and the current Project state.
- Phase 10 gives the Agent **no** Note mutation tool. Agent Note editing remains
  deferred; any future Note tool must be policy-gated and typed, and must never
  bypass the ordinary ProjectNote domain command.

## API surface

```text
GET   /api/projects/{project_id}/notes
POST  /api/projects/{project_id}/notes
GET   /api/projects/{project_id}/notes/{note_id}
PATCH /api/projects/{project_id}/notes/{note_id}                  rename / archive only
POST  /api/projects/{project_id}/notes/{note_id}/revisions        append (base_revision_seq)
GET   /api/projects/{project_id}/notes/{note_id}/revisions
```

The wire contract is the generated OpenAPI document; the frontend consumes only
generated TypeScript (`ADR-0014`). No raw ORM object is exposed, and Note request
models reject unknown fields.

## Workspace surface

A first-class **Notes** Project surface answers: what are we currently
thinking/writing, who changed it, what Project entities does it refer to, and how
did it change over time? The minimum flow is create → edit/append revision →
link Project-visible context → reload → open revision history → add a selected
Note to Agent context. It is not a generic SaaS document editor.

## Explicit non-goals

No RAG, embeddings, vector database, semantic Agent memory, `AgentMemory`,
conversation search, shared conversations, background/recursive Agents, workflow
engine, generic approval workflow, remote-provider Agent execution, automatic
REvoCompute submission, authentication/OIDC, RBAC engine, public sharing,
CRDT/realtime collaborative editing, full ELN, experiment inventory,
equipment/calendar/freezer management, rich block-editor framework, or global
full-text search.

Recorded in **ADR-0016**.
