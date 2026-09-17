# ADR-0020: External Protein Resolution Imports Immutable Scientific Snapshots

> **Status: Accepted** (Phase 14; human-accepted during PR #15 review; squash-merged
> into `main` as `93e2b221484446de5ca63db318a8099cc8ca799e`). The PR #15 human review
> and merge is the explicit acceptance event; a green test suite never promotes an
> ADR's status.

## Context

Phases 1–13 established canonical Project truth, the two ways to *use* it
(`ContextSelection` → `ContextBuilder`, and Phase-12 Project Search over what
REvoLab already knows), the Provider / Capability boundary (ADR-0012), and — in
Phase 13 — the first external-knowledge slice: discover a publication externally,
import it explicitly, and interpret it separately (`EXTERNAL_LITERATURE_DISCOVERY.md`,
ADR-0019).

Phase 13 imported a **reference identity card only**: a `LiteratureReference` is
`(authority, native_id)` plus presentation metadata, and the external system remains
the sole owner of the publication's content. Phase 14 must import something
categorically different — an entity whose *scientific content* REvoLab will hold and
reason over: a protein and its exact canonical amino-acid sequence. That raises
questions Phase 13 never had to answer:

- what exactly becomes a ScientificObject, and how many objects is "a protein";
- whether the external record's *content* is copied into REvoLab or referenced;
- what happens when the external record changes after an import;
- how an external accession relates to an existing manually-mapped object;
- which identity is durable when a resolver is not the authority.

The obvious shortcuts are all unsafe:

- storing the amino-acid sequence as a field on the `protein` payload would make the
  Protein a bag of metadata, destroy the `Sequence` ScientificObject's meaning, and
  put unbounded external content into a payload the Core registry does not bound;
- re-resolving and overwriting an imported object on every import would let a remote
  third party silently redefine Project truth and would make provenance a lie;
- appending a revision whenever the provider's record differs would answer the
  refresh/identity question implicitly and silently;
- persisting the raw provider payload (or a candidate cache) would create a second,
  stale source of truth and mirror a database REvoLab does not own;
- letting the browser or the Agent supply the scientific content would make the
  provider decorative and the client authoritative;
- giving the Agent an import Tool would cross the human authority boundary for a case
  whose semantics are not yet designed.

What was not yet decided is **what an external protein import creates, which identity
is durable, how a Protein relates to its Sequence, how the snapshot is provenanced,
how repeat/cross-Project/concurrent import behave, and what happens when the external
record changes**.

## Decision

### External protein import is an application sub-boundary, not a tenth Core domain

`revolab.proteins` is an application-level discovery/import service consumed by
Presentation and Agent Context — the same placement family as `revolab.literature`,
`revolab.search`, `revolab.notes`, and `revolab.actions`. It adds no Core domain and
no Project → domain edge; the accepted nine-domain DAG is unchanged. The
provider-neutral value objects live in `revolab.capabilities` (value objects + the
Protocol) and `revolab.domain.discovery` (the single invocation gate), and
provider-specific HTTP vocabulary lives only in `revolab.drivers.uniprot`.

### One new Core capability kind, forced by a real provider

`CapabilityKind.PROTEIN_DISCOVERY = "protein_discovery"` is added because a real
provider (UniProt) now realizes it, with the smallest provider-neutral Protocol the use
case forces:

```text
ProteinDiscoveryCapability
    search(query, limit, credential_lease) -> ProteinSearchResult
    resolve(authority, native_id, credential_lease) -> ResolvedProteinRecord
```

It is a **read-only** kind and is added to `READ_ONLY_CAPABILITY_KINDS`, so any
readable membership may discover while only owner/member may import. Capability kind
remains **not persisted** (it is projected at runtime from the driver capability map),
so this needs no migration.

No `BiologicalKnowledgeCapability`, `UniversalEntityCapability`, `OmicsCapability`,
`KnowledgeGraphCapability`, or `DatabaseCapability` is introduced.

### A `ProteinCandidate` is ephemeral, bounded, provider-neutral, and sequence-free

