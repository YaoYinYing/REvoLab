# Project Agent Runtime

> **Status: Accepted** (Phase 8). Defines the bounded Project Agent runtime: the
> model boundary, the ephemeral turn loop, prompt/trust separation, and the
> executable authority path that keeps the Agent a consumer — never an owner —
> of Project truth.

## Canonical principle

> **The model reasons; ProjectContext supplies truth; ToolCatalog defines
> capability; domain validation grants authority; the human controls
> commitment.**

The Agent runtime turns the deterministic Phase-6 proposal substrate into a real,
finite conversational loop. It adds exactly one external-model abstraction and
one typed Agent-turn endpoint; it does not add a second Tool truth, a second
Agent-tool schema, a chat/vector store, or any developer-agent authority model
(DeepSeek Harness is the development control plane, never the product runtime).

## Execution path

```text
POST /api/projects/{project_id}/agent/conversations/{conversation_id}/turns
    ↓
ContextBuilder                 (fresh bounded ProjectContext, rebuilt every turn)
    ↓
SkillCatalog                   (bounded bodies of explicitly selected skills)
    ↓
ToolCatalog                    (the ONE canonical catalog, shared with the human)
    ↓
prompt assembly                (trusted instructions <> untrusted project data)
    ↓
ModelBackend                   (one configured OpenAI-compatible adapter, or a
                                deterministic fake at the external boundary)
    ↓
tool-call validation           (tool id · availability · input schema ·
                                 membership · autonomy · execution class ·
                                 side-effect class)
    ↓
LocalToolRuntime  OR  PendingAction  (explicit_action)  OR  fail-closed
    ↓
bounded ToolResult back to the model
    ↓
final conversational response
```

Every turn is stateless with respect to truth: context and catalog are rebuilt
from canonical Project truth on every call. Conversation history is
server-owned and strictly bounded (`user`/`assistant` only, finite count and
bytes); Phase 9 persists exactly that history as durable working memory
(`docs/architecture/PROJECT_CONVERSATIONS.md`), never as Project truth, system
authority, a stale ProjectContext snapshot, or client-supplied system
instructions.

## Model boundary

`revolab/agent/model_backend.py` defines `ModelBackend`, `ModelRequest`,
`ModelResponse`, `ModelToolCall`, and `ToolSpec` value objects plus a single
`OpenAICompatModelBackend` over the already-approved `httpx` dependency.

- The model backend is **Agent runtime infrastructure**, not a scientific
  Provider/Capability. No LLM enters Core's domain models or the ToolCatalog.
- One configured concrete adapter per process — no generalized provider framework.
- A missing model configuration fails explicitly (`503 ModelUnavailableError`);
  there is no silent fallback to a fake production Agent.
- The deterministic `ScriptedModelBackend` exists **only** at the external model
  boundary (`revolab/testing/fake_model.py`) for tests and the browser slice; it
  drives the REAL ContextBuilder, Skill loading, ToolCatalog, parser,
  LocalToolRuntime, authority logic, loop, API, and frontend.

Server-owned configuration (`revolab/config.py`): `model_endpoint`, `model_name`,
optional `model_api_key` (`SecretStr`, never in OpenAPI/logs/repr/context), model
timeout, and the Agent-loop ceilings. Secrets are never echoed.

## Bounded loop

`AgentTurnRunner` implements the finite state machine:

```text
PREPARE_CONTEXT → MODEL → [FINAL_RESPONSE → DONE]
                       ↘ TOOL_REQUEST → VALIDATE → EXECUTE → MODEL
```

Conservative concrete ceilings (all regression-tested):

```text
max_model_turns                   8
max_tool_calls                   16
max_tool_calls_per_turn           4
max_context_chars            60 000
max_history_messages             20
max_history_chars            20 000
max_skill_count                   4
max_skill_bytes              20 000
max_tool_result_chars        12 000
total_turn_duration_seconds     300
```

The model request timeout is enforced by the adapter's `httpx` timeout. A bound
hit produces a typed, user-visible terminal result (`AgentTerminationReason`),
never a silent continuation. No recursive Agent, no background execution, no
silent retry loop.

## Model tool calls are untrusted input

