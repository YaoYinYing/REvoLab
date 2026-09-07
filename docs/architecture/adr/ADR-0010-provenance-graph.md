# ADR-0010: Provenance Graph — Distinct Reference Nodes, Evidence as Claim

## Context
The bootstrap puts `provider`+`external_id` directly on `Evidence` with
`evidence_type = RUN | ARTIFACT | LITERATURE`, conflating "the thing" (a reference)
with "the claim about it" (Evidence). The design brief requires provenance that stays
traversable after external systems change, without copying external execution truth.

## Decision
- **Distinct durable record types (the canonical node set):** ScientificObject,
  RunReference, SessionReference, ArtifactReference, LiteratureReference,
  ExternalReference, Evidence (interpreted claim), Decision.
  ExperimentalEvidence is a `kind` of Evidence, not a separate table.
- **A reference is a fact; Evidence is an interpreted claim.** Reference nodes are
  immutable identity cards (provider + provider-native id + checksum/digest + size +
  version_ref). Evidence points at a reference and adds kind, role, interpretation,
  polarity, confidence, scope, as_of.
- **Edges (typed, directed, optional rationale):** the single canonical `RelationType`
  closed enum defined in `SCIENTIFIC_GRAPH.md` (Edges section) — provenance edges
  `consumed_as_input_by`, `produced` (single owning edge), `imported_as`,
  `derived_from`, `supersedes`, plus `variant_of`, `represents`, `generated_by`,
  `evaluates`, `selects`, `supports`/`contradicts` (object or evidence target),
  and `cites` (with per-citation `cited_as` polarity).
- **The generic Relation supports a polymorphic target** (nullable node-type
  discriminator + per-kind FK columns) so `supports/contradicts → Evidence` and
  `supersedes → Decision` are representable without weakening referential integrity
  (see `SCIENTIFIC_GRAPH.md`).
- **Edges are immutable once written**; corrections add new edges or supersede, never
  rewrite.
- **Contradiction coexists** as separate append-only Evidence; a Decision settles it.
- **Relational, no graph DB** (reaffirms ADR-0003); graph assembled at the app layer.

## Consequences
- Prove origin by immutable identity + content fingerprint; state is refreshable via
  the provider, never copied.
- A provider disappearing leaves references intact as "unverifiable," never corrupt.
- Provenance reconstructs: why an object exists, where a structure came from, which
  run created an artifact, which evidence caused a decision.

## Rejected alternatives
- One generic JSON row for everything (loses interpretable provenance).
- Evidence carrying references directly (identity/claim conflation).
- A graph database or event sourcing (premature).
