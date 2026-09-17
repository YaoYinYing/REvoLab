# Workspace Information Architecture & API Contract

> **Status: Accepted** (merged into `main`). Wireframe-level
> workspace IA and the API/frontend contract,
> reconciled from Subagent D.

## Workspace information architecture

**Global chrome (top bar):**
- **Project selector** (left) — switches the active Project; drives left nav,
  context inspector, and agent panel.
- **Global object search** (command-palette typeahead over object names + external
  IDs + evidence labels). Breadcrumbs mirror navigation. **"Global" means globally
  placed UI, not an unrestricted global-resource query**: results are the **union of
  resources visible through the Projects the Actor belongs to** (active Project first),
  filtered through each Project's authorization projection — never a bare
  `SELECT ... WHERE name ILIKE ...` over the global tables, which would leak
  titles/external IDs.

**Left navigation (project-scoped):**

```text
Overview      → project's current scientific state (active decision, open next-actions, recent evidence, provider availability for you)
Objects       → the organization tree (parent/child = pure grouping). Selecting a node opens its OBJECT DETAIL (graph-centric), not a CRUD card
Analyze       → the Project Tool Harness surface: inspect artifacts, describe/select table columns, plot X-Y, and (from the same catalog) reach remote computation — schema-driven, never domain vocabulary
Notes         → the Project Notebook: shared, versioned working documents (create / append revision / link Project-visible context / revision history); working knowledge, never Evidence or Decision truth
Evidence      → all Evidence records, grouped by kind, filterable by source (authority / reference type)
Runs & Artifacts → cross-cutting view of Run/Artifact references (aggregated from evidence)
Decisions     → the project's decision log (draft / committed / superseded)
Knowledge     → the promoted project-truth surface (committed decisions + conclusions); the promotion gate lives here
```

**Main object workspace:** driven by the selected object — identity header, typed
metadata, graph neighborhood (relations in/out as edges with source→target/polarity/
rationale), attached evidence, and decisions that cite it. *Relations are created
from an object, not from a top-level bucket.*

**Context inspector (right rail):** passive, selection-driven, read-mostly. Shows the
current object brief, the governing decision, supporting & contradicting evidence,
provider-reference status (resolved/stale/unavailable), and the minimal context
slice. This is the UI twin of the backend `/context` assembly — one selection
semantics.

**Agent panel (right rail, collapsible):** sandboxed from accepted project truth.
Agent proposals appear as PENDING with explicit promote/commit buttons; nothing an
agent says becomes project graph data until promoted.

**Navigation decision:** the left nav maps 1:1 to first-class domain resources. One
deliberate divergence: **Relations is NOT a top-level bucket** — it is a graph lens
inside object detail, not a page people browse top-down.

**Project search (Phase 12, implemented):** the `Search` nav entry is the
Project-level retrieval surface. It is a retrieval/presentation surface, not a
domain resource bucket: a bounded query with an explicit scope
(`Project shared context` / `My private conversations` / both), an optional
target-kind filter, results grouped by backend-owned target kind, and a per-hit
`Open` (navigate to the EXISTING canonical surface and select it) plus — for
context-selectable kinds only — an explicit `Add to Agent context` that reuses the
canonical `ContextSelectionCreate`. Conversation hits are visually labelled
`private working memory` and are never selectable into shared Agent context.
Search results are candidate references, never truth and never an implicit context
change. The top-bar **global object search** above remains the future
cross-Project command palette; it is **not** implemented in Phase 12, and any
implementation must obey the same authorization projection described there.
Normative retrieval semantics: `docs/architecture/PROJECT_SEARCH_RETRIEVAL.md`.

**Literature (Phase 13, implemented):** the `Literature` nav entry is the external
discovery surface. It is deliberately SEPARATE from `Search`: `Search` retrieves what
REvoLab already knows, while `Literature` discovers publications it does not yet
know. The provider is chosen from the Provider Catalog filtered by the backend-owned
`literature_discovery` capability kind (never by a hard-coded provider key); a
bounded query returns external candidates labelled `external · not yet in Project`,
each with a citation line and its `authority:native_id` identity. An explicit
`Import to Project` (owner/member only) re-resolves the identity server-side and
creates the canonical `LiteratureReference` + `ProjectResourceLink`, after which the
publication appears under `Imported literature` and becomes visible to `Search`. An
explicit `Use as Evidence` hands the imported reference into the EXISTING Evidence
creation surface prefilled with `source_kind = literature_reference`; import alone
never creates Evidence. A viewer sees the discovery surface but no import/interpret
control. Normative semantics: `docs/architecture/EXTERNAL_LITERATURE_DISCOVERY.md`
(ADR-0019).

**Objects / Discover proteins (Phase 14, implemented):** the `Objects` workspace hosts
two compact panels rather than a new top-level entry or a generic external-database
dashboard:

```text
Objects
├ Project objects      what REvoLab already knows (create + collection)
└ Discover proteins    external protein discovery + explicit import
```

