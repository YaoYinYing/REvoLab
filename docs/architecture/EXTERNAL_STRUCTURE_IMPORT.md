# External Structure Discovery & Immutable Coordinate Import

> **Status: Proposed — pending human acceptance** (Phase 15; normative owner of the
> external PDB-structure discovery/import sub-boundary). Related ADR:
> `adr/ADR-0021-external-structure-import-takes-custody-of-immutable-coordinate-snapshots.md`
> (**Proposed — pending human acceptance**). Both are promoted only by explicit human
> acceptance at PR review; a green test suite does not promote them.
>
> This document is normative for external structure discovery and import. It is an
> **application sub-boundary consumed by Presentation and Agent Context** — the same
> placement family as `EXTERNAL_PROTEIN_IMPORT.md`,
> `EXTERNAL_LITERATURE_DISCOVERY.md`, `PROJECT_SEARCH_RETRIEVAL.md`,
> `PROJECT_CONVERSATIONS.md`, `PROJECT_NOTEBOOK.md`, and `AGENT_ACTION_HANDOFF.md`.
> It does **not** introduce a tenth Core domain, and the accepted nine-domain DAG is
> unchanged.

Related: `SYSTEM_ARCHITECTURE.md`, `DOMAIN_BOUNDARIES.md`,
`SCIENTIFIC_OBJECT_MODEL.md`, `SCIENTIFIC_GRAPH.md`, `EVIDENCE_PROVENANCE.md`,
`PROVIDER_CAPABILITIES.md`, `PROJECT_TOOL_HARNESS.md`, `AGENT_CONTEXT.md`,
`PROJECT_SEARCH_RETRIEVAL.md`, `WORKSPACE_INFORMATION_ARCHITECTURE.md`,
`EXTERNAL_PROTEIN_IMPORT.md`, ADR-0006, ADR-0008, ADR-0009, ADR-0010, ADR-0012,
ADR-0013, ADR-0015, ADR-0018, ADR-0019, ADR-0020.

---

## 1. The invariant

> **External identity, coordinate bytes, ScientificObject content, and scientific
> interpretation are four separate truths, and none may silently substitute for
> another.**

> **Explicit Import takes custody of one immutable coordinate snapshot; it does not
> leave the imported Structure dependent on a live external provider.**

> **`authority=revolab` on an ArtifactReference means REvoLab owns those exact
> bytes — never that REvoLab authored or scientifically originated them.**

> **Scientific origin remains PDB/wwPDB provenance through `ExternalIdentity` +
> `ExternalReference` + `imported_as`.**

> **A changed external structure never silently mutates an imported Structure
> revision.**

The whole slice is:

```text
RCSB PDB read-only discovery (Search API + batched Data API)
        ↓  bounded, ephemeral, zero persistence
ephemeral StructureCandidate            (never persisted, never Project truth)
        ↓  explicit human Import
CURRENT RCSB re-resolution of (authority=pdb, native_id=PDB entry id)
        ↓
bounded canonical PDBx/mmCIF coordinate download
        ↓
ContentStore immutable, content-addressed byte custody
        ↓
internal ArtifactReference(authority=revolab)  +  ExternalReference(pdb snapshot)
        ↓
ONE Structure ScientificObject (+ one immutable initial Revision)
        ↓
identity mapping + TWO typed `imported_as` provenance edges
        ↓
Project links + new-resource stewardship
        ↓  (already searchable by Phase 12)
Project Search visibility
        ↓  explicit Add to Agent context
existing ContextSelection -> ContextBuilder
```

---

## 2. What is external discovery?

**External discovery** is a bounded, read-only read of a REMOTE system for
structures REvoLab does not already know. It is the exact complement of Phase-12
Project Search, generalizing the Phase-13/14 slices:

```text
Project Search                    = search what REvoLab already knows
External literature discovery     = discover publications outside REvoLab   (Phase 13)
External protein discovery        = discover biological entities outside REvoLab (Phase 14)
External structure discovery      = discover 3D structure entries outside REvoLab (Phase 15)
```

These are **four** distinct knowledge layers, deliberately not merged into one
hidden search system:

```text
SearchHit          = already-canonical REvoLab resource     (Phase 12)
StructureCandidate = ephemeral external provider result      (Phase 15)
```

A `StructureCandidate` never enters `project.search`; after an explicit Import the
resulting Structure becomes discoverable through the EXISTING Phase-12 read
projection, because `project.search` already matches a series through its external
identity mapping (`search._series_corpus`) and its stored name. **No search index,
document table, or retrieval model is added.**

---

## 3. What is a `StructureCandidate`? Who owns it?

`StructureCandidate` (`revolab.capabilities`) is provider-neutral, **ephemeral**
presentation data returned by one bounded external lookup:

```text
provider_key            the resolver that answered (NOT the authority)
authority               the durable identity namespace (pdb)
native_id               the durable external identifier (a PDB archive entry id)
title?                  bounded untrusted text
experimental_methods    deterministic, bounded, de-duplicated, sorted text
resolution_angstrom?    best (minimum) finite positive value, or absent
release_date?           bounded text
polymer_entity_count?   provider-reported count (presentation only)
```

