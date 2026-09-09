"""Project Tool Harness — first-class Tool abstraction (Phase 7).

The Project Tool Harness turns Tool into the explicit execution surface of a
Project: a canonical, typed descriptor consumed by both the human workspace and
the Agent, a closed Local Tool Runtime for bounded lightweight analysis, and a
single ToolCatalog that projects the existing REvoCompute (remote) capability
path alongside local tools without duplicating it.

Dependency direction (frozen in TODO.md section 4):

    Project Harness -> Tool -> implementation adapter -> local service OR capability/driver

This package depends on the application/domain services and Provider/Capability
layer; the Agent (`revolab.agent`) and the Presentation API consume it. It never
imports `revolab.agent`, so the Agent remains a consumer of a lower layer.
"""

from revolab.tools.artifact_inspection import inspect_artifact
from revolab.tools.catalog import ToolCatalog, build_tool_catalog
from revolab.tools.registry import LocalToolRegistry, LocalToolSpec, build_default_registry
from revolab.tools.runtime import LocalToolRuntime
from revolab.tools.types import InvocationContext

__all__ = [
    "InvocationContext",
    "LocalToolRegistry",
    "LocalToolRuntime",
    "LocalToolSpec",
    "ToolCatalog",
    "build_default_registry",
    "build_tool_catalog",
    "inspect_artifact",
]
