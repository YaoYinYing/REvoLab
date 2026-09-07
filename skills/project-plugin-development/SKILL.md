---
name: project-plugin-development
version: 0.1.0
description: Extend REvoLab through a provider-neutral driver rather than a core branch.
---

# Project Plugin Development

Use a Python entry point only when the capability is independently deployable and its contract is stable. Keep registration, lifecycle, and failure behavior explicit. Do not introduce hot reload, Cordis, or a custom reactive plugin runtime for ordinary providers.
