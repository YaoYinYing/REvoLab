# ADR-0021: External Structure Import Takes Custody of Immutable Coordinate Snapshots

> **Status: Proposed — pending human acceptance.**
> Accepted only by explicit human acceptance at PR review; a green test suite does
> not promote this ADR's status.

## Context

Phases 1–14 established canonical Project truth, the two ways to *use* it
(`ContextSelection` → `ContextBuilder`, and Phase-12 Project Search over what
REvoLab already knows), the Provider / Capability boundary (ADR-0012), and two
external-knowledge slices: discover a publication externally, import it explicitly,
and interpret it separately (Phase 13, ADR-0019), and resolve a protein plus its
exact canonical amino-acid sequence into immutable ScientificObjects (Phase 14,
ADR-0020).

Both earlier slices imported something REvoLab can represent as **structured text**:
a citation identity card, or an amino-acid string. Phase 15 must import something
categorically different — a **3D structure**, whose scientific content is a large
**binary-ish coordinate file** that is expensive to fetch, that a remote archive can
silently revise, and that REvoLab must still be able to read after the provider is
gone. That raises questions neither earlier slice had to answer:

- what is the durable scientific identity of a PDB entry, and is the resolver that
  answered it the same thing;
- if the *content* is a coordinate file, does REvoLab reference it at the provider
  or take custody of it;
- if REvoLab takes custody, what does REvoLab-owned byte custody mean about
  scientific origin;
- how does a Structure relate to the bytes it was built from, and to the external
  source, without either fact duplicating the other;
- what happens when the archive entry changes after an import;
- how do repeat, cross-Project, and concurrent imports behave when a large immutable
  blob store sits outside the database transaction;
- which coordinate format is canonical.

The obvious shortcuts are all unsafe:

- storing the coordinate bytes (or a path to them) inside the `structure` payload
  would put an unbounded external blob into a typed scientific payload and duplicate
  a relationship the provenance graph already owns;
- storing `pdb_id` in the payload would create a second, non-authoritative
  representation of durable identity that `ExternalIdentity` already owns;
- referencing the coordinates only as an `ArtifactReference(authority="pdb")` would
  leave every imported Structure dependent on live RCSB availability and on the
  current, mutable archive state;
- letting the browser or the Agent supply the coordinates, title, method, or
  resolution would make the provider decorative and the client authoritative;
- re-resolving and overwriting, or appending a revision, whenever the archive entry
  differs would let a remote archive silently redefine Project truth and would answer
  the refresh/identity question by accident;
- pretending the blob store and PostgreSQL form one distributed transaction would be
  a false atomicity claim;
- giving the Agent an import Tool would cross the human authority boundary for a case
  whose semantics are not yet designed.

What was not yet decided is **what an external structure import creates, which
identity is durable, who owns the coordinate bytes, how scientific origin is kept
separate from byte custody, how a Structure relates to both an external source and
its exact bytes, how repeat/cross-Project/concurrent imports behave, and what happens
when the archive entry changes**.

## Decision

### External structure import is an application sub-boundary, not a tenth Core domain

`revolab.structures` is an application-level discovery/import service consumed by
Presentation and Agent Context — the same placement family as `revolab.proteins`,
`revolab.literature`, `revolab.search`, `revolab.notes`, and `revolab.actions`. It
adds no Core domain and no Project → domain edge; the accepted nine-domain DAG is
unchanged. Provider-neutral value objects live in `revolab.capabilities` (value
objects + the Protocol) and `revolab.domain.discovery` (the single invocation gate),
and provider-specific HTTP vocabulary lives only in `revolab.drivers.rcsb`.

### One new Core capability kind, forced by a real provider

`CapabilityKind.STRUCTURE_DISCOVERY = "structure_discovery"` is added because a real
provider (RCSB PDB) now realizes it, with the smallest provider-neutral Protocol the
use case forces:

```text
StructureDiscoveryCapability
    search(query, limit, credential_lease) -> StructureSearchResult
    resolve(authority, native_id, credential_lease) -> ResolvedStructureRecord
```

