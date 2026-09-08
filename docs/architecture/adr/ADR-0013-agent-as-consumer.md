# ADR-0013: Agent As Consumer + Authority Model

## Context
The Agent must read and propose against project truth without owning it, and chat must
never become project truth. Future agent actions need an authority boundary, but no
approval infrastructure should be built now.

## Decision
- **The Agent is a consumer**: read context → reason → propose → typed tool call →
  domain validation → persisted truth. The Agent can never emit a raw write.
- **Context abstractions:** `ProjectContext` (immutable per-turn value), `
  ContextSelection`, `ContextBuilder` (read-only assembler), `AgentSession`
  (ephemeral), thin `ToolCatalog`/`SkillCatalog`. Rejected: RAG, DB-backed agent
  memory, always-on skill encyclopedia.
- **Two distinct gates (reviewer finding #10):**
  - **Typed domain-operation gate — all persistence.** An Agent can never raw-write;
    every durable change is a typed domain command through domain validation.
  - **Promotion gate — only committing a knowledge assertion.** Promotion is
    specifically the `Decision draft → committed` transition (ADR-0011); it does NOT
    apply to ordinary object/evidence creation, which are typed domain operations.
- **Authority matrix (model only, no infra):**
  - Agent proposal → automatic.
  - Domain mutation (create object/evidence, record a Decision **as a draft**) →
    automatic or project-policy.
  - Knowledge commitment (Decision draft → committed) → approval (authorized actor) —
    the promotion gate.
  - External compute submission → explicit tool/policy, fails closed.
  - External data write → highly restricted.
  - Project sharing change → human approval.
  - Credential operation → never exposed to agent.

## Consequences
- Chat history never appears in the graph by default.
- Production/agent privileges fail closed on uncertainty.
- Skills teach judgment and procedure; typed APIs own executable truth (ADR-0007).

## Rejected alternatives
- Agent writing the DB directly (chat becomes truth).
- Building approval/RBAC infrastructure now (premature).