The provider is chosen from the Provider Catalog filtered by the backend-owned
`protein_discovery` capability kind (never by a hard-coded provider key); a bounded
query returns external candidates labelled `external · not yet in Project`, each with
protein name, gene, organism, accession, length, and reviewed status. An explicit
`Import to Project` (owner/member only) re-resolves the identity server-side and
creates the canonical **Protein** and **Sequence** ScientificObjects plus their
identity/provenance graph, after which the surface links to both through the EXISTING
object-detail surfaces and both become visible to `Search`. Import alone never creates
Evidence: a later human may interpret it through the existing Evidence surface. A
viewer sees the discovery surface but no import control. The only provider-specific
code is the legal attribution of the data source, which is presentation, not
capability semantics. Normative semantics:
`docs/architecture/EXTERNAL_PROTEIN_IMPORT.md` (ADR-0020).

**Objects / Discover structures (Phase 15, implemented):** the same `Objects` workspace
gains a third compact panel — still not a new top-level entry and still not a generic
external-database dashboard:

```text
Objects
├ Project objects      what REvoLab already knows (create + collection)
├ Discover proteins    external protein discovery + explicit import
└ Discover structures  external PDB structure discovery + explicit import
```

The provider is chosen from the Provider Catalog filtered by the backend-owned
`structure_discovery` capability kind (never by a hard-coded provider key); a bounded
query returns external candidates labelled `external · not yet in Project`, each with
PDB ID, title, experimental method, resolution, and release date. Discovery downloads
no coordinates, so it is cheap and ephemeral. An explicit `Import to Project`
(owner/member only) re-resolves the identity server-side, downloads the bounded
canonical PDBx/mmCIF snapshot, takes **immutable ContentStore byte custody**, and
creates the canonical **Structure** ScientificObject plus its identity/provenance
graph; the surface then shows `Imported to Project`, `Open Structure`, and
`Coordinate artifact available`. The **Structure detail derives the coordinate
artifact by provenance traversal** (the `imported_as` edge from the artifact to the
revision) and reads its checksum/size from the existing resource surface — there is no
second Structure detail, no persisted `coordinates_ref` for UI convenience, and no raw
coordinate text or 3D viewer in this phase. Import alone never creates Evidence, and
the imported Structure becomes visible to `Search` with no index. A viewer sees the
discovery surface but no import control. The only provider-specific code is the legal
attribution of the data source, which is presentation, not capability semantics.
Normative semantics: `docs/architecture/EXTERNAL_STRUCTURE_IMPORT.md` (ADR-0021).

## Key pages (what each answers scientifically)

- **Project Overview** — "What is this project's current state, and what should I do
  next?" (committed decisions, open decision, next-actions, recent evidence, provider
  availability)
- **Scientific Object Detail** — "Why does this object exist, what is it, and how is
  it connected?" (inbound `derived_from`/`imported_as`, linked run/artifact
  evidence, outbound relations, cited-by decisions)
- **Evidence Detail** — "Is this piece of evidence trustworthy, and what does it
  support/tell us?" (kind, durable reference, interpretation, polarity, role, which
  decisions it affects)
- **Decision Detail** — "Why did we decide this, on what evidence, and is it still
  current?" (statement, cited evidence with polarity, status, next-actions,
  supersession chain)
- **Run/Artifact Reference Detail** — "Is this external result available and what
  created it?" (durable reference, staleness/refresh status, provenance chain)
- **Provider Capability Surface** — "What can this external system do here, and is
  it credentialed for my project?" (schema-driven, never hand-duplicated)
- **Analyze / Tools** — "What can I compute locally over this artifact, and what
  was derived from it?" (closed typed tools in one ToolCatalog shared by the human
  workspace and the Agent; local results are ephemeral or explicitly persisted
  derived artifacts, never automatic project truth)
- **Notes (Project Notebook)** — "What are we currently thinking/writing, who
  changed it, what Project entities does it refer to, and how did it change over
  time?" (Project-shared working documents; editing appends an immutable revision;
  mentions are non-semantic references; a Note is never Evidence or Decision truth —
  see `PROJECT_NOTEBOOK.md`)

## API / frontend contract

**Ownership:** FastAPI is the single canonical owner of ALL domain schemas/enums.
The frontend consumes **only generated TypeScript** (a build-step generator with a CI
drift check); no manually duplicated enums. The current `App.tsx` hardcodes
`object_type`/`relation_type` strings — that anti-pattern is eliminated.

### Resource surface (CRUD + read)