`search` is a bounded read that DOWNLOADS NO COORDINATES; `resolve` re-reads ONE
archive entry and ALSO returns that entry's canonical coordinate bytes, because an
explicit import must take custody of exactly what the CURRENT provider served. It is
a **read-only** kind added to `READ_ONLY_CAPABILITY_KINDS`, so any readable
membership may discover while only owner/member may import. Capability kind remains
**not persisted**, so this needs no migration.

No `StructuralBiologyCapability`, `CoordinateCapability`, `MolecularDatabaseCapability`,
or `UniversalEntityCapability` is introduced.

### A `StructureCandidate` is ephemeral, bounded, provider-neutral, and byte-free

It carries `provider_key`, `authority`, `native_id`, `title`, `experimental_methods`,
`resolution_angstrom`, `release_date`, and `polymer_entity_count` — bounded
presentation fields only, with no `metadata: dict` escape hatch and **no coordinate
bytes**. It is never persisted, never a `SearchHit`, never Project truth, and it never
carries raw provider JSON or provider field names. Discovery persists nothing, needs
no candidate cache table, and never downloads a coordinate file.

### The durable identity is ONE fixed canonical form of the PDB archive entry id

```text
ExternalIdentity(authority="pdb", native_id=<durable PDB entry id>, kind="structure")
```

Both CURRENT official identifier forms are supported and ACCEPTED, because PDB
identifiers are **not permanently four characters**:

```text
classic  4 characters, first a digit            e.g. 4HHB
extended "pdb_" + 8 alphanumerics (12 chars)    e.g. pdb_00004hhb, pdb_10021abc
```

But "accepted" is not "durable". The durable form is ONE fixed normalization, applied
before EVERY `ExternalIdentity` lookup/create and before the imported identity is
returned, so it cannot depend on whichever official spelling a particular provider
response happens to use — or on the wwPDB transition from classic to extended primary
ids:

```text
1abc / 1ABC / 1AbC   -> pdb_00001abc     (a classic id is PROMOTED to its documented alias)
pdb_00001abc         -> pdb_00001abc     (unchanged)
pdb_1abc5678         -> pdb_1abc5678     (extended-only: unchanged)
```

Promotion is chosen over demotion because the fixed point of the normalization is the
extended form, which is where the archive is heading; the durable identity therefore
survives the transition in both directions. The rule is owned by the neutral capability
leaf (`durable_pdb_entry_id`) and applied by BOTH the driver and the application import
boundary, so a mis-wired or future resolver cannot mint a second identity for the same
entry either.

Never an entry title, method, resolution, URL, filename, provider response id, or
resolver key. A named deferral remains: reconciliation of an identity asserted under a
NON-canonical spelling through the pre-existing generic identity surface (identifier-
spelling reconciliation), exactly as Phase 14 named its analogous extra-snapshot
deferral rather than changing that surface's authority.

Computed Structure Models (`AF_…`/`MA_…`, or
`structure_determination_methodology == "computational"`) and integrative/hybrid
(IHM) entries are excluded from discovery and rejected at resolution, so **a computed
model can never receive `authority = "pdb"`**.

### `authority` is not the resolver, and `provider_key == authority` is false here

The resolver is `rcsb`; the durable authority is `pdb`. Core and the application
never assume they are the same: the resolver is recorded only as
`ExternalReference.cache_metadata.resolver_provider`. A load-bearing regression
registers a *different* resolver (`pdbmirror`) for the **same** `pdb` authority and
proves the import still creates `ExternalIdentity(pdb, …)`.

### ONE Structure ScientificObject, with a minimal payload

A PDB archive entry becomes exactly one object:

```text
Structure (ObjectType.STRUCTURE)   payload {resolution, method,
                                            pdb_id: null, coordinates_ref: null,
                                            ligand_ref: null}
```

It gains exactly one immutable initial Revision, validated by the existing Core type
registry, with REvoLab's EXISTING canonical payload checksum. Only the two scientific
fields that belong to the Structure snapshot are persisted. The three pre-existing
bootstrap fields are deliberately **null** because each would duplicate a fact
another part of the graph already owns:

```text
pdb_id          -> ExternalIdentity(pdb, native_id) already owns durable identity
coordinates_ref -> the typed `imported_as` edge to the ArtifactReference already
                   owns coordinate linkage
ligand_ref      -> ligand semantics are a later structure-content phase
```