It carries `provider_key`, `authority`, `native_id`, `protein_name`, `gene_name`,
`organism_name`, `organism_id`, `sequence_length`, and `reviewed` — bounded
presentation fields only, with no `metadata: dict` escape hatch and **no canonical
sequence**. It is never persisted, never a `SearchHit`, never Project truth, and it
never carries raw provider JSON or provider field names. Discovery persists nothing
and needs no candidate cache table.

### The durable identity is the active primary UniProtKB accession

```text
ExternalIdentity(authority="uniprot", native_id=<primary accession>, kind="protein")
```

6-or-10-character uppercase alphanumerics validated by the official UniProtKB
grammar. Never an entry name, gene symbol, protein name, URL, provider response id, or
resolver key. Inactive/deleted/demerged and secondary accessions fail closed and are
**never silently remapped**; isoform identifiers (`P12345-2`) fail closed and are
**never silently stripped**. UniProt ID Mapping is out of scope.

### `authority` is not the resolver, and `provider_key == authority` is a coincidence

The first provider is `uniprot` and resolves the `uniprot` authority, but Core and the
application never assume they are the same: the resolver is recorded only as
`ExternalReference.cache_metadata.resolver_provider`. A load-bearing regression
registers a *different* resolver (`mirrorprotein`) for the **same** `uniprot`
authority and proves the import still creates `ExternalIdentity(uniprot, ...)`.

### Protein and Sequence are two ScientificObjects, not one payload

A canonical UniProt entry becomes:

```text
Protein  (ObjectType.PROTEIN)   payload {organism, source_sequence_ref: null, chain: null}
Sequence (ObjectType.SEQUENCE)  payload {kind: "protein", sequence: <canonical AA>}
```

Each gains exactly one immutable initial Revision, validated by the existing Core type
registry, with REvoLab's EXISTING canonical payload checksum — no second sequence
checksum field. The Protein payload deliberately does **not** carry the Sequence UUID:
the typed `represents` relation is the relationship truth, and duplicating it in a
payload string would create a second, non-authoritative representation of the same
fact. Protein ≠ Sequence because a sequence can be shared, corrected, or absent while
the protein concept persists.

### The canonical relation reuses the existing vocabulary

```text
SequenceSeries --represents--> ProteinSeries
```

Direction frozen. No `has_sequence`/`sequence_of`/`protein_sequence_of`
`RelationType` is invented.

### One ExternalIdentity, one semantic target per qualifier

```text
qualifier="identity", is_canonical=true -> ProteinSeries
qualifier="sequence", is_canonical=true -> SequenceSeries
```

Exactly one `ExternalIdentity` row per accession — never two.

### `ExternalReference` is immutable snapshot provenance, not a second identity card

It carries `external_identity_id`, a deterministic `checksum` of the **normalized
scientific bundle** (`{protein_payload, sequence_payload}`, sorted keys), `as_of`, and
bounded `cache_metadata` (`resolver_provider`, `source_release?`,
`source_release_date?`). Because the digest covers normalized payloads, a change in
upstream response *formatting* can never change scientific snapshot identity. The
checksum is snapshot identity, **not proof of origin**: origin is the ExternalIdentity
+ ExternalReference + `imported_as` provenance.

Raw provider payloads, annotations, feature tables, GO terms, cross-references,
comments, and whole HTTP header sets are never stored anywhere.

### Provenance is exactly three typed edges

```text
SequenceSeries    --represents-->  ProteinSeries       (#3)
ExternalReference --imported_as--> ProteinRevision     (#7)
ExternalReference --imported_as--> SequenceRevision    (#7)
```

`imported_as` is created through a NEW typed domain command
(`services.record_imported_as`) because the revisions are created by the import itself
and therefore cannot use the append-based `import_revision`. No generic relation writer
is introduced.

### Import requests carry stable identity only, and the snapshot is built server-side

```text
provider_key, authority, native_id      (extra="forbid")
```

The server re-resolves the identity at the CURRENT provider, verifies the returned
identity matches the request, validates and normalizes the sequence and bounds, and
builds the canonical scientific snapshot itself. No name, organism, reviewed status, or
sequence is ever accepted from the client.

### The initial import is ONE atomic transaction

