---
name: architecture-review
version: 0.1.0
description: Run an independent architecture review of a proposed design or patch against the canonical architecture documents and ADRs.
---

# Architecture Review

## When to use
Use for the "independent review" step of the seven-phase workflow when a design or
architecture PR (like PR #1) needs an adversarial cross-document consistency audit,
or when asked to review an architecture change against the established invariants.

## Canonical sources
Read `CLAUDE.md` (short invariants), `docs/agents/HARNESS_OPERATING_MODEL.md`
(control plane), the architecture documents and ADRs under `docs/architecture/`, and
`IMPLEMENTATION_STATE.md` for current machine state. The single source of graph truth
is `docs/architecture/SCIENTIFIC_GRAPH.md`; `docs/architecture/DOMAIN_BOUNDARIES.md`
and `docs/architecture/SYSTEM_ARCHITECTURE.md` share the one dependency DAG.

## Procedure
1. **Orient** — read the current proposal/PR, the invariants, and the authority docs.
2. **Check for single-truth violations** — flag any place where one concept has two
   canonical answers across documents (a "which document is true?" situation). This is
   the #1 architecture-constitution failure mode.
3. **Check ownership** — every concept has exactly one owning domain; every edge in the
   dependency DAG consumes a public contract, never a sibling's internals; no circular
   ownership.
4. **Check security/separation** — global identity is not global readability; deletion
   is referentially valid (tombstones over hard-delete where FKs demand); provider
   vocabulary never leaks into Core.
5. **Report** — a concise findings list, each tagged P0/P1/P2, with the document and
   the concrete fix. Do not vote on architecture; report and let the integrator decide.

## Rules
- Flag cross-document contradictions (different wire values, conflicting statuses,
  competing DAGs) explicitly — never pick a winner silently.
- Execution/authority language must stay honnêtement "Proposed / pending human
  review" unless a human has accepted the design.
