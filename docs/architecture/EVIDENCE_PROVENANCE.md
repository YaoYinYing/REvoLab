# Evidence & Provenance

> **Status: Accepted** (merged into `main`). Defines the distinct
> durable record types, provenance
> semantics, and the Decision/knowledge promotion boundary. Inspired by
> LaminDB / AiiDA / OpenLineage, adapted to REvoLab. Relational, no event sourcing,
> no graph DB.

## Core principle

**Provenance-completeness:** context must stay traversable without copying any
external mutable execution truth. **Identity is durable; state is refreshable.**

> A reference is a **fact**. Evidence is an **interpreted claim**. A Decision is
> the **committed conclusion**.

---

## The distinct durable record types (do NOT collapse)

| Record type | What it IS | Distinct table |
|---|---|---|
| **ScientificObject** | a typed scientific entity REvoLab owns | `scientific_object_series` + `scientific_object_revision` + per-type tables |
| **RunReference** | durable, namespaced pointer to an external execution (identity card) | `run_references` |
| **SessionReference** | durable, namespaced pointer to an external interactive session (identity card) | `session_references` |
| **ArtifactReference** | durable, namespaced pointer to an external output (identity card) | `artifact_references` |
| **LiteratureReference** | durable citation to an external publication | `literature_references` |
| **ExternalReference** | resolver/cache metadata record that **references** an `ExternalIdentity` (`external_identity_id` FK) — checksum/as_of/bounded validated metadata, never a copy of the external payload | `external_references` |
| **Evidence** | a durable, interpreted **claim** that some reference/experiment/note supports or relates to a project target | `evidence` |
| **Decision** | durable project conclusion + next actions citing Evidence | `decisions` |

These eight durable record types — together with the `Project` container/scope node —
are the canonical graph node categories (see `SCIENTIFIC_GRAPH.md`).
**ExperimentalEvidence is NOT a separate table.** It is an Evidence whose `kind` is
`experimental` whose payload references an Artifact/Literature/ScientificObject.
A ninth table is premature ELN ontology.

---

## Byte ownership boundary — `ContentStore`

REvoLab needs to own **bytes that do not come from an external engine**: an uploaded
PDB/FASTA/CSV/trajectory/assay result. Those files are still ArtifactReference nodes, but
with `authority = revolab` and an **internal** resolver: the `ContentStore`.

```text
ContentStore  (put/get immutable bytes; fsspec backend:
               local | S3 | future)
    put(bytes) -> { checksum, size, content_type, store_handle }
    get(store_handle) -> bytes  (read-only, content-addressed)

ArtifactReference
    authority = revolab     → resolver = ContentStore
    authority = revocompute → resolver = REvoComputeDriver
```

- The same scientific-artifact abstraction covers **both** internal uploads and
  external compute outputs: one ArtifactReference + one ArtifactResolution capability
  shape; only the resolver differs.
- This is a **byte ownership boundary**, not a file server or a snapshot store: bytes
  are immutable and content-addressed (`checksum`), resolved on demand, and sized/
  typed at `put` time.
- `ContentStore` is a **Core shared storage primitive**; the owning domain for the
  *artifact record + provenance* remains Evidence/Provenance (see `DOMAIN_BOUNDARIES.md`).

## Reference = fact, Evidence = interpreted claim

- **Is a RunReference itself Evidence?** **No.** A run record is the provenance
  fact "this run happened with these inputs." It becomes evidence only through an
  Evidence record that asserts "this run, interpreted in context C, supports target
  T with confidence X."
- **Is an ArtifactReference evidence?** **Only when interpreted.** The artifact
  "raw PDB output" is not a claim. Two Evidence records may interpret the *same*
  artifact in contradictory ways and coexist, because each is a separate durable
  claim.

### Correction to the bootstrap

The current `Evidence` model carries `provider`/`external_id` directly and
`evidence_type = RUN | ARTIFACT | LITERATURE | ...`, which conflates the *thing*
(a reference) with the *claim* (Evidence). **Split reference nodes out of
Evidence** into first-class reference tables; make Evidence an edge-like claim
pointing at a `source` (the reference/observation interpreted) and a `target`
(the ScientificObjectRevision/Decision/Evidence the claim is about; see
`SCIENTIFIC_GRAPH.md`).

---

## Provenance semantics

Model the chain as a directed acyclic provenance graph:

