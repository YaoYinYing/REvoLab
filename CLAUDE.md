# REvoLab Engineering Memory

REvoLab is the independent scientific context layer of the REvo ecosystem. Core owns scientific relationships and project context; drivers own external capabilities. REvoCompute owns execution and REvoDesign owns interactive design.

## Invariants

- Choose the simplest durable implementation that satisfies a current requirement.
- Grow from small working end-to-end slices; remove obsolete paths instead of adding compatibility layers.
- Keep domain, persistence, API, frontend, drivers, and agent knowledge clearly separated.
- The backend schema/API is the canonical source of domain types and configuration. Frontend contracts and skills derive from it.
- Projects are hierarchical trees of typed scientific objects. Relations, evidence, and decisions add cross-tree context without replacing the hierarchy.
- External run and artifact identities are namespaced by provider. REvoLab stores references, not mutable execution truth or storage paths as identity.
- Prefer established libraries and explicit protocols. Do not add graph databases, event streaming, workflow engines, Cordis, or a custom framework without demonstrated need.
- Skills describe how agents use typed tools; they do not implement business logic or authorization.

Procedural instructions belong in `docs/agents/`. Keep this file concise and prune stale guidance.