```text
GET/POST            /api/projects
GET                 /api/projects/{project_id}                 → project METADATA (not the whole graph)
GET/POST            /api/projects/{project_id}/objects         → paginated list / create (mutation-capable membership; atomically creates series + initial revision + link + stewardship)
GET                 /api/projects/{project_id}/objects/{series_id}               → object-detail aggregate (series + visible revisions + provenance + evidence + decisions)
GET/PATCH/DELETE    /api/projects/{project_id}/objects/{series_id}               → PATCH series spine / archive — gated by ResourceStewardship
POST                /api/projects/{project_id}/objects/{series_id}/revisions     → append an immutable revision (stewardship)
GET                 /api/projects/{project_id}/resources/{resource_id}           → any global resource through this Project's lens
GET/POST            /api/projects/{project_id}/evidence        → paginated list / create an evidence claim
GET/PATCH           /api/projects/{project_id}/evidence/{evidence_id}            → PATCH interpretive fields only while uncited by a committed Decision
GET/POST            /api/projects/{project_id}/decisions       → paginated list / create a Decision DRAFT
GET/PATCH           /api/projects/{project_id}/decisions/{decision_id}           → PATCH allowed only while draft (statement, cites, selects)
GET/POST            /api/projects/{project_id}/notes           → paginated list / create a Project Note (working knowledge, not truth)
GET/PATCH           /api/projects/{project_id}/notes/{note_id} → detail (latest revision + resolved mentions) / rename-archive
GET/POST            /api/projects/{project_id}/notes/{note_id}/revisions         → immutable revision history / append (base_revision_seq; stale = 409)
GET                 /api/projects/{project_id}/providers        → capability discovery (Actor + Project lens)
GET                 /api/projects/{project_id}/providers/{key}/schema/{capability_kind}
```

**Project lens is the ordinary workspace surface (round 5).** A user-facing call that
names a global resource must do so *through a Project* — the endpoint carries
`project_id` and the backend applies the Project's `ProjectResourceLink` projection.
Bare global-address endpoints (`/api/objects/{series_id}`, `/api/relations/{id}`,
`/api/providers`, …) exist only as an **internal / admin / canonical-resource surface**,
not as the ordinary workspace API; they never appear in the generated user client.

### Domain commands (verbs, separate from CRUD; the lifecycle command model)

```text
POST /api/projects/{project_id}/decisions/{id}/commit   → draft → committed (the ONLY promotion; authorized actor)
POST /api/projects/{project_id}/decisions/{id}/supersede→ point a superseding Decision (close one, keep history)
POST /api/projects/{project_id}/objects/{series_id}/import → create a new Revision on the series + its `imported_as` edge (the typed import command)
POST /api/projects/{project_id}/runs/{id}/refresh       → re-resolve provider reference (never mutate stored provenance)
GET  /api/projects/{project_id}/context?scope=...       → assembled context slice (mirrors the context inspector)
POST /api/projects/{project_id}/objects/{series_id}/links/evidence → typed link
```

**Lifecycle command model (single source — see `SCIENTIFIC_GRAPH.md`, `EVIDENCE_PROVENANCE.md`,
`COLLABORATION_IDENTITY.md`):**
- **Decision status** is exactly `draft | committed` (+ derived `superseded`); there is
  no `open`/`proposed`/`concluded` stored status. `POST .../decisions` always creates a
  draft; only `commit` (authorized) promotes; `supersede` links a successor.
- **Global provenance edges have no generic writer.** `GlobalProvenanceEdge` (#1–7) is
  created only by its typed domain operation (run import → `produced`; import command →
  `imported_as`; task submission → `consumed_as_input_by`; object commands →
  `variant_of`/`represents`/`derived_from`/`evaluates`). `ProjectKnowledgeEdge` (#8–10)
  is created only through Decision domain commands and is scoped to its
  Decision/Evidence's Project. Edges are immutable after creation; there is no
  `PATCH /relations/{id}`; correcting an edge is `supersede` (or a new edge).
- **Evidence** `PATCH` is limited to interpretive fields; `source`/`target`/identity
  are immutable.

### Graph query

One explicit bounded traversal instead of client-side graph assembly:

```text
GET /api/projects/{id}/graph?from={object_id}&depth=2&kinds=relation,evidence,decision
```

returns a bounded, typed subgraph. It is a **query**, stays out of default project
reads.

### Object-detail aggregate

```text
GET /api/projects/{project_id}/objects/{series_id}
→ { series, visible_revisions, provenance: {inbound/outbound relations, creation
    evidence chain, importing refs}, evidence, decisions }
```

A dedicated first-class read endpoint assembled by the backend domain service in one
query batch. `?mode=summary` returns just the row for tree/list rendering. It is the
single contract behind the Object Detail page.

### Key corrections to the bootstrap API

- `GET /projects/{id}` currently returns the **entire `ProjectGraph`** (O(project),
  unpaginated, teaches the frontend to load everything). **Split** into
  project-metadata + paginated collections + the optional graph-traversal query.
- Add **pagination** (page/limit, default ~50) to every collection.
- Add **ETag/If-Match** for optimistic concurrency on mutable resources (the object
  series spine, a Decision while it is a draft, Evidence interpretive fields).
  Relations are immutable, so they need no ETag. Provenance and committed
  Decisions are append-only, never rewritten.

### Generation & drift

- Freeze the OpenAPI surface before first client generation.
- Run the TypeScript generator (`openapi-typescript` + a typed fetch wrapper, or
  `openapi-fetch`) as a **build step**; add a **CI drift check** that fails if the
  committed generated output differs from a fresh regeneration.
- The generated module is the ONLY source of `ObjectType`/`RelationType`/
  `EvidenceType` strings in the frontend.

Recorded in the existing contract intent (`frontend/src/contracts/README.md`) and
**ADR-0014**.
