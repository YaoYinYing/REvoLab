# ADR-0005: Cordis-Inspired Lifecycle Without Cordis

## Context
Cordis demonstrates useful reversible effects and capability lifecycle ideas, but REvoLab is Python-first and has no hot-reload requirement.

## Decision
Use a small explicit capability and lifecycle model without a Cordis runtime dependency.

## Consequences
The initial system has low operational complexity and explicit disposal. Revisit only if dynamic rewiring, hot unloading, or reactive dependency satisfaction becomes a demonstrated requirement.

## Rejected alternatives
Cordis runtime, HMR, and a general reactive dependency graph in the initial product.
