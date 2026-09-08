# Scientific Graph

> **Status: Proposed — pending human architecture review.** Defines the **logical**
> graph contract: node categories, the single canonical edge endpoint matrix, whether
> edges are immutable, and how semantic mistakes are prevented. Deliberately
> relational (ADR-0003) — **no graph DB**, no RDF. The *physical* persistence shape of
> relations (one polymorphic table vs several edge families; uniqueness/supersession
> key) is **deferred to the Phase-1 executable spike** (see "Physical persistence is
> deferred").

REvoLab is, at its heart, one logical directed graph over durable node categories,
assembled at the application layer over relational tables.

## Node categories

| Node | Kind | Global or project-scoped |
|---|---|---|
| Project | container/scope | project-local |
| ScientificObject (series + revisions) | scientific entity | **global** |
| RunReference | external execution identity card | **global** |
| SessionReference | external interactive-session identity card | **global** |
| ArtifactReference | external output identity card | **global** |
| LiteratureReference | external publication citation | **global** |
| ExternalReference | resolver/cache metadata over an `ExternalIdentity` (`external_identity_id` FK) | **global** |
| Evidence | interpreted claim (source + target + polarity) | project-scoped |
| Decision | committed conclusion | project-scoped |

References, Evidence, and Decision **are** graph nodes, not relation payloads — this
keeps provenance reconstructable. Global nodes are never project-owned (see
`COLLABORATION_IDENTITY.md`); Evidence and Decision are authored in a single Project.

---

## THE single canonical edge matrix

This matrix is the **only** authority for edge direction, cardinality, and
immutability. Every diagram, provenance query, API, and Agent tool schema derives from
it. `RelationType` is a Core-owned closed enum generated into the contracts; a
direction is fixed here and must not be inverted elsewhere.

**Convention:** the arrow reads naturally — the *source* is the thing that does
`relation_type` to the *target*. A provenance query is always stated in this same
direction (no silent reverse).

**Endpoint semantics are frozen (reviewer round 4):** the matrix uses the Series vs
Revision distinction from `SCIENTIFIC_OBJECT_MODEL.md` instead of the vague
`ScientificObject`. **Conceptual semantic edges** (relationships *between things*,
true regardless of version) address `ScientificObjectSeries`;
**content/provenance edges** (about *immutable content versions*) address
`ScientificObjectRevision`; a **Decision** targets a Series or a Revision **explicitly
typed** (the link carries the kind, never an ambiguous "object" pointer).

| # | source kind | relation_type | target kind | cardinality | immutable | scope |
|---|---|---|---|---|---|---|
| 1 | ScientificObjectSeries | `variant_of` | ScientificObjectSeries | 0..* | yes | **global** |
| 2 | ScientificObjectRevision | `derived_from` | ScientificObjectRevision | 0..* | yes | **global** |
| 3 | ScientificObjectSeries | `represents` | ScientificObjectSeries | 0..* | yes | **global** |
| 4 | ScientificObjectRevision | `evaluates` | ScientificObjectRevision | 0..* (an assay revision evaluates a variant revision) | yes | **global** |
| 5 | ScientificObjectRevision \| ArtifactReference | `consumed_as_input_by` | RunReference \| SessionReference | 0..* | yes | **global** |
| 6 | RunReference \| SessionReference | `produced` | ArtifactReference | 0..* (each 1:1 owning) | yes | **global** |
| 7 | ArtifactReference \| ExternalReference | `imported_as` | ScientificObjectRevision | 0..* | yes | **global** |
| 8 | Decision | `selects` | ScientificObjectSeries \| ScientificObjectRevision | 0..* | yes | **project-scoped** |
| 9 | Decision | `supersedes` | Decision | 0..1 | yes | **project-scoped** |
| 10 | Decision | `cites` | Evidence | 0..* (join, `cited_as`) | yes | **project-scoped** |

**Worked examples (reviewer round 4):** `VariantSeries --variant_of--> ProteinSeries`
(conceptual — every new variant revision does not re-assert the same biological
relation); `StructureRevision --derived_from--> StructureRevision` and
`StructureRevision --consumed_as_input_by--> RunReference` (content/provenance —
provenance addresses the exact immutable revision, so reproducibility is retained);
`Decision --selects--> VariantSeries` (target kind explicit). If `variant_of` were
bound to revisions, every new revision would duplicate the concept-level relation; if
compute provenance were bound to series, reproducibility would be lost. The matrix
above fixes both.

**Scope column (reviewer round 3, renumbered round 5):** edges **#1–7** are **global
provenance edges** — both endpoints are global resources (ScientificObject
series/revision, Run/Session/Artifact/Reference), so they live in the durable global
provenance graph as `GlobalProvenanceEdge` rows and are never archived by a Project
tombstone. Edges **#8–10** are **project knowledge edges** — their endpoints are the
project-scoped Decision/Evidence nodes, so on Project tombstone they archive **with**
the Decision/Evidence (soft-archive) as `ProjectKnowledgeEdge` rows, never kept as
dangling global edges.

**Ownership + lifecycle are frozen (reviewer round 4, renumbered round 5), only the
physical shape is deferred:**
- `GlobalProvenanceEdge` (#1–7) is owned by the **Evidence / Provenance domain**, has no
  `project_id`, and is **never** archived or deleted by a Project.
- `ProjectKnowledgeEdge` (#8–10: `selects`/`supersedes`/`cites`) is owned by the
  **Knowledge / Decision domain**, carries `project_id`, and is archived **with** its
  Decision/Evidence on Project tombstone.
- Whether the two share one physical table or are separate edge families is left to the
  Phase-1 spike; **ownership and lifecycle are not.**

**Write authority (round 5):** global provenance edges (#1–7) are created **only** by
typed authoritative domain operations — `produced` by run/artifact import,
`imported_as` by the import command, `consumed_as_input_by` by task submission,
`variant_of`/`represents`/`derived_from`/`evaluates` by their object-domain commands.
There is **no generic `POST /relations` writer for global edges**; the mutator must hold
the injected authority (see `ResourceStewardship` in `COLLABORATION_IDENTITY.md`).
Project knowledge edges (#8–10) are created only through Decision domain commands with
the project-context write-time check.

**Direction decisions (resolving prior ambiguity):**
- **`produced`**: `Run/Session → produced → Artifact` (a run/session produces an
  artifact). Query phrasing is always "which run produced this artifact?" — in this
  direction.
- **`consumed_as_input_by`**: `ScientificObjectRevision | ArtifactReference →
  consumed_as_input_by → Run/Session`. An **ArtifactReference may be consumed directly**
  by a run without first being imported into a ScientificObject (REvoCompute tasks
  frequently feed one task's artifact into the next); an imported entity is cited as a
  `ScientificObjectRevision`. The source kind is stored on the edge.
- **`evaluates`**: always `ScientificObjectRevision --evaluates--> ScientificObjectRevision`
  (e.g. an Assay revision evaluates a Variant revision). Do not write "Run evaluates
  Variant"; the run's role is expressed by the Assay revision that references the run.
- **`variant_of`** and **`represents`**: always Series → Series (conceptual identity,
  not content version).
- **`imported_as`**: `ArtifactReference | ExternalReference → imported_as →
  ScientificObjectRevision` — an external output artifact or an external lookup
  identity is imported as a specific immutable REvoLab revision (source kind stored on
  the edge).
- **`generated_by` is DERIVED, not persisted (round 5).** The reverse view "which run
  generated this imported revision" is the composite traversal — it is not a stored
  `RelationType` and cannot drift out of sync with the two stored edges:

  ```text
  generated_by  =  Revision ←imported_as← Artifact ←produced← Run/Session
  ```

  UI and API may return it as a derived aggregate field, but it is **never** persisted
  (same single-truth rule as `input_objects`/`output_artifacts`/`originating_run`).
- **`selects`**: `Decision --selects--> ScientificObjectSeries | ScientificObjectRevision`,
  with the target kind stored explicitly on the edge.

**Evidence polarity is NOT an edge.** `supports` / `contradicts` are **fields** of the
Evidence claim (its `polarity`) plus the `cited_as` on a Decision `cites` join —
they are not separate graph edges. See "Evidence as source + target + claim" below.

---

## Evidence as source + target + claim (single polarity truth)

Evidence is the interpreted claim that links **what was observed** to **what it says
about**. It is modelled as three parts, with polarity stated **once**:

```text
RunReference | SessionReference | ArtifactReference | LiteratureReference
| ExternalReference | ScientificObjectRevision | direct human observation / note
   (source: the reference/observation being interpreted)      ↑
                                                               |
       Evidence { kind, role, interpretation, polarity(supports|contradicts|neutral),
                  confidence, scope, as_of, target }
                                                               ↓
   ScientificObjectRevision | Decision | another Evidence
   (target: what the claim is about)
```

- **`polarity` is a field** on Evidence — there is no separate
  `Evidence --supports/contradicts-->` edge in the matrix. This removes the
  double-expression the review flagged.
- **Decision → Evidence** is expressed by the `cites` join edge (#10) with a
  per-citation `cited_as` ∈ {`supports`, `contradicts`, `context`} (distinct from the
  Evidence `polarity` field, whose values are `supports | contradicts | neutral`),
  because a Decision may cite evidence that *contradicts* it.
- **Evidence → Decision**: if an evidence claim is specifically about a Decision
  (supporting or undercutting it), that is a `cites`/`cited_as` relation from the
  Decision's side plus the Evidence `target` pointing at the Decision. The polarity
  lives in one place (`cited_as` / Evidence `polarity`), never two.

### Evidence association contract (frozen with the matrix)

Evidence is a graph node, so its two load-bearing associations must be frozen too.
They are **not** generic `RelationType` edges (they are the Evidence node's own
identity); they form a separate, co-authoritative contract (a different layer from the
provenance edge matrix, which physically is an edge-family deferred to the spike — but
the **legal source/target kinds, cardinality, and immutability are fixed in PR1**):

```text
Evidence.source  (0..1, per-kind required) — what was interpreted
    RunReference | SessionReference | ArtifactReference
    | LiteratureReference | ExternalReference
    | ScientificObjectRevision
    — REQUIRED (non-null) for any computation / literature / imported source. NULL
      (omitted) only for a direct human observation / note-like first-hand claim with
      no machine source; such an evidence claim still carries `kind=observation`.
      A project-side `note` that is the claim's provenance maps to a `note`-flavoured
      Evidence (see "experiment and note" below); the *evidence* is the graph node,
      not the note.

Evidence.target  (exactly one, required) — what the claim is about
    ScientificObjectRevision | Decision | Evidence
```

- **Cardinality:** exactly one `target` per Evidence (required). `source` is **0..1**:
  required for computation/literature/imported provenance, and is the sole legal **null**
  case — a direct human observation with no machine-addressable source. A many-sided
  claim is expressed as **multiple Evidence rows** sharing an interpretation, never as
  an Evidence with many sources/targets.
- **Immutability:** `source` and `target` are immutable once the Evidence is written.
  Only interpretive fields (`kind`/`role`/`polarity`/`confidence`/`scope`/
  `interpretation`) may change, and only while no Decision cites the Evidence.
- **`experiment` and `note` are not canonical node types.** An experiment that is the
  claim source is modeled as an Evidence with `kind=experimental`; a direct human
  observation is `kind=observation` (a `hypothesis` is an Evidence with a `hypothesis`
  role); matching the frozen
  `kind` enum in `EVIDENCE_PROVENANCE.md` (`experimental | literature | computation |
  observation | note`). A free-form note is a
  project-side scoped record; it is not a graph node. The source list above is the
  frozen legal set.

---

## Physical persistence is deferred (freeze the contract, not the tables)

Per the reviewer's finding, PR1 freezes the **logical graph contract above** — including
the **ownership and lifecycle** of `GlobalProvenanceEdge` vs `ProjectKnowledgeEdge` (see
the matrix section) — but does **not** lock the physical Relation schema. The Phase-1
executable spike will decide, against a real implementation, between:

```text
(a) one physical edges table shared by GlobalProvenanceEdge + ProjectKnowledgeEdge
    (kind discriminator + per-kind FKs)
(b) separate edge-family tables:  ObjectVariant, ObjectDerivation, ObjectRepresentation,
    ObjectEvaluation, RunInputOutput, ArtifactImport, DecisionSelects, DecisionCites,
    DecisionSupersession
```

Reasoning for deferral: a single polymorphic table tends to produce many nullable
FK columns and weak referential integrity, and adding a node kind still needs a
migration — true polymorphism is not gained. The likely outcome is edge-family tables
(b), with the already-promoted `DecisionEvidence` as the natural model for the
`cites` family. Do **not** treat either option as fixed today.

**Relation uniqueness vs append-only supersession** is likewise deferred. The earlier
draft's `UNIQUE(source, target, relation_type)` conflicts with append-only
correction (a corrected edge with the same triple would collide). The spike will pick
one of:

```text
(a) relations get their own identity + an explicit superseded-by pointer (no
    uniqueness on the triple)          [recommended default]
(b) uniqueness only over currently-active edges (partial unique index)
(c) simply drop the uniqueness constraint
```

Default for Phase 1 is (a) — an edge is an independent immutable record with an
id + optional `superseded_by` — which reconciles immutability with correction.

---

## Which edges are immutable

All matrix edges are immutable once written (a provenance graph). A wrong edge is
corrected by adding a **superseding edge** (pointing `superseded_by`) or, for the
`cites` family, by a newer join row — never by editing or deleting the original.

## How relation types are extended

`RelationType` is the Core-owned closed enum defined by the matrix above (it
supersedes the bootstrap `RelationPolarity`). `generated_by` is **not** in the enum — it
is a derived traversal over `produced`+`imported_as`, never a persisted relation. Adding
a scientific relation is a deliberate, reviewed Core change — Core's
scientific-semantics vocabulary, not plugin-extensible. Frontend, Agent tool schemas,
and skills derive from this single enum via the generated contract; never duplicated by
hand.

## Preventing invalid combinations

- **Schema/validation layer:** relation must connect two distinct nodes; the
  `relation_type` must be a known value; self-relations rejected; the endpoint kind
  (Series vs Revision) must match the matrix for each `relation_type`; for
  project-scoped endpoints, same-project enforced.
- **Domain-service layer:** semantic rules live here, e.g. "an ArtifactReference has
  exactly one owning `produced` edge", "a Decision `cites` only Evidence in the same
  Project", "a `supersedes` edge has cardinality ≤ 1 out", and the **project-context
  write invariant**: every project-scoped write (`Evidence`, `Decision`,
  `ProjectKnowledgeEdge`) may only reference global endpoints that are **already
  visible through that Project's `ProjectResourceLink` set** — no ghost knowledge whose
  anchors the Project cannot see.
- Rule of thumb: **structural** invariants (distinctness, membership, known enum,
  endpoint kind) in schema validation; **semantic** invariants (valid source/target
  kinds per `relation_type`, single-owner edges, project-context visibility at write
  time) in the domain service.

## What the graph must answer (directions match the matrix)

```text
Why does this scientific object exist?
    → derived_from chains (Revision → Revision) + imported_as (ArtifactReference |
      ExternalReference → Revision) + the derived `generated_by` traversal back to
      producing runs + citing Decisions
Where did this structure come from?
    → Run/Session --produced--> ArtifactReference --imported_as--> StructureRevision
Which run produced this artifact?
    → Run/Session --produced--> ArtifactReference (single owning edge, direction run→artifact)
Which variants is this protein related to?
    → VariantSeries --variant_of--> ProteinSeries (conceptual, Series → Series)
Which evidence caused which decision?
    → Decision --cites--> Evidence, then Evidence.target
```

Recorded in **ADR-0010** (logical contract: endpoint semantics, ownership, and lifecycle
frozen; the physical Relation table decision is a Phase-1 spike record).
