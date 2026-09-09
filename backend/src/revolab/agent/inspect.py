"""Backward-compatible re-export of the bounded artifact inspection boundary.

The implementation moved to `revolab.tools.artifact_inspection` in Phase 7 (the
Project Tool Harness owns the inspection boundary); this module keeps existing
`revolab.agent.inspect` imports working.
"""

from revolab.tools.artifact_inspection import inspect_artifact

__all__ = ["inspect_artifact"]
