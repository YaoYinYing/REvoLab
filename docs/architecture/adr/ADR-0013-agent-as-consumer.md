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
- **Promotion is the single gate** for turning agent output into project truth
  (ADR-0011).
- **Authority matrix (model only, no infra):**
  - Agent proposal → automatic.
  - Domain mutation → automatic or project-policy.
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