Multiple deposited experimental methods are de-duplicated, SORTED, and joined
deterministically, so the persisted method never depends on arbitrary provider
ordering. `resolution` is a finite positive Å value or absent — enforced by the
**Core type registry**, so manual creation, revision appends, and imports all obey
one rule and no resolution is ever invented for NMR/integrative structures.

Absent and malformed are kept distinct at the wire boundary, because collapsing them
would launder impossible provider data into a valid snapshot: `null`/`[]` (and an
all-null array) mean "no resolution applies", while a scalar where the documented
`[Float]` array is required — or any non-null element that is not a finite positive
angstrom value (`["x"]`, `[0]`, `[-1.0]`, `[NaN]`) — fails closed as a typed
`CapabilityError` on both the discovery and resolving paths. A null element beside a
real value is skipped, so a legitimate result is never rejected.

### The exact coordinates imported are the canonical archive entry file, in PDBx/mmCIF

```text
GET https://files.rcsb.org/download/{entry_id}.cif
```

The **uncompressed PDBx/mmCIF** entry coordinate file, and only that. PDBx/mmCIF is
the current archive standard (it *"became the standard PDB archive format in 2014"*),
large structures may not fit the legacy format, extended identifiers will not support
the legacy format, and one canonical coordinate representation avoids duplicate
truth. The biological assembly (`-assembly1.cif`), legacy `.pdb`, `.xml`, `.bcif`,
header-only files, structure factors, maps, and restraints are deliberately NOT
imported: **Phase 15's Structure is the deposited/archive entry coordinate model,
not a biological-assembly interpretation.**

The download is bounded while streaming (`MAX_STRUCTURE_COORDINATE_BYTES`, 128 MiB —
comfortably above the largest deposited entry while bounding one import). An
over-ceiling structure **fails explicitly**; coordinates are never truncated. An
empty body, a non-success status, a transport failure, or a body that fails a
bounded `data_` signature check all fail closed.

### REvoLab takes byte custody: ContentStore owns the imported bytes

```text
ContentStore = immutable, content-addressed byte custody owned by REvoLab
```

This covers user uploads, persisted local-tool outputs, and **explicitly imported
static external data snapshots**. It does NOT mean REvoLab scientifically originated
the bytes. ContentStore is deliberately NOT turned into a remote cache, an HTTP cache,
or a provider mirror; no second blob store is invented, and the existing canonical
internal-artifact path is reused.

### `authority=revolab` means byte custody, never scientific origin

```text
ArtifactReference(
    authority    = "revolab",
    native_id    = the content checksum (the store handle),
    checksum     = the same byte checksum,
    size         = the exact size,
    content_type = "chemical/x-cif"
)
```

`authority="revolab"` says only: **REvoLab can reproduce these exact bytes.** It never
means REvoLab authored, curated, or scientifically originated them. An
`ArtifactReference(authority="pdb")` would leave the imported Structure dependent on
live provider availability and current archive state, so it is not the imported byte
truth.

The canonical media type is a REvoLab assertion, not a copied transport header: the
RCSB file-download documentation says the generic "download" URL sets
`Content-Type: application/octet-stream` (the exact `.cif` URL actually answers
`chemical/x-cif`), and either value describes transport, not science.

An imported Structure's coordinate artifact ALWAYS carries this canonical type, and the
create path and the reuse validator agree on it. Because the shared
`assert_reference_compatible` rule treats a `NULL` media type as "no assertion",
byte-identical content already stored with a browser-supplied or absent media type is
refused BEFORE the first durable write with a typed conflict, rather than persisting a
mislabeled artifact that no later re-import could accept. The shared rule is deliberately
not relaxed for this. `chemical/x-cif` is the de-facto community media type for
CIF-family files (and is what the RCSB file service actually serves). A file
extension or path is never durable identity.

### Scientific origin is represented separately, by identity + snapshot + provenance

```text
ExternalReference:
    external_identity_id = the durable `pdb` identity
    checksum             = sha256({structure_payload, coordinate_checksum})
    as_of                = resolution timestamp
    cache_metadata       = {resolver_provider, pdb_revision_major?,
                            pdb_revision_minor?, pdb_revision_date?}
```

