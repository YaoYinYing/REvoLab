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

**Ingestion use cases (Phase 15 clarification).** The set of things ContentStore holds
is explicit, and Phase 15 adds the third:

```text
user uploads
persisted local-tool outputs
explicitly imported static external data snapshots   (Phase 15: PDBx/mmCIF coordinates)
```

It does **NOT** mean REvoLab scientifically originated the bytes, and it does not turn
ContentStore into a remote cache, an HTTP cache, or a provider mirror. Only an
**explicit Import** transfers the selected external snapshot into REvoLab custody: a
read-only discovery or candidate never does, and neither does merely resolving
metadata. The net-new Phase-15 consequence is that an imported structure's coordinate
bytes survive the provider becoming unavailable — durability comes from the
content-addressed `checksum`, never from assuming a remote URL stays reachable. Because
ContentStore and PostgreSQL are not one distributed transaction, a rolled-back import
may leave an unreachable content-addressed blob: it is not visible Project truth, it is
safely reused by a later import of the same bytes, and a future collector may remove
it.

## External discovery: candidate = remote read, reference = durable identity

Phase 13 separates three things that are easy to conflate:

```text
LiteratureCandidate   ephemeral, untrusted output of a REMOTE read-only lookup
LiteratureReference   the durable `(authority, native_id)` citation identity card
Evidence              the Project's interpreted claim ABOUT that publication
```

`LiteratureReference` stays a small global identity card (`authority`, `native_id`,
`title`); Phase 13 adds no provider payload, abstract, or metadata column. Authority
(`pubmed`) is not the resolver/provider (`ncbi`): a future resolver could resolve
the same identity unchanged. Discovery persists nothing; only an explicit human
Import creates the reference + `ProjectResourceLink`, and import never creates
Evidence. Provider outage never invalidates a stored reference. Normative semantics:
`docs/architecture/EXTERNAL_LITERATURE_DISCOVERY.md` (ADR-0019).

**Phase 14 extends this to a biological entity, and adds one more distinction.**
Importing an external protein creates **scientific objects and provenance**, not
interpretation, and a *snapshot* of the external record rather than a live view:

```text
ProteinCandidate       ephemeral, untrusted output of a REMOTE read-only lookup (no sequence)
ExternalIdentity       the durable `(authority, native_id)` — global, not Project-owned
ExternalReference      immutable resolver snapshot provenance over THAT identity
Protein ScientificObject   the biological concept (one immutable initial Revision)
Sequence ScientificObject  the exact canonical amino-acid content (its own Revision)
Evidence / Decision    the Project's interpretation (a SEPARATE, explicit human act)
```

Protein and Sequence are two distinct ScientificObjects joined by the typed
`represents` relation, never one payload: a Protein is a concept, a Sequence is
content. The `ExternalReference.checksum` is a deterministic digest of the
**normalized scientific bundle** (`{protein_payload, sequence_payload}`) — it is
snapshot identity, **not proof of origin**; origin is the ExternalIdentity +
ExternalReference + `imported_as` provenance chain. Import creates zero Evidence and
zero Decision, and an already-imported object is never silently mutated or refreshed:
a changed external record raises a typed conflict, because whether an external change
means a new revision, a new object, an alias, or a correction is a separate scientific
question that Phase 14 deliberately does not answer. Provider outage never invalidates
an imported object or its provenance. Normative semantics:
`docs/architecture/EXTERNAL_PROTEIN_IMPORT.md` (ADR-0020).

**Phase 15 extends this to a 3D structure, and adds the byte-custody vs
scientific-origin distinction — which is load-bearing.** A PDB structure import takes
immutable CUSTODY of the coordinate bytes, so three facts must be read from three
different places:

```text
StructureCandidate       ephemeral, untrusted output of a REMOTE read-only lookup (no coordinates)
ExternalIdentity(pdb)    the durable `(authority, native_id)` — global, not Project-owned
ExternalReference        immutable resolver snapshot provenance over THAT identity
ArtifactReference(revolab)  the exact BYTES REvoLab owns, addressed by content checksum
Structure ScientificObject  the normalized imported content (one immutable initial Revision)
Evidence / Decision      the Project's interpretation (a SEPARATE, explicit human act)
```

> **`authority=revolab` on an ArtifactReference means REvoLab owns those exact bytes
> — never that REvoLab authored or scientifically originated them.**

Never infer scientific origin from `authority=revolab`, and never infer byte
availability from `authority=pdb`: an `authority=pdb` artifact reference would leave
the imported Structure dependent on live provider availability and current mutable
archive state. Scientific origin is the `ExternalIdentity(pdb, <entry>)` +
`ExternalReference` + `imported_as` chain; durable byte identity is the ContentStore
checksum. The snapshot checksum digests `{structure_payload, coordinate_checksum}`, so
a changed archive entry — including changed coordinates — raises a typed conflict
rather than silently rewriting the revision or replacing the artifact. Import creates
zero Evidence and zero Decision, and provider outage never invalidates an imported
Structure, its coordinate artifact, its provenance, or its Project Search visibility.
ContentStore custody is a byte boundary, not a scientific claim: it is deliberately
not a remote cache, an HTTP cache, or a provider mirror. Normative semantics:
`docs/architecture/EXTERNAL_STRUCTURE_IMPORT.md` (ADR-0021).

---

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
- Next actions live in `Decision.next_actions`. A note-like first-hand claim is
  modeled as an Evidence with `kind=note`.
- **Phase 10 refinement (Accepted):** the Project
  Notebook adds a durable, Project-scoped `ProjectNote` working document
  (`PROJECT_NOTEBOOK.md`, ADR-0016). It is explicitly **not** Evidence and **not** a
  canonical knowledge node: it creates no Evidence/Decision/provenance row, and any
  conversion into a scientific claim goes through the canonical Evidence command.
  `Evidence(kind=note)` remains the representation of a note-like scientific claim;
  Note is not added to the source-kind matrix.

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