Ownership: **the Provider/Capability domain creates it; nothing owns it durably.**
It is deliberately NOT a `ScientificObject`, `ExternalIdentity`,
`ExternalReference`, `ArtifactReference`, `Evidence`, `SearchHit`, or Project
truth, and it carries **no coordinate bytes** and **no `metadata: dict` escape
hatch**. It is never persisted — a page reload may legitimately lose it, and there
is no candidate cache table (`structure_candidates`, `rcsb_cache`,
`pdb_records`). Searching performs **zero durable writes** and downloads **no
coordinates**.

Breaking this is a defect, not a preference: a persisted candidate would be a
second, stale source of truth for an external entry whose only truth is the
provider.

---

## 4. What is a `ResolvedStructureRecord`?

`ResolvedStructureRecord` is the **current** provider record an explicit Import
re-resolves. It is the canonical provider-neutral scientific snapshot *input*:

```text
provider_key
authority
native_id                 the canonical PDB entry id the provider CONFIRMED
coordinate_format         "mmcif"
coordinate_bytes          the EXACT canonical PDBx/mmCIF archive snapshot
title?
experimental_methods
resolution_angstrom?
entry_revision_major?     PDB entry revision metadata (provenance, never durability)
entry_revision_minor?
entry_revision_date?
```

`coordinate_bytes` is hidden from `repr` (`field(repr=False)`) so it can never leak
through a log line, and it is bounded at the wire boundary by
`MAX_STRUCTURE_COORDINATE_BYTES`.

It is **not durable truth by itself**: it is validated by the application service,
its coordinates are handed to the ContentStore, and only then transformed into one
immutable revision, one `ExternalReference` snapshot, one internal
`ArtifactReference`, and two typed provenance edges. It is never stored as a whole,
and arbitrary RCSB metadata (author lists, entity annotations, GO/UniProt
cross-references, ligand tables, validation metrics, structure factors, maps) is
deliberately not carried through Core.

---

## 5. What is the durable PDB identity?

```text
authority = "pdb"
native_id = the canonical PDB archive entry identifier
kind      = "structure"
```

### 5.1 Both official identifier forms are supported

PDB identifiers are **not permanently four characters**, and Phase 15 never assumes
they are. The two current official forms are:

```text
classic  4 characters, first a digit            e.g. 4HHB, 1CRN, 10AL
extended  "pdb_" + 8 alphanumerics (12 chars)   e.g. pdb_00004hhb, pdb_10021abc
```

The extended grammar is quoted from the official PDBx/mmCIF dictionary and the
wwPDB PDB ID Extension FAQ: **`pdb_[a-z0-9]{8}`**.

### 5.2 The documented alias is the ONLY permitted translation

