---
name: project-tool-harness
version: 0.1.0
description: Add or invoke a first-class Project Tool through the closed local runtime and shared ToolCatalog.
---

# Project Tool Harness

Tool is a first-class Project Harness abstraction, not a provider abstraction. Before adding a Tool, MUST inspect the canonical schemas in `backend/src/revolab/schemas.py`, the enum vocabulary in `backend/src/revolab/enums.py`, and `docs/architecture/PROJECT_TOOL_HARNESS.md`; never copy a schema or enum into this skill.

Rules:

- Register local tools only in `revolab/tools/registry.py` (`ALL_LOCAL_TOOLS`); every tool carries a canonical Pydantic input model, a typed output model, an autonomy class, an execution class (`local`), and a side-effect class. Duplicate tool ids are refused.
- Local execution is closed and bounded: no eval/exec, shell, arbitrary filesystem paths, raw SQL, or arbitrary HTTP. Add explicit per-tool bounds and fail closed for unsupported inputs.
- Keep remote REvoCompute execution a separate runtime: project it through the catalog as `execution_class=remote`; never duplicate its task/run/artifact model in the Tool layer.
- Persistence semantics are the tool's declared side-effect class; a derived result persists only with `persist` (owner/member) and records a `ToolInvocation`, while Evidence/Decision truth routes through the existing typed domain operations and is never produced automatically from tool output.
- Humans and the Agent consume the same `ToolCatalog`; do not add a second tool-definition surface or a manually synchronized frontend enum.