Because the digest covers only NORMALIZED payloads plus the coordinate byte checksum,
changing upstream response formatting — or a title — can never change scientific
snapshot identity. The digest is snapshot identity, **not proof of origin**; origin is
the `ExternalIdentity` + `ExternalReference` + `imported_as` chain. Revision metadata
is useful provenance, never durability: durability comes from the ContentStore
checksum, not from assuming an old remote URL stays available.

### Both references relate to the StructureRevision through the frozen `imported_as` grammar

```text
ExternalReference --imported_as-->  StructureRevision     (#7)
ArtifactReference --imported_as-->  StructureRevision     (#7)
```

The two edges express two DIFFERENT truths about the same revision: the scientific
external source, and the exact bytes used as coordinate content. No
`has_coordinates`/`from_pdb`/`coordinate_file_of`/`downloaded_from` `RelationType` is
invented. One `ExternalIdentity` maps `qualifier="identity"`, `is_canonical=true` to
the `StructureSeries`; there is no second `ExternalIdentity` for the coordinate file,
because a file is content, not a scientific entry identity.

### Import requests carry stable identity only, and the snapshot is built server-side

```text
provider_key, authority, native_id      (extra="forbid")
```

The server re-resolves the identity at the CURRENT provider, verifies the returned
identity matches the request (case- and alias-aware), validates and normalizes the
resolution/methods/bounds against the Core registry, and builds the canonical
scientific snapshot itself. No title, method, resolution, coordinate URL, coordinate
bytes, PDB version, or checksum is ever accepted from the client.

### The initial import is ONE atomic database transaction

The whole database bundle — `ExternalIdentity`, `ExternalReference`, the internal
`ArtifactReference`, the `StructureSeries`, the `StructureRevision`, the identity
mapping, both `imported_as` edges, four `ProjectResourceLink`s, and new-resource
`ResourceStewardship` — commits exactly once. If any database step fails, NONE of the
local durable import remains.

### ContentStore and PostgreSQL are NOT one distributed transaction, and that is stated honestly

The coordinate bytes are content-addressed BEFORE the database commit, so a later DB
rollback may leave an **unreachable content-addressed blob** behind. Because the store
is immutable and content-addressed, that orphan is not visible Project truth, is
safely reused by a later successful import of the same bytes, and may be cleaned by a
future garbage collector. No two-phase commit is invented and no test pretends the
filesystem write rolled back; what is guaranteed is the absence of partial DATABASE
truth.

### Repeat import is idempotent, and the existing path never writes a blob

A repeat import of an unchanged entry returns the canonical existing bundle and
creates nothing. The repeat path re-downloads into bounded memory, recomputes the
checksum, compares it against the stored snapshot, and only then reuses the existing
bundle — so an already-imported entry never produces an unnecessary orphan write.

### Cross-Project import reuses global custody without transferring stewardship

A second Project receives links to the SAME series, revision, `ExternalReference`, and
internal `ArtifactReference`. No copied objects, no duplicate identity, no duplicate
bytes. It gains only a read lens: `ProjectResourceLink` grants visibility and
`ResourceStewardship` alone authorizes mutation, so **stewardship is never stolen**.

### A changed entry fails closed; refresh is deliberately deferred

Re-resolving, recomputing the normalized checksum (which covers the coordinate bytes
AND the normalized payload), and comparing it against the imported snapshot either
confirms reuse or raises a typed conflict. Phase 15 NEVER silently mutates a revision,
appends a revision, relabels an object, replaces the coordinate artifact, renames the
Structure, or updates the `ExternalReference`. Because the frozen digest covers only
`{structure_payload, coordinate_checksum}`, a **title-only** change is presentation
drift: the re-import is idempotent and the stored series name is never rewritten.

An existing identity that is NOT a complete compatible bundle — a manual mapping, a
wrong object type, a disagreeing `kind`, a non-canonical mapping, a retired series, a
missing edge, a missing artifact, more than one source snapshot, a provider-authority
artifact, or a stored digest that disagrees with the stored payload and artifact
checksum — fails closed with a typed conflict and is never repaired automatically.

### PostgreSQL uniqueness is the concurrency backstop, and the shared artifact helper was hardened