The whole bundle — `ExternalIdentity`, `ExternalReference`, both series, both
revisions, both identity mappings, the `represents` edge, both `imported_as` edges,
five `ProjectResourceLink`s, and new-resource `ResourceStewardship` — commits exactly
once. If any step fails, NONE of the local durable import remains.

To make that possible without a generic writer, `persistence.insert_edge` and
`provenance.add_conceptual_edge` gain an explicit `commit: bool = True` keyword whose
default preserves every existing caller.

### Repeat import is idempotent; cross-Project import reuses global identity

A repeat import of an unchanged accession returns the canonical existing bundle and
creates nothing; the response echoes the STORED series name so it can never imply a
refresh. A second Project receives links to the SAME series/revisions/reference and
gains only a read lens: `ProjectResourceLink` grants visibility and
`ResourceStewardship` alone authorizes mutation, so **stewardship is never stolen**.

### A changed external record fails closed; refresh is deliberately deferred

Re-resolving, recomputing the normalized checksum, and comparing it against the
imported snapshot either confirms reuse or raises a typed conflict. Phase 14 NEVER
silently mutates a revision, appends a revision, relabels an object, replaces a
sequence, or links stale data as current. Because the frozen digest covers only
`{protein_payload, sequence_payload}`, a **name-only** change is presentation drift: the
re-import is idempotent and the stored series name is never rewritten (it mutates
nothing). External refresh → new revisions is a named future phase with its own
scientific question.

An existing identity that is NOT a complete compatible bundle — a manual mapping, a
half-attached pair, a wrong object type, a missing edge or checksum, or more than one
source snapshot — fails closed with a typed conflict and is never repaired or augmented
automatically.

### PostgreSQL uniqueness is the concurrency backstop

The bundle is created in ONE transaction and the `ExternalIdentity` insert (the FIRST
durable write) is protected by `uq_external_identity_authority_native`, so a committed
ExternalIdentity always implies a committed complete bundle. A loser rolls back,
re-reads the committed winner, validates the complete compatible bundle, and reuses it;
no raw `IntegrityError` reaches a caller. Existing-bundle linking is
SAVEPOINT-guarded per link. PostgreSQL is the acceptance truth.

### The importing Project is the steward of the new objects

Steward of the new `ProteinSeries`, `SequenceSeries`, and `ExternalReference`. Never
the user, never the provider, and never the `ExternalIdentity` (the global identity
registry is not Project-owned).

### Import creates no Evidence and no Decision

An external database record is a fact with provenance. Whether it *supports* a Project
conclusion is a separate human interpretation through the existing canonical Evidence
operation. Import never auto-cites and never promotes a Decision.

### The Agent may search, but can never import

Exactly one Tool is projected, `{provider}.protein.search`, with
`source=provider`, `execution_class=remote`, `side_effect_class=read_only`,
`autonomy=automatic`, input `{query, limit}` (extra=forbid), no `url`/`host`/`sequence`
/`import` field, and it calls the SAME application service as the human UI. There is
deliberately NO `protein.import` Tool and `ActionRequest` is deliberately NOT
generalized.

### Network shape is fixed and non-negotiable

Fixed `https://rest.uniprot.org/` host; caller input supplies only query/limit/
authority/native_id; `trust_env=False`; redirects OFF (a `303` is an inactive/
secondary-accession IDENTITY SIGNAL that fails closed); bounded connect/read timeouts;
response bytes checked while streaming; parameters encoded by the HTTP client. No
numeric rate limit is invented — the official documentation publishes none — so exactly
one bounded request is made per operation, with no retry, prefetch, harvesting, or
pagination, and `429`/`5xx` become a typed retryable failure.

### Provider text is data, and the persisted scientific content is exact

Provider text is normalized to bounded inert text by one shared neutral helper and can
never modify instructions, the ToolCatalog, authority, Evidence, or compute. The
canonical sequence is validated against the accepted uppercase amino-acid alphabet
(including legitimate ambiguity codes) and stored COMPLETE — the persistence bound is
never a presentation truncation, and malformed provider sequence data fails closed
rather than being repaired.

### User-facing presentation stays a compact existing-surface treatment

