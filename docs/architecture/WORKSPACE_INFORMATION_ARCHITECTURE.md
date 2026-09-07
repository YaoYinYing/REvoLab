# Workspace Information Architecture & API Contract

> **Status: Proposed — pending human architecture review.** Wireframe-level
> workspace IA and the API/frontend contract,
> reconciled from Subagent D.

## Workspace information architecture

**Global chrome (top bar):**
- **Project selector** (left) — switches the active Project; drives left nav,
  context inspector, and agent panel.
- **Global object search** (command-palette typeahead over object names + external
  IDs + evidence labels). Breadcrumbs mirror navigation.

**Left navigation (project-scoped):**

```text
Overview      → project's current scientific state (active decision, open next-actions, recent evidence, provider availability for you)
Objects       → the organization tree (parent/child = pure grouping). Selecting a node opens its OBJECT DETAIL (graph-centric), not a CRUD card
Evidence      → all Evidence records, grouped by kind, filterable by source (authority / reference type)
Runs & Artifacts → cross-cutting view of Run/Artifact references (aggregated from evidence)
Decisions     → the project's decision log (open / committed / superseded)
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

## Key pages (what each answers scientifically)

- **Project Overview** — "What is this project's current state, and what should I do
  next?" (committed decisions, open decision, next-actions, recent evidence, provider
  availability)
- **Scientific Object Detail** — "Why does this object exist, what is it, and how is
  it connected?" (inbound `derived_from`/`generated_by`, linked run/artifact
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

## API / frontend contract

**Ownership:** FastAPI is the single canonical owner of ALL domain schemas/enums.
The frontend consumes **only generated TypeScript** (a build-step generator with a CI
drift check); no manually duplicated enums. The current `App.tsx` hardcodes
`object_type`/`relation_type` strings — that anti-pattern is eliminated.

### Resource surface (CRUD + read)

```text
GET/POST            /api/projects
GET                 /api/projects/{id}                → project METADATA (not the whole graph)
GET/POST            /api/projects/{id}/objects        → paginated list / create (creates a draft object)
GET/PATCH/DELETE    /api/objects/{id}                 → object-detail aggregate / edit series spine / archive
POST                /api/projects/{id}/relations      → create an immutable provenance edge
GET                 /api/relations/{id}               → read only (relations are immutable once written)
POST                /api/projects/{id}/evidence       → create an evidence claim
GET/PATCH/DELETE    /api/evidence/{id}                → PATCH updates interpretive fields ONLY (kind/role/interpretation/polarity/confidence/scope); source+target+identity are immutable
POST                /api/projects/{id}/decisions      → create a Decision as DRAFT
GET/PATCH/DELETE    /api/decisions/{id}               → PATCH allowed only while draft (status, next_actions); committed is immutable
GET                 /api/projects/{id}/runs           → aggregated Run/Artifact references
GET                 /api/runs/{id} / /api/artifacts/{id}
GET                 /api/providers                    → capability discovery (Actor-scoped)
GET                 /api/providers/{key}/schema/{capability_kind}
```

### Domain commands (verbs, separate from CRUD; the lifecycle command model)

```text
POST /api/decisions/{id}/commit         → draft → committed (the ONLY promotion; authorized actor)
POST /api/decisions/{id}/supersede      → point a superseding Decision (close one, keep history)
POST /api/relations/{id}/supersede      → add a superseding/corrected edge (relations are never edited)
POST /api/runs/{id}/refresh             → re-resolve provider reference (never mutate stored provenance)
GET  /api/context?scope=...             → assembled context slice (mirrors the context inspector)
POST /api/objects/{id}/links/evidence   → typed link
```

**Lifecycle command model (single source — see `SCIENTIFIC_GRAPH.md`, `EVIDENCE_PROVENANCE.md`,
`COLLABORATION_IDENTITY.md`):**
- **Decision status** is exactly `draft | committed` (+ derived `superseded`); there is
  no `open`/`proposed`/`concluded` stored status. `POST .../decisions` always creates a
  draft; only `commit` (authorized) promotes; `supersede` links a successor.
- **Relations** are immutable after creation; there is no `PATCH /relations/{id}`.
  Correcting an edge is `supersede` (or a new edge), see the graph contract.
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
GET /api/objects/{id}
→ { object, provenance: {inbound/outbound relations, creation evidence chain, importing refs},
    evidence, decisions }
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
