# Agent Conventions

Procedural agent guidance belongs under `docs/agents/`; architectural invariants belong in `CLAUDE.md`.

The control plane for how the harness develops REvoLab (loops, Primary Integrator, skills, safety, the ODDRIVC workflow) is defined in `docs/agents/HARNESS_OPERATING_MODEL.md`; `CLAUDE.md` carries only the short invariants derived from it. Substantial tasks follow that model: orient, decompose, delegate bounded investigation to fresh subagents, reconcile under the Primary agent, implement one coherent patch, verify with executable gates, and update `IMPLEMENTATION_STATE.md`.

A project skill should state its purpose, trigger conditions, canonical sources, workflow, validation checks, and boundaries. It must identify the typed API/tool contract it uses and must not redeclare backend enums or authorization rules. Unimplemented skills are documented as planned rather than presented as available.

When a schema or API changes, regenerate derived skill material and run the contract freshness check. Keep examples small and provenance explicit.
