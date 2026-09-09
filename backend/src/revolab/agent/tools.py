"""Backward-compatible re-export of the Project Tool Harness catalog.

Phase 7 moved the canonical ToolCatalog into `revolab.tools` (the Project Tool
Harness domain), so the Agent and the human workspace consume the same catalog.
This module exists only so existing `revolab.agent.tools` imports keep resolving
to the same names; it contains no catalog logic.
"""

from revolab.tools import ToolCatalog, build_tool_catalog

__all__ = ["ToolCatalog", "build_tool_catalog"]
