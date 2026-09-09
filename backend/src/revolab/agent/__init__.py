"""Agent Context domain (Phase 6) — the Agent is a consumer, never an owner.

This package owns the read-only `ContextBuilder` (ContextSelection -> ProjectContext),
the typed `ToolCatalog` projection, the minimal `SkillCatalog` that points at
`.agents/skills/`, and an ephemeral `AgentSession` value object. It consumes the
Core domains' public contracts (Project/Evidence/Knowledge/Provider/Identity) and
never writes project truth itself: every mutation is routed back through the same
typed domain/application services ordinary API callers use.
"""

from revolab.agent.builder import ContextBuilder, build_context
from revolab.agent.inspect import inspect_artifact
from revolab.agent.session import AgentProposal, AgentSession, propose_selection, record_proposal
from revolab.agent.skills import SkillCatalog, select_skills
from revolab.agent.tools import ToolCatalog, build_tool_catalog

__all__ = [
    "AgentProposal",
    "AgentSession",
    "ContextBuilder",
    "SkillCatalog",
    "ToolCatalog",
    "build_context",
    "build_tool_catalog",
    "inspect_artifact",
    "propose_selection",
    "record_proposal",
    "select_skills",
]
