# Agent Conventions

Procedural agent guidance belongs under `docs/agents/`; architectural invariants belong in `CLAUDE.md`.

A project skill should state its purpose, trigger conditions, canonical sources, workflow, validation checks, and boundaries. It must identify the typed API/tool contract it uses and must not redeclare backend enums or authorization rules. Unimplemented skills are documented as planned rather than presented as available.

When a schema or API changes, regenerate derived skill material and run the contract freshness check. Keep examples small and provenance explicit.