The bundle is created in ONE transaction and the `ExternalIdentity` insert (the FIRST
durable write) is protected by `uq_external_identity_authority_native`, so it is the
**primary** linearization point. It is insert-only, so a racing creator loses on the
constraint rather than building a second bundle; the loser rolls back, re-reads the
committed winner, validates it, and reuses it.

Phase 15 adds a SECOND race — the internal content-addressed
`ArtifactReference(authority="revolab", native_id=<checksum>)`. The **shared** internal
artifact get-or-create was therefore hardened for every existing caller with ONE
dialect-level *insert-if-absent* statement (`ON CONFLICT DO NOTHING` on
`uq_artifact_identity`), so two concurrent creators converge on ONE artifact and the
loser's transaction is never aborted. `IntegrityError` recovery was rejected because
it poisons the caller's PostgreSQL transaction and would destroy unrelated work for a
`commit=False` composition (the Local Tool Runtime's derived result, or the import
bundle); a `Session.begin_nested()` savepoint is not a portable substitute because it
is unreliable on the SQLite substrate. A lost race cleans up its own unused
`GlobalResourceRegistry` row. Linking an existing bundle is idempotent and
SAVEPOINT-guarded per link.

### The importing Project is the steward of the new objects

Steward of the new `StructureSeries`, the `ExternalReference`, and the internal
coordinate `ArtifactReference`. Never the user, never the provider, and never the
`ExternalIdentity` (the global identity registry is not Project-owned).

### Import creates no Evidence and no Decision

An external archive record is a fact with provenance. Whether it *supports* a Project
conclusion is a separate human interpretation through the existing canonical Evidence
operation. Import never auto-cites an associated publication and never promotes a
Decision.

### Network shape is fixed and non-negotiable; no rate limit is invented

Three fixed documented hosts (`search.rcsb.org`, `data.rcsb.org`, `files.rcsb.org`);
caller input supplies only query/limit/authority/native_id; `trust_env=False`;
redirects OFF; bounded connect/read timeouts; API bodies bounded at 2 MiB and
coordinate bodies at 128 MiB while streaming; parameters encoded and GraphQL ids
bound as VARIABLES by the HTTP client. The official documentation publishes no
numeric quota — only *"we recommend starting with a handful of requests per second"*
and a `429` on excess — so exactly two bounded API requests are made per search and
one batched Data API request plus one static-file coordinate download per
import/resolve, with no retry, pagination, prefetch, or crawler, and `429`/`5xx`
become a typed retryable failure.

### The Agent may search, but can never import or take byte custody

Exactly one Tool is projected, `{provider}.structure.search`, with
`source=provider`, `execution_class=remote`, `side_effect_class=read_only`,
`autonomy=automatic`, input `{query, limit}` (extra=forbid), no
`url`/`host`/`graphql`/`import`/coordinate field, and it calls the SAME application
service as the human UI. There is deliberately NO `structure.import` Tool and
`ActionRequest` is deliberately NOT generalized. It downloads no coordinates.

### User-facing presentation stays a compact existing-surface treatment

The Objects workspace hosts a third panel (`Discover structures`); provider selection
is driven by the backend-owned `structure_discovery` capability kind, the only
provider-specific frontend code is legal data-source attribution, and the Structure
detail derives its coordinate artifact by provenance traversal. No 3D viewer, no
second Structure detail, and no raw coordinate rendering.

### No schema migration

Phase 15 adds no table and no column: `ExternalIdentity`, `ExternalReference`,
`ArtifactReference`, `ScientificObjectSeries`/`Revision`,
`ScientificObjectExternalIdentity`, `GlobalProvenanceEdge`, `ProjectResourceLink`,
and `ResourceStewardship` already exist, and `_GLOBAL_EDGE_SHAPE` already accepts an
`ARTIFACT_REFERENCE` source for `imported_as`. `alembic check` stays drift-clean on
SQLite and PostgreSQL 16.

## Consequences

- **Positive.** REvoLab gains durable, provider-independent custody of real 3D
  structural content — one immutable coordinate snapshot per imported entry — while
  keeping four truths separate: who identifies the structure, where it came from,
  which exact bytes REvoLab owns, and what ScientificObject those bytes represent.