The Objects workspace hosts two panels (`Project objects` / `Discover proteins`);
provider selection is driven by the backend-owned `protein_discovery` capability kind,
and the only provider-specific frontend code is legal data-source attribution.

### No schema migration

Phase 14 adds no table and no column: `ExternalIdentity`, `ExternalReference`,
`ScientificObjectSeries`/`Revision`, `ScientificObjectExternalIdentity`,
`GlobalProvenanceEdge`, `ProjectResourceLink`, and `ResourceStewardship` already exist,
and `_GLOBAL_EDGE_SHAPE` already accepts an `EXTERNAL_REFERENCE` source for
`imported_as`. `alembic check` stays drift-clean on SQLite and PostgreSQL 16.

## Consequences

- **Positive.** REvoLab gains its first imported *scientific content* — a Protein and
  its exact canonical Sequence — as immutable, fully provisioned objects that survive
  provider disappearance and are reachable through the existing Search/Context/Agent
  surfaces. Identity, provenance, and scientific content remain strictly separated, and
  the deferred refresh question is deferred explicitly rather than answered by accident.
- **Positive.** The Phase-13 pattern (ephemeral candidate → explicit human import →
  current re-resolution → one atomic bundle → typed provenance) is shown to generalize
  from a reference-only import to an object-creating import without new Core domains,
  new relation vocabulary, or a new retrieval system.
- **Cost.** Phase 14 imports a *snapshot*, so an imported object can diverge from the
  live external record. That divergence is visible (the stored snapshot checksum) and
  deliberately unrepairable until the refresh phase exists. A user who needs the newer
  content must wait for that phase; the conflict message says so.
- **Cost.** A name-only external change re-imports idempotently and leaves the stored
  series name untouched. This is a direct consequence of the frozen checksum definition
  and is documented rather than hidden; it mutates nothing. Its one visible effect is
  that `project.search` matches the STORED name, so the object is findable by accession
  but not by the new name until a steward renames the series via the existing
  `update_series` operation.
- **Cost.** The real remote provider is opt-in (`REVOLAB_UNIPROT_DISCOVERY_ENABLED`),
  exactly like NCBI, so no CI or browser test ever performs a live UniProt request. A
  deployment must enable it deliberately.
- **Explicitly deferred** (and named, not silently postponed): external refresh → new
  revisions, inactive/secondary accession reconciliation, isoforms, UniProt ID Mapping,
  RCSB/PDB discovery/import, structure import, UniProt↔PDB reconciliation,
  GO/domain/PTM/pathway annotation import, a protein feature graph, external sequence
  alignment, RAG/vector retrieval, embeddings, semantic memory, external Web search, an
  Agent import Tool, automatic import, and background sync/caching.

## Rejected alternatives

- One giant `protein` payload containing the sequence (destroys the Sequence object,
  puts unbounded content in a typed payload, and conflates concept with content).
- Reverse or new relation vocabulary (`protein has_sequence sequence`) instead of
  reusing `represents` (duplicates frozen scientific vocabulary).
- Overwriting or refreshing an imported object on re-import (lets a remote third party
  silently redefine Project truth; makes provenance false).
- Appending a revision on any external difference (answers the refresh/identity
  question implicitly and silently).
- Persisting the raw provider payload or a candidate cache table (second stale source
  of truth; mirrors a database REvoLab does not own).
- Accepting scientific content from the browser or the model (makes the provider
  decorative and the client authoritative).
- Silently stripping an isoform suffix, or following a `303` to a different accession
  (silently changes the requested durable identity).
- Treating the checksum as proof of origin (it proves snapshot content, not who said
  it; origin is the identity + reference + provenance chain).
- Auto-repairing a manual or partial identity mapping (global identity mapping is a
  scientific assertion requiring explicit reconciliation).
- Auto-creating Evidence on import (conflates fact with interpretation).
- An Agent import Tool or a generalized `ActionRequest` (crosses the human authority
  boundary before its semantics are designed).
- Inventing a numeric UniProt rate limit (nothing is documented; a made-up constant
  would be a false constraint), or a `Retry-After`-driven background retry scheduler
  (explicitly out of scope).
