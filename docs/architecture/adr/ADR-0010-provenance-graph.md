# ADR-0010: Provenance Graph — Distinct Reference Nodes, Evidence as Claim

## Context
The bootstrap puts `provider`+`external_id` directly on `Evidence` with
`evidence_type = RUN | ARTIFACT | LITERATURE`, conflating "the thing" (a reference)
with "the claim about it" (Evidence). The design brief requires provenance that stays
traversable after external systems change, without copying external execution truth.
A review (PR #1) also demanded a single canonical edge direction/endpoint contract and
that the physical Relation schema not be frozen before an implementation fork.

## Decision
- **Distinct durable record types (the canonical node set):** ScientificObject
  (series + revisions), RunReference, SessionReference, ArtifactReference,
  LiteratureReference, ExternalReference, Evidence (interpreted claim), Decision.
  ExperimentalEvidence is a `kind` of Evidence, not a separate table.
- **A reference is a fact; Evidence is an interpreted claim.** Reference nodes are
  immutable identity cards carrying an **identity authority** (not a resolver/provider)
  + authority-native id + checksum/digest + size + version_ref.
- **Evidence = source + target + claim**: a source (a reference/experiment/note being
  interpreted), a target (a ScientificObject, Decision, or another Evidence), and
  interpretation/polarity/scope. **`polarity` is a field on Evidence, not a graph
  edge**; `cited_as` on a Decision `cites` join carries per-citation polarity. This
  removes the prior double-expression of supports/contradicts.
- **Single canonical edge matrix** defined in `SCIENTIFIC_GRAPH.md`: source kind →
  `relation_type` → target kind, cardinality, immutability, with one fixed direction
  (`Run/Session --produced--> Artifact`, `Artifact --imported_as--> ScientificObject`,
  `ScientificObject --generated_by--> Run/Session`, `ScientificObject
  --evaluates--> ScientificObject`, `Decision --selects/cites/supersedes--> ...`).
- **Edges are immutable once written**; corrections add a superseding edge, never
  rewrite.
- **This is a LOGICAL graph contract only.** The physical Relation schema (one
  polymorphic table vs several edge-family tables; uniqueness/supersession key) is
  **deferred to the Phase-1 executable spike** — do not freeze it here.
- **Contradiction coexists** as separate append-only Evidence; a Decision settles it.
- **Checksum proves content integrity, not origin**: origin comes from the provenance
  assertion + provider identity; integrity from the checksum. The two are kept apart.
- **Relational, no graph DB** (reaffirms ADR-0003); graph assembled at the app layer.

## Consequences
- Record immutable identity + content fingerprint; state is refreshable via the
  provider, never copied; checksums verify integrity, provenance assertion proves origin.
- A provider disappearing leaves references intact as "unverifiable," never corrupt.
- Provenance reconstructs (in one canonical direction): why an object exists, where a
  structure came from, which run produced an artifact, which evidence a decision cites.

## Rejected alternatives
- One generic JSON row for everything (loses interpretable provenance).
- Evidence carrying references directly (identity/claim conflation).
- A graph database or event sourcing (premature).
- Freezing the physical Relation table in this decision (deferred to the Phase-1 spike).
