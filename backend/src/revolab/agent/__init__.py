"""Agent Context & Runtime (Phase 6 + Phase 8) — the Agent is a consumer, never
an owner.

This package owns:
- the read-only `ContextBuilder` (ContextSelection -> bounded ProjectContext),
- the minimal `SkillCatalog` pointing at `.agents/skills/` + bounded skill bodies,
- the `ModelBackend` boundary (one configured OpenAI-compatible adapter, or a
  deterministic test double at the external boundary),
- the bounded `AgentTurnRunner` loop (context -> model -> canonical ToolCatalog ->
  LocalToolRuntime OR PendingAction -> result).

It consumes Core's public contracts and the Project Tool Harness (`revolab.tools`)
and never writes project truth itself: every mutation routes through the same
typed domain/application services ordinary API callers use.
"""

from revolab.agent.builder import ContextBuilder, build_context
from revolab.agent.inspect import inspect_artifact
from revolab.agent.model_backend import (
    ChatMessage,
    ModelBackend,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    OpenAICompatModelBackend,
    ToolSpec,
)
from revolab.agent.runtime import AgentLoopBounds, AgentTurnRunner
from revolab.agent.skills import SkillCatalog, load_skill_bodies, select_skills
from revolab.agent.tools import ToolCatalog, build_tool_catalog

__all__ = [
    "AgentLoopBounds",
    "AgentTurnRunner",
    "ChatMessage",
    "ContextBuilder",
    "ModelBackend",
    "ModelRequest",
    "ModelResponse",
    "ModelToolCall",
    "OpenAICompatModelBackend",
    "SkillCatalog",
    "ToolCatalog",
    "ToolSpec",
    "build_context",
    "build_tool_catalog",
    "inspect_artifact",
    "load_skill_bodies",
    "select_skills",
]