For every model-emitted tool call the runtime validates:

```text
tool id           exact lookup in the canonical ToolCatalog
availability      callable for THIS Actor in THIS Project
input             canonical Pydantic schema (object args only)
membership        readable_membership / mutation_capable_membership
autonomy          automatic | policy | explicit_action
execution class   local (LocalToolRuntime) | remote (never the local runtime)
side-effect class read_only | creates_derived_result | domain_mutation | external_action
```

Unknown tools fail closed; malformed or non-object arguments fail closed;
invented resource ids are re-authorized at execution time. Persistence inside
the loop is always the typed `LocalToolRuntime` path with `persist=False` — the
model cannot bypass it and cannot construct arbitrary HTTP/SQL/filesystem/shell
operations.

## Authority (`AgentToolAutonomy`)

One canonical vocabulary, three executed classes:

```text
automatic        safe bounded reads/analysis        → execute in the loop
policy           typed domain mutations (evidence,   → execute only if the
                 Decision draft)                      project policy authorizes
explicit_action  Decision commit, compute submit     → NEVER execute
                                                       → PendingAction
never_agent       membership / sharing / credential  → never projected as a Tool
```

`decision.record_draft` is the only Decision shape the loop can produce, and it is
always a **DRAFT**; commit remains the separate promotion gate
(`POST /api/projects/{project_id}/decisions/{decision_id}/commit`). Remote
`explicit_action` (e.g. `{provider}.compute.submit`) becomes a `PendingAction`;
remote automatic/policy tools are surfaced in the catalog but the Phase-8 loop
does **not** autonomously cross the external boundary to execute them — they
remain on the existing human capability endpoints (documented deferral).

## Pending explicit actions

`PendingActionRead` is an ephemeral, validated, non-secret description of a
proposed-but-NOT-executed operation. Phase 8 has no approval database or
workflow engine; the existing human UI/typed endpoint remains the execution
authority.

## Prompt-injection boundary

The executable authority boundary is the actual defense; prompt assembly is
structural defense-in-depth:

- Trusted instructions (server-owned system prompt + bounded skill bodies) live
  in the **system** role.
- All Project content (labels, Evidence, Decisions, artifact/table text, tool
  results) is serialized once into a single `<untrusted_project_data>` user
  message and told it is data that cannot redefine authority or policy.
- No credential, `secret_ref`, lease, server environment, or hidden system
  instruction is ever rendered because project content asks for it.

Regressions prove hostile project text cannot unlock `never_agent` tools, convert
`explicit_action` to `automatic`, invoke an unknown tool, expand Project
visibility, or cause arbitrary filesystem/HTTP/shell execution (TODO.md §11).

## API / frontend

`POST /api/projects/{project_id}/agent/conversations/{conversation_id}/turns`
is the one typed Agent-turn execution surface; persistence orchestration
(`revolab/agent/conversations.py`) wraps `AgentTurnRunner` and resolves the
server-owned history. The transient `/agent/turns` path is superseded and
removed. The full conversation surface (create / list / read / patch / turn) is
owned by `docs/architecture/PROJECT_CONVERSATIONS.md`:

```text
request    ConversationTurnCreate { message, selection? }
response   ConversationTurnRead    { turn: AgentTurnRead, user_message,
                                    assistant_message? }
```

`AgentTurnRead` (final response, tool trace, pending actions, termination
reason, budget) never exposes the raw provider/model response object, hidden
prompt text, token/API credentials, or any secret. The frontend Agent workspace
shows the persisted conversation list/transcript, the tools used during the
last turn, pending explicit actions, and the Decision DRAFT vs COMMITTED truth
boundary.

## Deferrals (explicit)

- Notebooks/structured working notes, RAG/vector/semantic memory, workflow
  engines, background jobs, recursive Agents, Agent subagents, conversation
  sharing, conversation search, generic approval workflows.
- Authentication/OIDC (the endpoint uses the existing `X-Actor-Id` seam).
- Live-model acceptance in CI (CI uses the deterministic fake; a live smoke test
  would be opt-in and is not part of normal gates).
- Remote provider tool execution inside the Agent loop (external-boundary reads
  remain on the human capability endpoints).