- **Positive.** The Phase-13/14 pattern (ephemeral candidate → explicit human import
  → current re-resolution → one atomic bundle → typed provenance) is shown to
  generalize to a **large immutable byte** import without a new Core domain, new
  relation vocabulary, a second blob store, or a new retrieval system. The
  strengthen-the-shared-helper requirement also removed a latent concurrency defect
  from pre-existing callers.
- **Cost.** Byte custody is not a distributed transaction with PostgreSQL. An
  unreachable content-addressed blob can survive a rolled-back import. That is
  documented, bounded, reusable, and never visible Project truth — but it is a real
  non-atomicity that operators must know about.
- **Cost.** A coordinate ceiling exists. A structure larger than 128 MiB cannot be
  imported and fails explicitly rather than being truncated.
- **Cost.** Phase 15 imports a *snapshot*, so an imported Structure can diverge from
  the live archive entry. The divergence is visible (the stored snapshot checksum)
  and deliberately unrepairable until the refresh phase exists. A title-only change
  re-imports idempotently and leaves the stored name untouched, so the object is
  findable by PDB ID but not by a new title until a steward renames the series.
- **Cost.** The real remote provider is opt-in (`REVOLAB_RCSB_DISCOVERY_ENABLED`), so
  no CI or browser test ever performs a live RCSB request. A deployment must enable it
  deliberately.
- **Explicitly deferred** (and named, not silently postponed): refresh → new
  revisions, obsolete/superseded/alias reconciliation, integrative (IHM) entries,
  UniProt↔PDB mapping, polymer entities, chains, biological assemblies, Complex
  objects, ligand extraction, structure factors/maps/restraints/validation reports,
  associated publication auto-import, legacy PDB/BCIF/XML, mmCIF parsing, structure
  analysis/comparison, Mol*/3D visualization, REvoDesign/PyMOL handoff, RAG/vector
  retrieval, an Agent import Tool, automatic import, background sync/caching.

## Rejected alternatives

- Coordinates (or a coordinate path) inside the `structure` payload (unbounded
  external content in a typed payload; duplicates a relationship provenance owns).
- `pdb_id` inside the payload (a second, non-authoritative representation of durable
  identity).
- Referencing coordinates only as `ArtifactReference(authority="pdb")`
  (leaves the imported object dependent on live provider availability and mutable
  archive state).
- Storing the provider's `application/octet-stream` content type as the scientific
  media type (a transport description is not a scientific assertion).
- Accepting coordinates, title, method, or resolution from the browser or the model
  (makes the provider decorative and the client authoritative).
- Overwriting or refreshing an imported Structure on re-import (lets a remote archive
  silently redefine Project truth; makes provenance false).
- Appending a revision on any external difference (answers the refresh/identity
  question implicitly and silently).
- Persisting the raw provider payload or a candidate cache table (second stale source
  of truth; mirrors a database REvoLab does not own).
- Importing the biological assembly instead of the deposited entry model (assembly is
  an interpretation, not the archive entry).
- Importing legacy `.pdb` or conditionally choosing it (superseded standard;
  cannot represent large structures or extended identifiers).
- Auto-creating Protein/Sequence/Complex/Ligand objects, or auto-importing the
  associated publication (cross-domain side effects that need their own semantics).
- A home-grown mmCIF parser (re-reads fields the Data API already validated; adds a
  structural-biology stack speculatively).
- Treating the snapshot checksum as proof of origin (it proves snapshot content, not
  who said it).
- `IntegrityError`-catching recovery for the content-addressed artifact (poisons the
  caller's PostgreSQL transaction and breaks `commit=False` composition), or a
  `begin_nested()` savepoint shim (unreliable on the SQLite substrate).
- A non-atomic read-then-insert for the shared internal artifact identity (the
  pre-existing latent race this ADR removes).
- Inventing a numeric RCSB rate limit or a `Retry-After`-driven background retry
  scheduler (nothing is published; a made-up constant would be a false constraint).
- An Agent import Tool or a generalized `ActionRequest` (crosses the human authority
  boundary before its semantics are designed).
- A 3D viewer in this phase (durable coordinate truth is the deliverable; rendering
  can later consume the canonical `ArtifactReference`).