```text
ScientificObjectRevision | ArtifactReference --consumed_as_input_by--> RunReference | SessionReference
RunReference | SessionReference             --produced-->            ArtifactReference
ArtifactReference
  | ExternalReference    --imported_as-->         ScientificObjectRevision   (creates the derived revision)
```

**RunReference is an identity card, not a relationship store (single provenance
truth).** Input and output relationships live **only** as edges — `consumed_as_input_by`
(what a run consumed) and `produced` (what a run produced) — never as denormalized
columns on the reference rows:

- `input_objects` / `output_artifacts` / `originating_run` are **derived aggregate
  fields** returned by API/traversal queries, not persisted columns. Persisting them
  alongside the edges would be a second, driftable copy of the same truth; the edges are
  the single durable source, and the aggregate fields are computed on read.

**Record origin AND content without copying REvoCompute state:** pin the immutable
identity and a content fingerprint (checksum/digest). A RunReference stores:
`authority(=revocompute for its own runs), run_id (stable identity), task_type,
input_parameter_digest, submitted_at` — its inputs are the `consumed_as_input_by` edges.
An ArtifactReference stores: `authority, artifact_id, content_type, size, checksum,
version_id` — its originating run is the reverse traversal of `produced`.

**Checksum is NOT "proof of origin"** (reviewer finding #12). A checksum proves
**content integrity / byte identity** — that a fetched artifact is byte-for-byte what
was recorded. **Origin** is established separately by the **provenance assertion**:
the RunReference's identity, the `produced` edge, and the provider's own identity/
receipt. They are two distinct semantics and are kept apart:
- content integrity = `checksum/digest` (recompute-and-verify);
- origin = provenance assertion + provider identity (who did the work), *not* the checksum.
This is LaminDB's hash-and-link philosophy (integrity) plus AiiDA's immutable
nodes-with-edges (origin) — without a graph DB.

## Durable identity: immutable vs refreshable

**Durable external identity (immutable):** `authority` (the identity namespace —
`uniprot`, `pdb`, `doi`, `pubmed`, or `revocompute` for its own IDs; see
`PROVIDER_CAPABILITIES.md` "Authority vs provider"), authority-native stable
`run_id`/`artifact_id`/accession, `checksum`/`digest`, `size`, an immutable
`version_ref`, content type.

**External identity vs external reference — one truth.** `ExternalIdentity` (owned by
the Scientific Object domain, `UNIQUE(authority, native_id)`) is the **single** registry
of an external identity. `ExternalReference` does **not** re-store `authority`/
`native_id`; it holds an `external_identity_id` FK plus resolver/cache metadata
(`checksum`, `as_of`, bounded validated metadata). The chain is:

```text
ExternalIdentity  (the durable id, once)
   ↓ external_identity_id FK
ExternalReference (resolver/cache metadata)
   ↓ imported_as
ScientificObjectRevision
```

**Never identity:** a filesystem path, a mutable container tag (`:latest`), a bare
bucket name, a mutable username, or a *resolver provider* (an aggregator vs a
direct API). Paths and resolver names are access/location hints, not identity.

**Immutable on the REvoLab side:** reference ID, authority, authority-native ID,
checksum/size, input digest, timestamps. Write-once provenance nodes.

**Refreshable from the provider (via a capability, e.g. ArtifactResolution):** live
run status, live download URL / current "latest" pointer, provider-side metadata.
Core never stores these as truth. If a provider disappears, the durable pin +
checksum remain authoritative and provenance stays traversable; refresh simply
becomes unavailable.

## Evidence attributes

- `kind`: experimental | literature | computation | observation | note
- `role`: primary_support | corroborating | background | methodology |
  negative_result | hypothesis
- `interpretation`: free text — "what we conclude from this"
- `polarity`: supports | contradicts | neutral
- `confidence`: bounded ordinal (low/med/high or 0–1) + optional `confidence_source`;
  a lightweight field, not a GRADE engine
- `scope`: which aspect it applies to (an artifact can support one facet and
  contradict another of the same target)
- `as_of`: interpretation timestamp

**Contradiction & lifecycle:** contradictory Evidence records coexist as separate rows;
contradiction is a first-class fact, not an inconsistency to avoid. An Evidence row is
**mutable only while no COMMITTED Decision cites it and no other Evidence targets it**
(see `SCIENTIFIC_GRAPH.md`): its `source`/`target` are immutable from creation, and its
interpretive fields freeze once a committed Decision cites it **or another Evidence
targets it** — a draft Decision's citation does **not** freeze it. Correction after
freeze is a **new Evidence row**, never an in-place edit. A **Decision** settles a
contradiction by citing both sides with per-citation `cited_as` polarity and stating the
resolution rationale; supersession happens by recording a *newer* Decision, never by
mutating or deleting the old one.

---

## Minimal provenance graph (the four questions)

```text
Why does this object exist?          → derived_from chains (Revision → Revision) + imported_as (ArtifactReference | ExternalReference → Revision) + the derived `generated_by` traversal back to producing runs + citing Decisions
Where did this structure come from?  → RunReference --produced--> ArtifactReference --imported_as--> StructureRevision
Which run produced this artifact?    → RunReference --produced--> ArtifactReference (single owning edge; direction run→artifact)
Which evidence caused which decision?→ Decision --cites--> Evidence, then Evidence.target (polarity is an Evidence field / cited_as)
```

Edge directions follow the single canonical matrix in `SCIENTIFIC_GRAPH.md`.
`supports`/`contradicts` are **fields** of Evidence (and `cited_as` on a Decision
`cites` join), not separate graph edges.

---

## The knowledge layer

**Avoid a premature ELN ontology.** Note / Observation / Hypothesis / NextAction /
Conclusion are **modes**, not tables:
- An observation is an Evidence with `kind=observation`.
- A hypothesis is an Evidence with a `hypothesis` role/interpretation + a proposed test.
- A conclusion is the *derived* current committed, not-superseded Decision on a
  question (there is no stored `status=concluded`).
- Next actions live in `Decision.next_actions`. A free-form **Note is deferred** — it is
  not a canonical node and has **no durable model** in PR1 (no fields to point at); if a
  note-like first-hand claim is needed it is modeled as an Evidence with `kind=note`.

**Decision is the one first-class knowledge node** — the durable project-truth
record with its own lifecycle.

**Decision status (defined exactly once, per ADR-0011):**
`draft | committed` (a stored lifetime field) with `superseded` **derived** from a
`supersedes` edge — it is never a stored status. A Decision is created as `draft`,
moves to `committed` only through the explicit `commit` domain operation by an
authorized actor, and becomes `superseded` only by a newer Decision pointing to it.

**A conclusion** is a Decision with a committed `statement` — there is no stored
`status=concluded`; "conclusion" is the *derived* current-not-superseded committed
Decision on a question (below).

### What is a Decision?
A durable, authored record: `title`, `statement` (the committed conclusion),
`status`, `next_actions`, and `cites → Evidence` (with per-citation `cited_as`
polarity). It is **project truth**, not chat.

- **Superseded?** Yes — by a newer Decision pointing to a superseding Decision via
  a `supersedes` edge, never by editing/deleting the old one. The old Decision stays
  as immutable history.
- **Reopened?** Reopening = creating a new Decision that reinstates the question and
  supersedes the closed one. Treat `status` as a presentation facet; the immutable
  statement + supersession edges are the durable truth.
- **Rationale preserved** by: the statement + cited Evidence (reasoning at commit
  time), Evidence `interpretation`, and citation `cited_as` polarity — all append-only.
- **"Current scientific conclusion"?** A derived notion: the most recently
  committed, not-superseded Decision on a question (follow `supersedes` to the
  leaf). Not a mutable field.

### The promotion boundary

> **Agent output is conversation until explicitly promoted into project knowledge.**

- A Decision first has status `draft`, authored by an Agent, with its
  cited Evidence. A draft is *in the project but not project truth*. (Status is
  exactly `draft | committed` + derived `superseded` — no separate `proposed`/`open`
  stored state.)
- **Commit** is a distinct, auditable domain operation (`draft → commit`) by an
  **authorized human actor** (or a policy-governed actor with commit authority).
  It is *not* the same as "create."
- Only committed decisions — only after an authorized commit — are accepted project
  truth. Everything un-promoted is conversation/proposal.

**Correction to the bootstrap:** the current API lets any `POST /decisions`
immediately create a durable truth record. Add explicit `draft → committed`
promotion.

Recorded in **ADR-0010** (graph) and **ADR-0011** (promotion).