The wwPDB FAQ states: *"All existing four-character PDB IDs will be extended by
adding prefixing `pdb_0000` to the IDs, e.g., PDB ID '1abc' would be listed as
'pdb_00001abc'"*. The two forms are therefore **officially the same entry**, not two
identities. Phase 15 normalizes **case only** (`4hhb` → `4HHB`; `PDB_00004HHB` →
`pdb_00004hhb`) plus this one documented alias, and it **stores the canonical
identifier the provider response confirmed** (the Data API's `rcsb_id`) — never the
raw caller spelling. Importing `4hhb` and `pdb_00004hhb` therefore converges on ONE
`ExternalIdentity`.

Because the RCSB Search and Data APIs currently answer `204`/`null`/`404` for an
extended-only identifier (live-verified 2026-09-17; the static file host accepts
both), an extended identifier of the documented `pdb_0000<legacy>` form is looked
up through its legacy alias; any other extended identifier is sent as given and
fails closed when the provider does not know it. An extended-only identifier is
**grammar-valid** and is never rejected merely for not being four characters.

Never used as durable identity: a title, a method, a resolution, a URL, a filename,
a Data API response id, or the **resolver** key.

### 5.3 Computed Structure Models are excluded

Phase 15 imports the **experimental PDB archive** only:

```text
AF_… / MA_…  identifiers (AlphaFold DB / ModelArchive CSMs)  -> excluded / rejected
structure_determination_methodology == "computational"        -> rejected
structure_determination_methodology == "integrative" (IHM)    -> rejected (deferred)
unknown / obsolete identifier                                 -> NOT_FOUND
```

Discovery additionally constrains the provider query itself
(`request_options.results_content_type: ["experimental"]` plus an
`rcsb_entry_info.structure_determination_methodology == "experimental"` predicate),
so **a computed model can never receive `authority = "pdb"`**.

---

## 6. What becomes a ScientificObject? Why is `Structure` one object?

An explicit import creates exactly **one** conceptual ScientificObject with exactly
one immutable initial Revision:

| Object | Type | Payload | Meaning |
| --- | --- | --- | --- |
| Structure | `structure` | `{resolution, method, pdb_id: null, coordinates_ref: null, ligand_ref: null}` | the canonical deposited archive entry coordinate model |

Phase 15 does **not** automatically create `Protein`, `Sequence`, `Complex`,
`Ligand`, `Dataset`, or `Assay` objects from an entry. A PDB entry may contain
multiple polymer entities, nucleic acid, ligands, modified chains, and engineered
constructs; promoting any of that requires an explicit later semantic decision.

### 6.1 `StructurePayload` stays minimal, and its bootstrap fields are null

Only the two scientific fields that belong to the Structure snapshot are persisted:

```text
resolution   best finite positive Å value, or null (NMR/integrative)
method       bounded inert text; multiple methods are de-duplicated,
             SORTED, and joined with "; " (never arbitrary provider ordering)
```

The three pre-existing bootstrap fields are deliberately **null**, and this is
demonstrably correct rather than merely convenient:

```text
pdb_id          redundant: durable identity already lives in ExternalIdentity(pdb, native_id)
coordinates_ref redundant: coordinate linkage already lives in the typed `imported_as`
                provenance edge to the internal ArtifactReference
ligand_ref      deferred: ligand semantics belong to a later structure-content phase
```

Copying any of them into the payload would create a second, non-authoritative
representation of a fact that another part of the graph already owns. They remain in
the Core schema (removing them would be a Core change with no Phase-15 requirement)
but Phase 15 never populates them.

### 6.2 The resolution invariant is a Core registry rule

`resolution` is **finite, positive, in Å, or absent**. `NaN`, `infinity`, zero,
negative, and boolean values are rejected by the Core `StructurePayload` validator
(`domain/types_registry.py`), so **every** path that persists a `structure` revision
— a manual create, an append, or an external import — obeys the same rule. This is
deliberately not an import-only shadow rule. No resolution is ever invented for an
NMR or integrative structure.

### 6.3 A series name is presentation, not identity

The series name derives from the bounded RCSB title, falling back to `PDB
<native_id>`. Identity remains `ExternalIdentity(pdb, native_id)`. A later
title-only provider change must not mutate an existing series name during repeat
import.

### 6.4 The revision checksum remains REvoLab's existing one

`persistence.payload_checksum` over the validated payload. No second checksum field
is added.

---

## 7. What does `ExternalReference` represent?

`ExternalReference` is created over the `ExternalIdentity` and represents:

> "this external identity was resolved at this point and yielded this imported
> scientific snapshot"

It is **immutable resolver-snapshot provenance**, NOT another identity card:

```text
external_identity_id   the durable identity it is a snapshot of
checksum               deterministic digest of the NORMALIZED scientific snapshot
as_of                  resolution timestamp
cache_metadata         {resolver_provider, pdb_revision_major?, pdb_revision_minor?,
                        pdb_revision_date?}
```

The checksum is conceptually `sha256({structure_payload, coordinate_checksum})` with
sorted keys, so **changing upstream response formatting — or a title — can never
change scientific snapshot identity**. It is snapshot identity, **not proof of
origin**: origin comes from the `ExternalIdentity` + `ExternalReference` +
`imported_as` provenance, and the digest is never treated as authorship.

`cache_metadata` is explicitly bounded and normalized: the **resolver** provider key
(never the durable authority) and the PDB entry revision triple. Raw Search API
responses, Data API JSON, author lists, entity annotations, GO and UniProt
cross-references, ligand tables, validation metrics, and HTTP headers are never
stored there — or anywhere else.

---

## 8. Who owns the imported coordinate bytes?

### 8.1 ContentStore, and only ContentStore

```text
ContentStore = immutable byte custody owned by REvoLab
```

This covers user uploads, persisted local-tool outputs, and **explicitly imported
static external data snapshots**. It does **NOT** mean "REvoLab scientifically
originated the bytes".

ContentStore is deliberately NOT turned into a remote cache, an HTTP cache, a
provider mirror, or a mutable file server. Only an explicit Import transfers the
selected PDBx/mmCIF bytes into REvoLab custody. No second blob store is invented,
and the canonical internal-artifact path is reused unchanged.

### 8.2 The bytes are content-addressed

```text
coordinate bytes
    -> ContentStore.put
    -> sha256
    -> ArtifactReference(
           authority     = "revolab",
           native_id     = the content checksum (the store handle),
           checksum      = the same byte checksum,
           size          = the exact size,
           content_type  = the ONE canonical PDBx/mmCIF media type
       )
```

### 8.3 `authority=revolab` means byte custody, never scientific origin

This distinction is load-bearing:

| Fact | Owned by | Means |
| --- | --- | --- |
| PDB entry identity | `ExternalIdentity(pdb, <entry>)` | which archive entry this is |
| scientific origin | `ExternalReference` + `imported_as` | these data were resolved from PDB entry X |
| exact bytes | `ArtifactReference(revolab)` | REvoLab can reproduce these exact bytes |
| scientific content | `StructureRevision.payload` | the normalized snapshot |

Never infer origin from `authority=revolab`, and never infer byte availability from
`authority=pdb`. An `authority=pdb` ArtifactReference would leave the imported
ScientificObject dependent on live provider availability and current mutable
archive state, so it is **not** used; the internal `revolab` authority is the
imported byte truth.

### 8.4 The canonical coordinate media type

There is no IANA-registered media type for PDBx/mmCIF, and the RCSB file-download
documentation states that the generic short-style "download" URL sets
`Content-Type: application/octet-stream` — a byte-stream description that carries
**no scientific meaning**. Phase 15 therefore asserts its own canonical semantic
type:

```text
STRUCTURE_COORDINATE_CONTENT_TYPE = "chemical/x-cif"
```

`chemical/x-cif` is the de-facto community media type for CIF-family files (and is
the type the RCSB file service actually serves for `.cif`); the `x-` prefix marks
it as a non-IANA convention. The content type is presentation metadata over the
bytes: durable byte identity is the checksum, and scientific origin is the
`pdb:<entry>` identity plus `imported_as` provenance. A file extension or path is
never durable identity.

---

## 9. Which provenance edges are created?

Exactly two, both through the existing typed domain commands (never a generic
relation writer):

```text
ExternalReference --imported_as-->  StructureRevision      (#7)
ArtifactReference --imported_as-->  StructureRevision      (#7)
```

Both edges reuse the frozen #7 `imported_as` grammar and its creator authority
(import authority + steward(target Series) + read(source reference)). No
`has_coordinates`, `from_pdb`, `coordinate_file_of`, or `downloaded_from`
`RelationType` is invented.

The two edges express two DIFFERENT truths about the same revision:

```text
ExternalReference  = the scientific external source
ArtifactReference  = the exact bytes used as the coordinate content
StructureRevision  = the normalized scientific object imported from those sources
```

Identity mapping reuses the existing `ExternalIdentity` design — **one**
`ExternalIdentity` row per PDB entry, one semantic target per qualifier:

```text
ExternalIdentity(pdb, <entry>, kind="structure")
    qualifier="identity", is_canonical=true  -> StructureSeries
```

There is deliberately **no second `ExternalIdentity` for the coordinate file**: the
file is content, not another scientific entry identity.

---

## 10. Current RCSB / wwPDB contract inspection (2026-09-17)

The driver reflects the CURRENT official contracts, not a historical client. Every
normative claim below was read from the official documentation **and** live-verified
against the official hosts on **2026-09-17**.

### 10.1 Fixed hosts

| Purpose | Host |
| --- | --- |
| Search API | `https://search.rcsb.org/` |
| Data API (GraphQL) | `https://data.rcsb.org/` |
| Static entry file download | `https://files.rcsb.org/` |

### 10.2 Search API

```text
POST https://search.rcsb.org/rcsbsearch/v2/query
```

* `return_type: "entry"` returns PDB identifiers only; metadata requires the Data API.
* A free-text query is the **`full_text`** service, whose parameters contain **only
  `value`** (it has no `attribute`). Attribute search is the `text` service with
  `{attribute, operator, value}`. Because the driver builds this JSON itself and
  accepts only bounded plain text from the caller, the raw RCSB query DSL is never
  exposed.
* `request_options.paginate.rows` defaults to 10 and is capped at 10 000; Phase 15
  requests at most `MAX_STRUCTURE_RESULT_LIMIT` (20) and never paginates.
* `request_options.results_content_type` selects `experimental` vs `computational`
  (its documented default is already `["experimental"]`; it is set explicitly).
* Response: `{query_id, result_type, total_count, result_set:[{identifier, score}]}`;
  `compact` verbosity returns bare strings instead. The driver accepts both shapes.
* **A no-hit search answers HTTP 204 with an EMPTY body**, which is a successful
  empty result, not an error.

### 10.3 Data API (GraphQL)

```text
POST https://data.rcsb.org/graphql
entries(entry_ids: [...])  -> [CoreEntry]
```

* Batch-fetches MANY entries in ONE request (a runtime cap of 1000 ids exists;
  Phase 15 requests at most 20).
* Results are **keyed by `rcsb_id`, never by position** (order is not guaranteed),
  duplicate ids are de-duplicated, and unknown ids are silently dropped.
* A GraphQL error is reported with **HTTP 200 and an `errors` array**, so the
  envelope is inspected rather than the status.
* `rcsb_entry_info.resolution_combined` is a **`[Float]` array that is NOT sorted**;
  multiple values appear only for multi-method entries and it is `null` for
  NMR/integrative structures. Phase 15 takes the **minimum** (the conventional
  headline resolution) and never invents a value.
* `rcsb_entry_info.structure_determination_methodology` is
  `computational | experimental | integrative`.
* `rcsb_accession_info` supplies `initial_release_date`, `major_revision`,
  `minor_revision`, and `revision_date`.

### 10.4 File download service

```text
GET https://files.rcsb.org/download/{entry_id}.cif
```

* The documented **uncompressed PDBx/mmCIF** entry file; both `4hhb.cif` and the
  extended `pdb_00004hhb.cif` form are officially listed and byte-identical.
* Observed: HTTP 200, **no redirect**, `Content-Type: chemical/x-cif`.
* Deliberately NOT used: legacy `.pdb`, `.xml`/`.bcif`, biological-assembly
  (`-assembly1`) files, header-only variants, structure factors, NMR restraints,
  validation reports, and EM maps.

### 10.5 Identifier rules

* Extended format: `pdb_[a-z0-9]{8}` (12 characters).
* Legacy 4-character ids remain primary today; all 260 089 current holdings ids are
  4-character.
* The docs' regex and the observed `rcsb_id` casing (`4HHB`, `pdb_00004hhb`) define
  the canonical case for each form.
* CSM ids are `AF_…`/`MA_…`, and the authoritative discriminator is
  `structure_determination_methodology == "computational"`.

### 10.6 Revision history

`rcsb_accession_info.{major_revision, minor_revision, revision_date}`. A metadata
change is a minor revision (+0.1); a chemistry/coordinate change is a major revision
(+1). Revision metadata is **useful provenance**, never durability: imported
coordinate durability comes from the ContentStore checksum, not from assuming an old
remote URL stays available.

### 10.7 Rate policy

* *"While access to static files is not restricted, all RCSB PDB APIs have rate
  limits in place. We recommend starting with a handful of requests per second. If
  you exceed the limit, the service will respond with a 429 HTTP error code."*
* No numeric quota is published, so **no undocumented numeric ceiling is invented**.
  Phase 15 makes exactly **two** bounded API requests per search (one Search + one
  batched Data) and **two API requests plus one static-file download** per import —
  well inside the published guidance — with **no retry, no pagination, no prefetch,
  and no crawler**. `429`/`5xx` become a typed retryable
  `PROVIDER_UNAVAILABLE` failure; `Retry-After` is deliberately not surfaced (nothing
  in Phase 15 consumes it, and a background retry scheduler is an explicit deferral).

### 10.8 Usage policy / license

PDB archive data and the RCSB APIs are **CC0 1.0 Universal**, with attribution
encouraged; data originating from integrated external resources carries that
provider's terms (which is one more reason CSMs are out of scope). The workspace
therefore shows an unobtrusive *"Data source: RCSB PDB"* attribution, and no
copyrighted RCSB editorial text is copied into the application. Provider terms
presentation is allowed to be provider-specific; capability semantics are not.

### 10.9 PDBx/mmCIF

`https://mmcif.wwpdb.org/` — *"PDBx/mmCIF became the standard PDB archive format in
2014"* (current dictionary V5.0, `mmcif_pdbx_v50.dic`). It is the ONE coordinate
format Phase 15 imports.

---

## 11. Network and trust boundary

The driver uses **three fixed upstream hosts** and no caller-controlled network
shape. Caller/model input may provide only `query`, `limit`, `authority`, and
`native_id`.

It may NOT provide a URL, host, scheme, port, proxy, redirect target, HTTP method,
GraphQL endpoint, download path, or Search API JSON. Concretely
(`revolab/drivers/rcsb.py`):

```text
base_url      = one process constant per host, never per request
path          = a module constant, or `download/{entry_id}.cif` formatted from an
                identifier that ALREADY passed the official grammar
GraphQL       = a module document with the entry ids bound as VARIABLES
params/body   = serialized by httpx, never concatenated into a URL
follow_redirects = False
trust_env     = False   (ambient HTTPS_PROXY/ALL_PROXY/~/.netrc ignored)
timeout       = bounded connect/read
API body      = bounded while STREAMING (2 MiB), then closed exactly once
coordinates   = bounded while STREAMING (128 MiB), then closed exactly once
```

Because redirects are off and every identifier is grammar-validated first, there is
no SSRF surface: an unexpected redirect is a typed failure, never a transport
detail to follow.

**Coordinate format guard.** The downloaded body passes a bounded *signature check*
(`data_` must begin the data block after optional BOM/whitespace). This is NOT a
parser: it rejects an HTML/error page served with a 200 and a truncated or garbage
payload, and NO scientific field is ever read from the coordinate file — every
scientific value comes from the Data API. No home-grown CIF parser is written and no
structural-biology parsing stack is added speculatively.

Provider text (titles, methods) is normalized into **bounded inert text** by the
shared neutral helper `capabilities.bounded_inert_text`. It can never modify Agent
instructions or the ToolCatalog, authorize an import, create Evidence, submit
compute, be rendered as HTML, or be used as SQL. It is data.

---

## 12. Bounds (single canonical values)

`revolab.capabilities` owns the ceilings; the driver enforces them at the wire
boundary and the application service re-applies them to the projection, so a
mis-wired driver cannot widen the frontend or Agent surface.

```text
query characters             300
result count                 1..20 (default 10)
title                        500
method                       200 per method, at most 8 methods
revision text                100
authority                    100   (= persisted column width)
native_id                    300   (= persisted column width)
API response bytes           2 MiB (checked while streaming)
coordinate bytes             128 MiB = 134 217 728 (checked while streaming)
timeout                      bounded connect/read (default 15 s)
```

### The coordinate ceiling

`MAX_STRUCTURE_COORDINATE_BYTES = 134_217_728` (128 MiB). Observed archive entry
files are well under a megabyte for ordinary proteins and tens of MiB for the
largest ribosomal/viral assemblies, so this is **comfortably above the largest
deposited entry coordinate file** while bounding the memory and time a single import
can consume. A structure larger than the ceiling **fails explicitly**; coordinates
are never truncated. (The bounded read implies a transient peak of roughly twice the
ceiling, which is acceptable for one explicit human import.)

### Method normalization

Multiple deposited experimental methods are bounded to
`MAX_STRUCTURE_METHODS` (8), de-duplicated case-insensitively, and **sorted**, then
joined with `"; "`. The persisted representation therefore never depends on
arbitrary provider ordering, and an absurd method list fails closed.

---

## 13. Import semantics

### 13.1 The request carries stable identity only

```text
provider_key, authority, native_id
```

`extra="forbid"`. The browser never sends a title, method, resolution, coordinate
URL, coordinate bytes, PDB version, or checksum — those would be tamperable client
data, and a provider whose answer could be overridden by the client would be
decorative. The server re-resolves the identity at the CURRENT provider and builds
the canonical normalized scientific snapshot **server-side**.

### 13.2 The flow

```text
explicit human Import
    ↓  current Project MUTATION authority (owner/member, active Project)
    ↓  current Provider availability (READY driver, policy, credentials)
    ↓  CURRENT provider resolve(authority, native_id)   [metadata + mmCIF bytes]
    ↓  validate the resolved identity (provider, authority, alias-aware native_id)
    ↓  validate + normalize the snapshot (resolution, methods, bounds, registry)
    ↓  ContentStore byte custody (immutable, content-addressed)
    ↓  ATOMIC persistence of the whole database bundle
```

### 13.3 What is created, atomically

```text
ContentStore blob                                     (immutable, OUTSIDE the DB txn)
ArtifactReference(authority=revolab)                  (1)
ExternalIdentity(pdb, <entry>)                        (1)
ExternalReference                                     (1)
StructureSeries + StructureRevision                   (1 + 1)
ScientificObjectExternalIdentity                      (1: identity)
ExternalReference --imported_as--> StructureRevision  (1 edge)
ArtifactReference --imported_as--> StructureRevision  (1 edge)
ProjectResourceLink                                   (4)
new-resource ResourceStewardship                      (3: series + reference + artifact)
```

If any database step fails, **none** of the local durable import remains: no orphan
series, revision, identity mapping, reference, artifact, link, stewardship, or
provenance edge. The whole bundle is one caller-owned transaction, and the only
commit happens once at the end.

### 13.4 ContentStore and PostgreSQL are NOT one distributed transaction

This is stated honestly rather than papered over:

```text
ContentStore byte custody  !=  PostgreSQL transactional truth
```

The coordinate bytes are content-addressed **before** the database transaction
commits, so a later DB rollback may leave an **unreachable content-addressed blob**
behind. Because the store is immutable and content-addressed:

* the orphan is **not visible Project truth** (no DB row and no Project link points
  at it);
* it is **safely reused** by a later successful import of the same bytes;
* a future garbage collector may clean unreachable blobs if that is ever needed.

No two-phase commit is invented, and no test pretends the filesystem write rolled
back. **What is guaranteed is the absence of partial DATABASE truth.**

### 13.5 Repeat import is idempotent

Importing the same unchanged entry repeatedly into the same Project creates no
duplicate identity, reference, artifact, series, revision, mapping, or provenance
edge. The canonical existing bundle is returned. The repeat path also **never writes
a new blob**: it re-downloads into bounded memory, recomputes the checksum, compares
it to the stored snapshot, and only then reuses the existing bundle — so an
already-imported entry never produces an unnecessary orphan write.

### 13.6 Cross-Project import reuses global identity without transferring stewardship

Project A imports `pdb:<entry>`; Project B imports the same current snapshot and
receives links to the SAME `StructureSeries`, `StructureRevision`,
`ExternalReference`, and internal `ArtifactReference`. No copied ScientificObjects,
no duplicate `ExternalIdentity`, no duplicate bytes. Project B gains only its own
read lens: `ProjectResourceLink` grants visibility, `ResourceStewardship` alone
authorizes mutation, so **the second Project never steals stewardship**.

### 13.7 A changed external entry fails closed

When an `ExternalIdentity` already maps to an imported Structure, Phase 15:

1. re-resolves the CURRENT provider record (including the coordinate bytes);
2. computes the normalized current snapshot checksum;
3. compares it against the imported snapshot provenance.

If unchanged, the existing import is reused/linked. If **any** normalized scientific
content changed — the coordinate byte checksum, the method, or the resolution — it
**fails closed with a typed conflict**:

```text
PDB entry has changed since the imported snapshot. Refresh/re-import revision
semantics are not implemented in Phase 15.
```

It never silently mutates a revision, appends a revision, relabels an object,
replaces the coordinate ArtifactReference, renames the Structure, or updates the
`ExternalReference`.

### 13.8 Presentation-only drift is idempotent

The digest covers only `{structure_payload, coordinate_checksum}`, so a
**title-only** change is presentation drift: the re-import succeeds idempotently and
the stored series name is never rewritten. Its one user-visible consequence is that
`project.search` matches the **stored** name, so after an upstream rename the object
remains reliably findable by its PDB ID but not by the new title until a steward
renames the series through the existing mutable `update_series` operation. This is
bounded presentation staleness, never silent corruption: no scientific content,
identity, mapping, or provenance changes.

### 13.9 An incomplete or incompatible existing mapping fails closed

The trusted importer never hijacks or silently augments a global mapping it did not
create. If the identity already exists but is **not** a complete, compatible Phase-15
bundle, the import returns a typed conflict and leaves durable state unchanged.
Automatic repair is an explicit deferral. The detected states include:

```text
a manual `identity` mapping        a missing coordinate artifact
a wrong ObjectType                 a missing ExternalReference snapshot
a disagreeing identity `kind`      more than one source snapshot
a non-canonical qualifier mapping  a snapshot digest that disagrees with the stored
a retired (archived) series        revision payload and artifact checksum
a missing `imported_as` edge       a provider-authority coordinate artifact
```

The checksum re-computation matters: the digest is defined over the stored payload
and the stored artifact checksum, so trusting it without recomputing it would let a
digest/revision disagreement present stale content as current.

### 13.10 Concurrency

Database uniqueness is the backstop, and PostgreSQL is the acceptance truth.

* The `ExternalIdentity` insert is the FIRST durable write of the bundle and is
  protected by `uq_external_identity_authority_native`, so it is the **primary
  scientific-identity linearization point** for a first import. It is deliberately
  **insert-only** (`scientific_object.create_external_identity`) rather than
  get-or-create, so a racing creator always loses on the constraint rather than
  building a second bundle. A loser rolls back, re-reads the committed winner,
  validates the complete compatible bundle, and reuses it.
* Phase 15 adds a SECOND possible race: the internal content-addressed
  `ArtifactReference(authority=revolab, native_id=<checksum>)`. The shared internal
  artifact get-or-create (`services._persist_artifact_reference_trusted` →
  `provenance.create_artifact_reference_row_if_absent`) was therefore **hardened for
  every existing caller**: the row is created by ONE dialect-level
  *insert-if-absent* statement (`ON CONFLICT DO NOTHING` on
  `uq_artifact_identity`), so two concurrent creators converge on ONE artifact and
  the loser's transaction is **never aborted**.
  *Why not `IntegrityError` recovery:* catching the violation would poison the
  caller's PostgreSQL transaction, which would destroy unrelated work for a caller
  that composed the create with `commit=False` (the Local Tool Runtime's derived
  result, or the scientific import bundle). A `Session.begin_nested()` savepoint is
  not a portable substitute (it is unreliable on the SQLite substrate). The atomic
  insert avoids the failure entirely, and it introduced its own regression coverage.
  A lost race cleans up its own unused `GlobalResourceRegistry` row, so no orphan
  registry entry remains.
* Linking an already-existing bundle into a Project is idempotent and
  SAVEPOINT-guarded per link, so a concurrent duplicate resolves to the committed
  winner without damaging the rest of the bundle.

A raw `IntegrityError` never reaches a caller, no orphan loser objects remain, and
no duplicate global series or artifact is created.

### 13.11 Who becomes steward

The importing Project becomes steward of the newly created **StructureSeries**, the
`ExternalReference`, and the internal coordinate `ArtifactReference`. Never the
user, never the provider, and never the `ExternalIdentity` (the identity registry is
global and is not Project-owned).

---

## 14. `StructureCandidate` vs imported truth, and Import vs Evidence

Import creates **facts and provenance**, not interpretation:

```text
after import:  Evidence count unchanged
               Decision count unchanged
```

Phase 15 never claims "PDB supports this structure" as Evidence, never creates a
Decision or a Decision draft, and never auto-cites the associated publication (Phase
13 owns explicit literature discovery/import). A later human or Agent may explicitly
create Evidence using the existing canonical operation.

Project Search integration is automatic and free:

```text
before import:  project.search("<PDB id>") -> no imported Structure hit
after import:   project.search("<PDB id>") -> Structure (+ provenance) hits
                project.search("<stored name>") -> Structure series
```

Import does **not** add anything to Agent context. The existing flow is unchanged:
`import → canonical objects → Project Search → explicit Add to Agent context →
ContextSelection → ContextBuilder`. Coordinate bytes are NEVER injected into a turn:
they remain ContentStore artifact bytes reachable through the canonical artifact
boundaries.

---

## 15. The Agent boundary

Phase 15 projects exactly ONE Agent-facing Tool through the existing ToolCatalog:

```text
{provider}.structure.search          e.g. rcsb.structure.search
    source            = provider
    execution_class   = remote
    side_effect_class = read_only
    autonomy          = automatic
    input             = {query, limit}   (extra=forbid)
```

No `url`, `host`, `graphql`, `import`, `project` target, or coordinate field exists,
and the Tool calls the SAME application discovery service as the human UI. The Agent
may search, inspect bounded candidates, reason over title/method/resolution, and
recommend a PDB entry for import. It may **not** download or import coordinates, may
not create an `ExternalIdentity`, `ExternalReference`, `ArtifactReference`,
ScientificObject, or Evidence, and may not execute an Import: there is deliberately
**no** `structure.import` Tool, and `ActionRequest` is deliberately **not**
generalized.

External candidate text is untrusted Agent data. A hostile title is inert:
regressions prove it cannot change instructions, widen Tool autonomy, trigger an
Import or an `ActionRequest`, reveal credentials, or modify context.

---

## 16. Capability and catalog

Phase 15 adds exactly ONE Core-owned capability kind:

```text
CapabilityKind.STRUCTURE_DISCOVERY = "structure_discovery"    (read-only)
```

It is added because a real provider now realizes it, it is provider-neutral, and it
is NOT persisted in the database (capability kind is projected at runtime from the
driver's capability map). `STRUCTURE_DISCOVERY` is added to
`READ_ONLY_CAPABILITY_KINDS`, so any readable membership may discover while only
owner/member may import.

The provider-neutral protocol is the smallest one the real use case forces:

```text
StructureDiscoveryCapability
    search(query, limit, credential_lease) -> StructureSearchResult
    resolve(authority, native_id, credential_lease) -> ResolvedStructureRecord
```

`resolve` returns the coordinate bytes because an explicit import must take custody
of exactly what the CURRENT provider served; `search` never downloads coordinates.
No `StructuralBiologyCapability`, `CoordinateCapability`, `MolecularDatabaseCapability`,
or `UniversalEntityCapability` is introduced without a second concrete use case.

The first concrete provider:

```text
provider_key = "rcsb"
display_name = "RCSB PDB"
authority    = "pdb"
```

`provider_key != authority` here, and the implementation keeps the concepts
separate. A load-bearing regression registers a different resolver
(`provider_key = "pdbmirror"`, `authority = "pdb"`) and proves that importing through
it creates `ExternalIdentity(pdb, …)` — never `ExternalIdentity(pdbmirror, …)` — with
the resolver recorded only as `cache_metadata.resolver_provider`.

Registration: the public RCSB APIs need no credential and no operator identity, but
the real remote driver is installed only when the deployment sets
`REVOLAB_RCSB_DISCOVERY_ENABLED` — exactly like the NCBI/UniProt providers. A
zero-config deployment keeps the Provider Catalog an honest empty set, and
CI/browser slices never perform a live RCSB request. They opt into an in-process fake
that claims its OWN `fakepdb` authority, so a synthetic fixture identity can never be
mistaken for a real PDB entry; the real `pdb` namespace is guarded by (a) the
driver-registry authority-collision check, which refuses any SECOND resolver claiming
`pdb` alongside the real driver, and (b) the production refusal — the fake is never
installed when `environment == "production"`.

---

## 17. Presentation

The workspace integrates external structure discovery with the existing Objects
workspace as compact panels rather than a new top-level surface or a generic
external-database dashboard:

```text
Objects
├ Project objects      (what REvoLab already knows)
├ Discover proteins    (external discovery + explicit import)
└ Discover structures  (external discovery + explicit import)
```

Candidates are presented with PDB ID, title, method, resolution, and release date,
carry the explicit badge `external · not yet in Project`, and offer
`Import to Project`. After an import the surface shows `Imported to Project`,
`Open Structure`, and `Coordinate artifact available`, linking to the canonical
Structure through the EXISTING object-detail surface. A viewer may search but is
offered no Import control, and the backend independently refuses a direct viewer
import with `403`.

The Structure detail derives the coordinate artifact by **provenance traversal** (the
`imported_as` edge from the artifact to the revision) and reads the artifact's
checksum/size/content type from the existing resource surface. No second
`coordinates_ref` truth is persisted for UI convenience, no raw coordinate text is
rendered, and there is **no second Structure detail implementation**. Provider
selection comes from the Provider Catalog filtered by the backend-owned
`structure_discovery` capability kind; the frontend never writes
`if provider.key === "rcsb"` to decide capability behavior. The only
provider-specific code is the legal attribution of the data source, which is
presentation, not capability semantics.

**No molecular viewer.** Mol\*, NGL, PyMOL embedding, and any 3D canvas are
deliberately absent: this phase establishes durable Structure/coordinate truth, and
visualization is a separate concern that can later consume the canonical
`ArtifactReference`.

---

## 18. Explicit deferrals (not silently postponed)

```text
Structure refresh -> new revision semantics
obsolete / superseded PDB reconciliation
PDB entry alias reconciliation beyond the documented legacy<->extended alias
integrative / hybrid (IHM) PDB entries
UniProt <-> PDB mapping
polymer entity import
chain / asym-unit objects
biological assemblies (assembly1.cif)
Complex objects
ligand extraction / chemical component import / ligand_ref population
structure factors, EM maps, NMR restraints, validation reports
associated publication auto-import
legacy PDB format, BCIF, XML
mmCIF parsing, structure parsing, feature extraction, structure comparison/alignment
Mol* / 3D visualization
REvoDesign handoff, PyMOL integration
RAG / vector retrieval / embeddings / semantic memory
an Agent import Tool, automatic import, ActionRequest generalization
background syncing, crawling, a PDB mirror/cache, provider cache tables
```

Nothing is silently postponed: each is a named future design question with its own
authority semantics.

---

## 19. Verification

Machine gates (see `IMPLEMENTATION_STATE.md` for the recorded evidence):

* deterministic RCSB driver tests over an injected `httpx.MockTransport` covering
  the fixed-host contract, the bounded Search query translation, batched GraphQL
  metadata, both Search result shapes, HTTP 204, every status class, redirects,
  timeouts, oversized bodies, empty/oversized/non-mmCIF coordinates, the identifier
  grammar (legacy, extended, extended-only, malformed, CSM), and the alias
  normalization — CI never touches live RCSB;
* SQLite application/API/Agent regressions and the PostgreSQL 16 acceptance suite
  (initial import, repeat import, cross-Project reuse, same-Project and cross-Project
  first-import races, the **content-only artifact race**, atomic rollback,
  changed-snapshot conflict, incomplete-mapping conflict, Project Search integration,
  and authorization negatives);
* `alembic check` drift-clean on SQLite and PostgreSQL 16 with **no new migration**
  (Phase 15 adds no table and no column);
* regenerated OpenAPI + frontend contracts (idempotent);
* frontend typecheck/test/build/`check:contracts`;
* a Playwright browser vertical slice (discovery → external labelling → Import →
  Structure → coordinate artifact → Project Search → Agent context) plus the browser
  negatives (viewer cannot import, provider outage, hostile title inert, repeat
  import idempotent, changed snapshot reports a conflict, oversized structure yields
  a safe error).
