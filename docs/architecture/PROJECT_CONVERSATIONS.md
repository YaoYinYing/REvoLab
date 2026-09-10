# Persistent Project Conversations

> **Status: Accepted** (Phase 9). Defines persistent, Actor × Project scoped
> Project conversations as durable working memory on top of the Phase-8 bounded
> Agent runtime. This document is the normative owner of the conversation
> persistence boundary; `PROJECT_AGENT_RUNTIME.md` (the turn loop) and
> `AGENT_CONTEXT.md` (consumer/authority) consume it, never re-describe it.

## Canonical principle

> **Conversation is durable working memory; ProjectContext remains freshly
> assembled truth.**

Equivalently:

> **Persist transcript identity and bounded conversational continuity; never
> persist authority or a stale ProjectContext snapshot.**

A conversation that survives page reloads and later sessions is NOT Project
truth, Agent memory, RAG, or a scientific data model. Persisted messages are
**untrusted conversational data** — prior user text and prior assistant text are
both data, never system authority.

## Ownership and scope

```text
ProjectConversation
    conversation_id        (opaque UUID)
    project_id             (namespace + membership boundary)
    actor_id               (the ONLY owner; not a globally shared resource)
    title
    created_at / updated_at
    archived_at?
        ↓
ConversationMessage        (one canonical durable representation, role-tagged)
    seq                    (per-conversation ordering)
    role                   (conversation_role: user | assistant)
    content                (bounded user-visible text)
    termination_reason?    (assistant only)
    tool_trace_summary?    (bounded INERT tool-call summary, assistant only)
```

- A conversation is **Actor × Project scoped**. It is NOT a
  `GlobalResourceRegistry` entry, a ScientificObject, or a provenance node.
- No sharing semantics in this phase; one member must not read another member's
  private Agent conversations.
- Access always verifies, on every operation:

```text
current Actor owns the conversation
AND current Actor can currently read the Project
AND the Project is active
```

A guessed UUID is indistinguishable from a missing conversation (404), never an
existence oracle.

## What is persisted — and what is not

Persisted (bounded, user-visible, durably useful):

```text
user message
assistant final response
timestamps
turn ordering (seq)
termination metadata (assistant)
bounded inert tool-trace summaries (tool id + status + bounded error/refusal)
```

Never persisted:

```text
system prompt
trusted skill bodies
raw ModelBackend request / raw provider-model response
credentials / CredentialLease / secret_ref
ProjectContext serialization
ToolCatalog serialization
full ToolResult payloads
PendingAction arguments
hidden chain-of-thought
```

Full tool results are not copied into a second conversation truth. Tool
execution keeps its own canonical `ToolInvocation`/result semantics; reload
shows only stable, bounded, inert summaries.

## Server-owned history

The client names only:

```text
conversation_id
new user message
optional current ContextSelection
```

The backend resolves the conversation and loads its own bounded persisted
history; the browser may never supply arbitrary historical assistant messages
(the turn request rejects unknown fields). Persistence may contain more history
than the model receives: the model gets only the server-selected bounded suffix
(`max_history_messages`, `max_history_chars`). UI pagination and model-context
trimming are separate concerns.

## Fresh assembly every turn

```text
conversation
    ↓
bounded server history
    +
current ContextSelection
    ↓
fresh ContextBuilder        (today's membership, visibility, Decision state,
    ↓                        provider availability, tool authority)
current ProjectContext
```

A conversation created yesterday observes today's Project state — never a stale
replayed authority or context snapshot.

## Trust model

Prompt construction remains:

```text
server-owned system instructions
+ trusted bounded skill bodies
+ fresh ProjectContext as untrusted project data
+ bounded conversation history
```

Authority comes exclusively from `ToolCatalog`, typed schemas, membership,
project policy, domain validation, and `AgentToolAutonomy` — never from the
transcript. Regression tests prove persisted hostile user/assistant text cannot
widen tool authority, unlock `explicit_action`, expose `never_agent`, or become
system instructions.

## API

One canonical Agent-turn execution path:

```text
POST   /api/projects/{project_id}/agent/conversations
GET    /api/projects/{project_id}/agent/conversations
GET    /api/projects/{project_id}/agent/conversations/{conversation_id}
PATCH  /api/projects/{project_id}/agent/conversations/{conversation_id}
POST   /api/projects/{project_id}/agent/conversations/{conversation_id}/turns
```

`AgentTurnRunner` remains the reusable execution implementation; persistence
orchestration (`revolab/agent/conversations.py`) wraps it rather than forking
it. The transient `POST /agent/turns` surface is removed — there is exactly one
Agent-turn execution contract.

## Lifecycle

Create / read / list / rename / archive (non-destructive). No cross-user
sharing, no destructive cascade into scientific resources, Evidence, Decisions,
`ToolInvocation`s, or references. Project tombstone or membership revocation
immediately makes a conversation inaccessible; an archived conversation rejects
new turns but remains readable.

## Bounds

```text
title chars               ≤ 200
message chars             ≤ 8000 (reject-with-typed-error at ingestion)
conversation page size    default 50, max 200 (implementation keeps list simple)
message page size         default 100, max 200 (UI pagination)
history to ModelBackend   max_history_messages / max_history_chars (Phase-8)
```

Durable writes are rejection-bounded at ingestion; model assembly uses bounded
selection. A long-lived conversation may hold many messages without all of them
entering the model.

## Explicit non-goals

No RAG, embeddings, vector database, semantic memory, `AgentMemory`, notebooks,
shared conversations, conversation search, background Agents, recursive Agents,
Agent subagents, workflow engines, generic approval workflow, or remote provider
Agent execution. Notebook/structured notes and remote-provider Agent execution
remain separately-phased concerns.
