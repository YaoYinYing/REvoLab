# External Protein Discovery & Scientific Object Import

> **Status: Accepted** (Phase 14; human-accepted during PR #15 review; squash-merged
> into `main` as `93e2b221484446de5ca63db318a8099cc8ca799e`; normative owner of the
> external-protein discovery/import sub-boundary). Related ADR:
> `adr/ADR-0020-external-protein-resolution-imports-immutable-snapshots.md`
> (**Accepted**). The PR #15 human review and merge is the explicit acceptance event;
> a green test suite does not promote either.
>
> This document is normative for external protein discovery and import. It is an
> **application sub-boundary consumed by Presentation and Agent Context** — the same
> placement family as `EXTERNAL_LITERATURE_DISCOVERY.md`,
> `PROJECT_SEARCH_RETRIEVAL.md`, `PROJECT_CONVERSATIONS.md`, `PROJECT_NOTEBOOK.md`,
> and `AGENT_ACTION_HANDOFF.md`. It does **not** introduce a tenth Core domain, and
> the accepted nine-domain DAG is unchanged.

Related: `SYSTEM_ARCHITECTURE.md`, `DOMAIN_BOUNDARIES.md`,
`SCIENTIFIC_OBJECT_MODEL.md`, `SCIENTIFIC_GRAPH.md`, `EVIDENCE_PROVENANCE.md`,
`PROVIDER_CAPABILITIES.md`, `PROJECT_TOOL_HARNESS.md`, `AGENT_CONTEXT.md`,
`PROJECT_SEARCH_RETRIEVAL.md`, `WORKSPACE_INFORMATION_ARCHITECTURE.md`,
`EXTERNAL_LITERATURE_DISCOVERY.md`, ADR-0006, ADR-0008, ADR-0009, ADR-0010,
ADR-0012, ADR-0013, ADR-0018, ADR-0019.

---

## 1. The invariant

> **Resolve externally, import explicitly, snapshot immutably.**

> **External candidate ≠ Project truth.**

> **Provider/resolver ≠ durable identity authority.**

> **Protein ≠ Sequence.**

> **Import provenance ≠ Evidence.**

> **A changed external record never silently mutates an imported ScientificObject.**

The whole slice is:

```text
external protein provider (UniProt REST)
        ↓  bounded read-only discovery
ephemeral ProteinCandidate              (never persisted, never Project truth)
        ↓  explicit human Import
CURRENT provider resolve of (authority=uniprot, native_id=primary accession)
        ↓
one ExternalIdentity  +  one immutable ExternalReference snapshot
        ↓
Protein ScientificObject  +  Sequence ScientificObject (+ their Revisions)
        ↓
identity/sequence mappings + `represents` + `imported_as` provenance
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
biological entities REvoLab does not already know. It is the exact complement of
Phase-12 Project Search, generalizing the Phase-13 literature slice:

```text
Project Search                    = search what REvoLab already knows
External literature discovery     = discover publications outside REvoLab   (Phase 13)
External protein discovery        = discover biological entities outside REvoLab (Phase 14)
```

These are **three** distinct knowledge layers. Phase 14 does not merge them into one
hidden search system:

```text
SearchHit        = already-canonical REvoLab resource      (Phase 12)
ProteinCandidate = ephemeral external provider result      (Phase 14)
```

A `ProteinCandidate` never enters `project.search`; after an explicit Import the
resulting ScientificObjects become discoverable through the EXISTING Phase-12 read
projection, because `project.search` already matches a series through its external
identity mapping (`search._series_corpus`). **No search index, document table, or
retrieval model is added.**

---

## 3. What is a `ProteinCandidate`? Who owns it?

`ProteinCandidate` (`revolab.capabilities`) is provider-neutral, **ephemeral**
presentation data returned by one bounded external lookup:

```text
provider_key      the resolver that answered (NOT the authority)
authority         the durable identity namespace (e.g. uniprot)
native_id         the durable external identifier (a primary UniProtKB accession)
protein_name      bounded untrusted text
gene_name?        bounded untrusted text
organism_name?    bounded untrusted text
organism_id?      provider taxon identifier
sequence_length?  provider-reported length (presentation only)
reviewed?         Swiss-Prot vs TrEMBL, when reliably available
```

Ownership: **the Provider/Capability domain creates it; nothing owns it durably.**
It is deliberately NOT a `ScientificObject`, `ExternalIdentity`,
`ExternalReference`, `Evidence`, `SearchHit`, or Project truth, and it carries **no
sequence** and **no `metadata: dict` escape hatch**. It is never persisted — a page
reload may legitimately lose it, and there is no candidate cache table
(`protein_candidates`, `uniprot_cache`, `provider_records`). Searching performs
**zero durable writes**.

Breaking this is a defect, not a preference: a persisted candidate would be a
second, stale source of truth for an external record whose only truth is the
provider.

---

## 4. What is a `ResolvedProteinRecord`?

`ResolvedProteinRecord` is the **current** provider record an explicit Import
re-resolves. It is the canonical provider-neutral scientific snapshot *input*:

```text
provider_key
authority
native_id
canonical_sequence            the exact canonical (non-isoform) amino-acid sequence
protein_name?                 recommended protein name (bounded)
organism_name?                scientific name (bounded)
entry_name?                   provider mnemonic (bounded, PRESENTATION only)
primary_gene_name?            bounded
sequence_length?              provider-reported length (must agree with the sequence)
reviewed?
source_release?               upstream release identity (bounded)
source_release_date?
```

It is **not durable truth by itself**: it is validated by the application service and
transformed into immutable revisions, one `ExternalReference` snapshot, and typed
provenance edges. It is never stored as a whole, and the entire UniProt entry
(comments, features, domains, GO terms, pathways, cross-references, citations) is
deliberately not carried through Core.

---

## 5. What is the durable UniProt identity?

```text
authority = "uniprot"
native_id = the PRIMARY UniProtKB accession
```

Only **ACTIVE PRIMARY** accessions are supported. The driver validates the format
with the official UniProtKB grammar (§9), and the durable identity is stored once in
`external_identities`, protected by the existing unique constraint
`uq_external_identity_authority_native`.

Never used as durable identity: entry name (`uniProtkbId`), gene symbol, protein
name, a URL, a provider response id, or the **resolver** key.

Phase 14 does **not** support:

```text
inactive / deleted / demerged accessions   -> fail closed, never silently remapped
secondary accessions (merged away)         -> fail closed, never silently remapped
isoform identifiers  (P12345-2)            -> fail closed, never silently stripped
UniProt ID Mapping (RefSeq/PDB/GeneID/...) -> out of scope entirely
```

---

## 6. What becomes a ScientificObject? Why are Protein and Sequence separate?

A canonical UniProt entry is represented by **two** conceptual ScientificObjects:

| Object | Type | Payload | Meaning |
| --- | --- | --- | --- |
| Protein | `protein` | `{organism, source_sequence_ref: null, chain: null}` | the biological concept |
| Sequence | `sequence` | `{kind: "protein", sequence: <canonical AA>}` | the exact canonical content snapshot |

Both are created in ONE atomic import bundle; each gains exactly one immutable
initial Revision.

There is deliberately **no** `source_sequence_ref` string on the Protein payload:
the typed `represents` relation IS the relationship truth, and copying the Sequence
UUID into a payload field would create a second, non-authoritative representation of
the same fact. A Protein is a distinct conceptual entity from its sequence — a
sequence can be shared by several proteins, a protein's sequence can be corrected, and
a protein can exist as a concept without the sequence REvoLab happens to hold. Merging
them into one object would destroy that distinction permanently.

The revision checksum remains REvoLab's EXISTING canonical payload checksum
(`persistence.payload_checksum`). No second sequence checksum field is added.

---

## 7. What does `ExternalReference` represent?

`ExternalReference` is created over the `ExternalIdentity` and represents:

> "this external identity was resolved at this point and yielded this imported
> scientific snapshot"

It is **immutable resolver snapshot provenance**, NOT another identity card:

```text
external_identity_id   the durable identity it is a snapshot of
checksum               deterministic digest of the NORMALIZED scientific bundle
as_of                  resolution timestamp
cache_metadata         {resolver_provider, source_release?, source_release_date?}
```

The checksum is conceptually `sha256({protein_payload, sequence_payload})` with
sorted keys, so **changing upstream response formatting can never change scientific
snapshot identity**. It is snapshot identity, **not proof of origin**: origin comes
from the `ExternalIdentity` + `ExternalReference` + `imported_as` provenance.

`cache_metadata` is explicitly bounded and normalized: the **resolver** provider key
(never the durable authority) and the upstream release identity (bounded inert text
from the response headers). Raw UniProt JSON, annotations, feature tables, GO terms,
cross-references, comments, whole HTTP headers, and whole responses are never stored
there — or anywhere else.

---

## 8. Which provenance edges are created?

Exactly three, all through the existing typed domain commands (never a generic
relation writer):

```text
SequenceSeries    --represents-->   ProteinSeries              (#3, Series -> Series)
ExternalReference --imported_as-->  ProteinRevision            (#7)
ExternalReference --imported_as-->  SequenceRevision           (#7)
```

`represents` reuses the existing semantic edge; the direction is frozen
(Sequence → Protein) and no `has_sequence` / `sequence_of` /
`protein_sequence_of` `RelationType` is invented.

`imported_as` is the frozen #7 creator authority: **import authority +
steward(target Series)**, plus read(source reference). Phase 14 adds exactly one
typed command for it (`services.record_imported_as`) because the revisions are
created by the import itself and therefore cannot use the append-based
`import_revision`.

Identity mappings reuse the existing `ExternalIdentity` design — **one**
`ExternalIdentity` row per accession, one semantic target per qualifier:

```text
ExternalIdentity(uniprot, <accession>, kind="protein")
    qualifier="identity", is_canonical=true  -> ProteinSeries
    qualifier="sequence", is_canonical=true  -> SequenceSeries
```

---

## 9. Current UniProt API inspection (2026-09-16)

The driver must reflect the CURRENT official API, not a historical client. All of
the following was read from the official documentation and **additionally
live-verified** against `rest.uniprot.org` on **2026-09-16** (observed release
`2026_03`, release date `02-September-2026`).

**How the documentation was read.** `www.uniprot.org/help/*` is a JavaScript
single-page application and serves no content to a plain fetch. The same official help
content is served from the same origin in a machine-readable form at the explicit
**`https://rest.uniprot.org/help/<id>.json`** path (the extension matters: a bare
`/help/<id>` is content-negotiated and can answer `500` instead of the document). The
`api_queries`, `api_retrieve_entries`, `rest-api-headers`, `accession_numbers`,
`query-fields`, `return_fields`, `pagination`, `canonical_and_isoforms`,
`alternative_products`, and `license` documents were read there and are the source of
every normative claim below.

### 9.1 Endpoints

| Purpose | Endpoint |
| --- | --- |
| Search | `GET https://rest.uniprot.org/uniprotkb/search` |
| One entry | `GET https://rest.uniprot.org/uniprotkb/{accession}` (`.json` / `?format=json`) |

Documented search parameters: `query`, `format` ("applies to `tsv`, `xslx` and
`json` formats only"), `fields`, `includeIsoform`, `compressed`, `size`, `cursor`.
`/uniprotkb` itself is **not** a working search endpoint (it answers `301` to a
malformed internal host), so Phase 14 uses `/uniprotkb/search`.

`sort` is **not** in the official parameter table, so the driver does not send it.

### 9.2 Response shape (`format=json`)

```text
search:  {"results": [ ... ]}                      (no total/next in the body)
entry:   one object
```

Fields the driver consumes (all live-verified):

| Need | Path |
| --- | --- |
| Primary accession | `primaryAccession` |
| Entry name | `uniProtkbId` |
| Recommended protein name | `proteinDescription.recommendedName.fullName.value` |
| Primary gene name | `genes[0].geneName.value` |
| Organism | `organism.scientificName`, `organism.taxonId` |
| Canonical sequence | `sequence.value`, `sequence.length` |
| Reviewed / unreviewed | `entryType` |
| Release identity | `X-UniProt-Release`, `X-UniProt-Release-Date` response headers |

Two deliberate choices:

* **`fields` is always sent.** It is documented for JSON and reduces one result from
  tens of KiB of annotation payload to a few hundred bytes, so Core structurally never
  receives GO terms, features, comments, or cross-references.
* **`includeIsoform` is never sent**, so the documented default (the canonical entry,
  and therefore the canonical sequence) is what the provider returns.

The driver reads `entryType` (which is always returned) and does **not** request the
`reviewed` return field: it is a TSV column only and adds nothing to JSON. Because
`recommendedName` is **absent entirely** when an entry has only submitted names, the
driver reads it defensively and falls back to the accession for the Protein series
name rather than inventing a name.

### 9.3 `entryType` values (the only reviewedness signal)

```text
"UniProtKB reviewed (Swiss-Prot)"
"UniProtKB unreviewed (TrEMBL)"
"Inactive"                              (deleted / merged / demerged)
```

There is **no boolean `reviewed` field** in the JSON.

### 9.4 Inactive / secondary accession behavior (load-bearing)

Inactivity is reported **two different ways**, and the driver handles both:

| Case | Status | Signal |
| --- | --- | --- |
| Merged (now a secondary accession of a live entry) | **`303 See Other`** | `Location: /uniprotkb/<new>?from=<old>` |
| Demerged | **`200`** (no redirect) | body `entryType == "Inactive"`, `inactiveReason.inactiveReasonType == "DEMERGED"` |
| Deleted | **`200`** (no redirect) | body `entryType == "Inactive"`, `inactiveReason.inactiveReasonType == "DELETED"` |
| Genuinely unknown accession | `404` | error body |

There is **no `redirected` JSON field** in the current API; the legacy field is gone.
A client that inspected only the status code would silently treat a deleted entry as a
live one — Phase 14 checks **both**.

A **secondary accession requested through `accession:` search returns the Inactive
record itself**, not the live entry (the `sec_acc:` field is the documented way to
search secondary accessions). Phase 14 fails closed rather than adopting a redirect
target as the requested identity.

### 9.5 Accession grammar

Quoted from the official `accession_numbers` document:

```text
[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2}
```

6 or 10 uppercase alphanumerics, with position-specific constraints. Confirmed against
the official examples (`P12345`, `A2BC19`, `A0A023GPI8`) and other live accessions.
The `-N` isoform suffix is a **separate** layer ("the primary accession number of the
entry, followed by a dash and a number") and is rejected explicitly.

### 9.6 HTTP status semantics and rate limits

`200` success (also for in-body `Inactive`); `303` redirect (documented with the
inactive-entry example); `400` invalid request; `404` not found; `500`/`503` server
error. `410 Gone` appears in one official page but could **not** be reproduced on
2026-09-16, so the driver maps both `404` and `410` to `NOT_FOUND`.

**No numeric rate limit is documented.** The only `429` statement in the official
corpus concerns the *separate* `stream` endpoint. Phase 14 therefore:

* makes exactly **ONE** bounded request per search and per resolve;
* never retries, never prefetches, never harvests, never paginates;
* translates `429`/`5xx` into a typed, retryable `PROVIDER_UNAVAILABLE` failure;
* does **not** invent a pacing constant, and does **not** surface `Retry-After`
  (it is not documented for this API and nothing in Phase 14 consumes it — a
  background retry scheduler is an explicit deferral).

The driver also sends no invented `User-Agent`/contact parameter: the official
documentation prescribes none for the REST API. License: CC BY 4.0, attributed in the
UI.

---

## 10. Network and trust boundary

The driver uses a **fixed upstream host** and no caller-controlled network shape.
Caller/model input may provide only `query`, `limit`, `authority`, and `native_id`.

It may NOT provide a URL, host, scheme, port, proxy, HTTP method, redirect target, or
filesystem path. Concretely (`revolab/drivers/uniprot.py`):

```text
base_url      = https://rest.uniprot.org/    (process constant, never per request)
path          = a module constant, or `uniprotkb/{accession}.json` formatted from an
                accession that ALREADY passed the official grammar
params        = dict(params)         (parameter encoding through httpx)
follow_redirects = False
trust_env     = False                (ambient HTTPS_PROXY/ALL_PROXY/~/.netrc ignored)
timeout       = bounded connect/read
response body = bounded while STREAMING (2 MiB), then closed exactly once
```

Because redirects are off and the accession is grammar-validated first, there is no
SSRF surface and no identity remapping: a `303` is an *identity signal that fails
closed*, never a transport detail to follow.

**Logging note.** The application configures no logging, so `httpx`'s own INFO
`HTTP Request: GET <url?query>` line is dropped by the default WARNING root logger
today. A deployment that RAISES the `httpx` logger level would log the fixed provider
URL, the opaque search query, and the resolved accession. That is upstream transport
telemetry, not a REvoLab log statement, and no secret, header, or response body is
involved — but it is recorded here so the guarantee is explicit rather than
accidental.

Provider text (protein/gene/organism names, release headers) is normalized into
**bounded inert text** by one shared neutral helper (`capabilities.bounded_inert_text`,
also used by the Phase-13 driver): control/format characters are removed, whitespace
is collapsed, and the result is truncated to its neutral bound. It can never modify
Agent instructions or the ToolCatalog, authorize an import, create Evidence, submit
compute, be rendered as HTML, or be used as SQL. It is data.

---

## 11. Bounds (single canonical values)

`revolab.capabilities` owns the ceilings; the driver enforces them at the wire
boundary and the application service re-applies them to the projection, so a
mis-wired driver cannot widen the frontend or Agent surface.

```text
query characters            300
result count                1..20 (default 10)
protein name                300
gene name                   200
organism name               300
entry name                  100
source release              100
authority                   100   (= persisted column width)
native_id                   300   (= persisted column width)
canonical sequence          100_000 residues  (PERSISTENCE bound, never truncation)
remote response bytes       2 MiB (checked while streaming)
timeout                     bounded connect/read (default 15 s)
```

The sequence bound is deliberately far above the longest known protein (~35k residues
for titin) so a legitimate very large protein is never rejected, while an absurd
provider body still fails closed. **The complete bounded sequence is persisted**; it is
never truncated to satisfy context or storage limits.

### Sequence validation

The canonical sequence must be non-empty, bounded, and composed ONLY of the accepted
uppercase amino-acid alphabet (`ACDEFGHIKLMNPQRSTVWYBXZUOJ` — including the IUPAC
ambiguity codes that genuinely occur in UniProt entries, so legitimate rare/ambiguous
residues are not over-constrained). Whitespace, control characters, markup, lowercase,
and any other symbol are **malformed provider data and fail closed**; nothing is
silently repaired. Where the provider reports a length, it must equal the canonical
sequence length.

---

## 12. Import semantics

### 12.1 The request carries stable identity only

```text
provider_key, authority, native_id
```

`extra="forbid"`. The browser never sends a protein name, organism, gene name,
reviewed status, or sequence — those would be tamperable client data, and a
provider whose answer could be overridden by the client would be decorative. The
server re-resolves the identity at the CURRENT provider and builds the canonical
normalized scientific snapshot **server-side**.

### 12.2 The flow

```text
explicit human Import
    ↓  current Project MUTATION authority (owner/member, active Project)
    ↓  current Provider availability (READY driver, policy, credentials)
    ↓  CURRENT provider resolve(authority, native_id)
    ↓  validate the resolved identity (provider, authority, native_id)
    ↓  validate + normalize the snapshot (sequence, length, bounds, registry)
    ↓  ATOMIC persistence of the whole bundle
```

### 12.3 What is created, atomically

```text
ExternalIdentity                          (1)
ExternalReference                         (1)
ProteinSeries + ProteinRevision           (1 + 1)
SequenceSeries + SequenceRevision         (1 + 1)
ScientificObjectExternalIdentity          (2: identity, sequence)
Sequence --represents--> Protein          (1 edge)
ExternalReference --imported_as--> both   (2 edges)
ProjectResourceLink                       (5)
new-resource ResourceStewardship          (3: two series + the reference)
```

If any step fails, **none** of the local durable import remains: no orphan series,
revision, identity mapping, reference, link, stewardship, or provenance edge. The whole
bundle is one caller-owned transaction, and the only commit happens once at the end.

Implementation note (why this needed a small change): the historical
`persistence.insert_edge` always committed, and `add_conceptual_edge` inherited that.
Phase 14 adds an explicit `commit: bool = True` keyword to both (defaults preserve
every existing caller) plus the typed `services.record_imported_as` command, so the
bundle can compose several typed edges in ONE transaction **without** a generic
relation writer.

### 12.4 Repeat import is idempotent

Importing the same unchanged accession repeatedly into the same Project creates no
duplicate series, revision, identity mapping, `represents` edge, or `imported_as`
graph. The canonical existing bundle is returned, and the response echoes the
**stored** series name — not the current provider name — so a repeat import can never
imply that anything was refreshed.

### 12.5 Cross-Project import reuses global scientific identity

Project A imports `uniprot:<accession>`; Project B imports the same current snapshot
and receives links to the SAME `ProteinSeries`, `ProteinRevision`, `SequenceSeries`,
`SequenceRevision`, and `ExternalReference`. No copied ScientificObjects, no duplicate
`ExternalIdentity`, no duplicate conceptual representation. Project B gains only its
own read lens: `ProjectResourceLink` grants visibility, `ResourceStewardship` alone
authorizes mutation, so **the second Project never steals stewardship**.

### 12.6 A changed external record fails closed

When an `ExternalIdentity` already maps to an imported pair, Phase 14:

1. re-resolves the CURRENT provider record;
2. computes the normalized current snapshot checksum;
3. compares it against the imported snapshot provenance.

If unchanged, the existing import is reused/linked. If changed, it **fails closed
with a typed conflict**:

```text
External record has changed since the imported snapshot. Refresh/re-import
revision semantics are not implemented in Phase 14.
```

It never silently mutates a revision, appends a revision, relabels an existing
object, replaces a sequence, or links stale data as current.

The digest covers the normalized scientific bundle (`{protein_payload,
sequence_payload}`), so a change to the **recommended protein name alone** is
presentation drift, not a scientific change: the re-import succeeds idempotently and
the stored series name is never rewritten. This is a deliberate, documented
consequence of the frozen checksum definition, and it mutates nothing.

**Its one user-visible consequence.** `project.search` matches the *stored* series
name (§13), so after an upstream rename the object is reliably findable by its
ACCESSION but not by the new name until a steward renames the series through the
existing mutable `update_series` operation. This is bounded presentation staleness,
never silent corruption: no scientific content, identity, mapping, or provenance
changes.

### 12.7 Why refresh is deliberately deferred

Refresh raises a separate scientific question that Phase 14 must not answer
implicitly:

```text
Does an external change mean a new revision, a new conceptual object, a new alias,
a provider correction, a sequence replacement, or a merged/deleted accession?
```

Phase 14's job is initial import + stable reuse. External refresh → new revisions is
an explicit future phase (§18).

### 12.8 An incomplete or incompatible existing mapping fails closed

The trusted importer never hijacks or silently augments a global mapping it did not
create. If the identity already exists but is **not** a complete, compatible Phase-14
bundle, the import returns a typed conflict and leaves durable state unchanged.
Automatic repair is an explicit deferral.

The validator requires ALL of the following, and rejects the bundle otherwise:

```text
identity.kind == "protein"
both qualifier mappings present AND is_canonical
mappings point at two DIFFERENT series
object_type is protein / sequence respectively
neither series is archived (a retired object is never a live import target)
exactly ONE source snapshot (ExternalReference) over the identity
that reference carries a non-null snapshot checksum
exactly ONE imported_as revision per series, from THAT reference
the represents edge (Sequence -> Protein) exists
the stored checksum equals sha256 of the STORED revision payloads
```

The last check matters: the checksum is defined as the digest of the stored
`{protein_payload, sequence_payload}`, so trusting it without recomputing it from the
revisions it is supposed to describe would let a digest/revision disagreement present
stale content as current. Recomputing makes that state a typed conflict.

The detected states therefore include a manual `identity` mapping, a half-attached
pair, a wrong object type, an archived series, a disagreeing identity `kind`, a
non-canonical qualifier mapping, a missing `represents`/`imported_as` edge, a missing
checksum, a checksum that disagrees with its revisions, and **more than one source
snapshot**.

**Named deferral — global extra references.** The pre-existing generic surface
(`POST /projects/{id}/external-references`) lets any owner/member attach an additional
`ExternalReference` to any durable identity. Because such an extra snapshot makes
`len(references) != 1`, the Phase-14 validator then fails closed for that accession in
EVERY Project. That is the correct Phase-14 reaction (failing closed on an ambiguous
snapshot history), and Phase 14 deliberately does not change the generic endpoint's
authority; reconciling extra snapshots and requiring stewardship for them belongs to
the deferred identity-reconciliation phase (§18).

### 12.9 Concurrency

Database uniqueness is the backstop. The `ExternalIdentity` insert is the FIRST
durable write of the bundle and is protected by
`uq_external_identity_authority_native`, so it is the linearization point for a first
import.

That insert is deliberately **insert-only** (`scientific_object.create_external_identity`)
rather than get-or-create. A read-then-insert would open a window in which the winner
commits between the lookup and the insert, after which the loser would see the
winner's identity, skip the insert, build a SECOND bundle, and then fail on the
mapping primary key — a spurious "different mapping" conflict instead of convergence
(regression: `test_a_winner_committing_after_the_first_lookup_still_converges`).
Inserting straight away makes the database the single arbiter: the loser always loses
on the unique constraint.

Because the WHOLE Phase-14 bundle is created in ONE transaction, **for this import path
a committed `ExternalIdentity` implies a committed complete bundle** — which is exactly
why a loser can safely roll back and re-read the winner. (Qualification: the
pre-existing generic `ExternalReference`/identity surfaces can also commit a bare
identity with no Phase-14 bundle at all. The loser path never assumes completeness — it
calls the validator in §12.8 and fails closed — so the guarantee is a property of THIS
path, not of the identity table.)

A losing transaction rolls back, re-reads the committed winner, validates the complete
compatible bundle, and reuses it. A raw `IntegrityError` never reaches a caller, no
orphan loser objects remain, and no duplicate global series is created. Linking an
already-existing bundle into a Project uses a SAVEPOINT per link, so a concurrent
duplicate resolves to the committed winner without damaging the rest of the bundle.
PostgreSQL is the acceptance truth for this behavior; SQLite is a single-process
dev/test substrate.

### 12.10 Who becomes steward

The importing Project becomes steward of the newly created **ProteinSeries** and
**SequenceSeries** (and, following the existing reference-node convention, of the
`ExternalReference`). Never the user, never the provider, and never the
`ExternalIdentity` — the identity registry is global and is not Project-owned.

---

## 13. `ProteinCandidate` vs imported truth, and Import vs Evidence

Import creates **facts and provenance**, not interpretation:

```text
after import:  Evidence count unchanged
               Decision count unchanged
```

Phase 14 never claims "UniProt supports this protein" as Evidence, never creates a
Decision or a Decision draft, and never auto-cites. A later human or Agent may
explicitly create Evidence using the existing canonical operation. An external
database record is a reference fact; whether it *supports* a Project conclusion is a
separate human interpretation.

Project Search integration is automatic and free:

```text
before import:  project.search("<accession>") -> no imported ScientificObject hit
after import:   project.search("<accession>") -> Protein AND Sequence series
                project.search("<protein name>") -> Protein series
```

Import does **not** add anything to Agent context. The existing flow is unchanged:
`import → canonical objects → Project Search → explicit Add to Agent context →
ContextSelection → ContextBuilder`. No `ProteinImportContext`, `ExternalEntityContext`,
`AutoContext`, or per-turn search is introduced. Because `RevisionRefRead` omits
payloads, a large imported Sequence can never dump its content into an Agent turn: the
complete sequence is persisted, and context presentation stays bounded (regression
`test_a_large_sequence_stays_bounded_in_project_context`).

---

## 14. The Agent boundary

Phase 14 projects exactly ONE Agent-facing Tool through the existing ToolCatalog:

```text
{provider}.protein.search          e.g. uniprot.protein.search
    source           = provider
    execution_class  = remote
    side_effect_class= read_only
    autonomy         = automatic
    input            = {query, limit}   (extra=forbid)
```

No `url`, `host`, `sequence`, `import`, or project-target field exists, and the Tool
calls the SAME application discovery service as the human UI. The Agent may search,
inspect bounded candidates, reason over candidate metadata, and recommend an accession
for import. It may **not** create an `ExternalIdentity`, `ExternalReference`,
ScientificObject, or Evidence, may not attach an identity mapping, and may not execute
an Import: there is deliberately **no** `protein.import` Tool, and `ActionRequest` is
deliberately **not** generalized merely to support protein import.

External candidate text is untrusted Agent data. A hostile protein name is inert:
regressions prove it cannot change instructions, widen Tool autonomy, trigger an
Import or an `ActionRequest`, reveal credentials, or modify context.

---

## 15. Capability and catalog

Phase 14 adds exactly ONE Core-owned capability kind:

```text
CapabilityKind.PROTEIN_DISCOVERY = "protein_discovery"    (read-only)
```

It is added because a real provider now realizes it, it is provider-neutral, and it is
NOT persisted in the database (capability kind is projected at runtime from the
driver's capability map). `PROTEIN_DISCOVERY` is added to
`READ_ONLY_CAPABILITY_KINDS`, so any readable membership may discover while only
owner/member may import.

The provider-neutral protocol is the smallest one the real use case forces:

```text
ProteinDiscoveryCapability
    search(query, limit, credential_lease) -> ProteinSearchResult
    resolve(authority, native_id, credential_lease) -> ResolvedProteinRecord
```

No `BiologicalKnowledgeCapability`, `UniversalEntityCapability`, `OmicsCapability`,
`KnowledgeGraphCapability`, or `DatabaseCapability` is introduced without a second
concrete use case.

The first concrete provider:

```text
provider_key = "uniprot"
display_name = "UniProt"
authority    = "uniprot"
```

`provider_key == authority` holds here **only by coincidence**, and the implementation
keeps the concepts separate. A load-bearing regression registers a different resolver
(`provider_key = "mirrorprotein"`, `authority = "uniprot"`) and proves that importing
through it creates `ExternalIdentity(uniprot, <accession>)` — never
`ExternalIdentity(mirrorprotein, ...)` — with the resolver recorded only as
`cache_metadata.resolver_provider`.

Registration: the public UniProt REST surface needs no credential and no operator
identity, but the real remote driver is installed only when the deployment sets
`REVOLAB_UNIPROT_DISCOVERY_ENABLED` — exactly like the NCBI literature provider. A
zero-config deployment keeps the Provider Catalog an honest empty set, and CI/browser
slices never perform a live UniProt request. They opt into an in-process fake that
claims its OWN `fakeuniprot` authority, so a synthetic fixture identity can never be
mistaken for a real accession; precisely because that namespace is its own, the fake
does not collide with the real driver. The real `uniprot` namespace is guarded by (a)
the driver-registry authority-collision check, which refuses any SECOND resolver
claiming `uniprot` alongside the real driver, and (b) the production refusal — the fake
is never installed when `environment == "production"`.

---

## 16. Presentation

The workspace integrates external protein discovery with the existing Objects
workspace as two compact panels rather than a new top-level surface or a generic
external-database dashboard:

```text
Objects
├ Project objects      (what REvoLab already knows)
└ Discover proteins    (external discovery + explicit import)
```

Candidates are presented with protein name, gene, organism, accession, length, and
reviewed status, carry the explicit badge `external · not yet in Project`, and offer
`Import to Project`. After an import the surface links to the canonical **Protein** and
**Sequence** through the EXISTING object-detail surfaces. A viewer may search but is
offered no Import control, and the backend independently refuses a direct viewer
import with `403`.

Provider selection comes from the Provider Catalog filtered by the backend-owned
`protein_discovery` capability kind. The frontend never writes
`if provider.key === "uniprot"` to decide capability behavior; the only
provider-specific code is the legal attribution of the data source, which is
presentation, not capability semantics.

---

## 17. External protein import is not reference merging

Phase 14 does not merge the external entity into an existing ScientificObject:

```text
"UniProt says P12345 is my Protein X"  ->  reconciliation, deliberately deferred
```

Automatic reconciliation would let a remote third party silently redefine what an
existing REvoLab object *is*. Phase 14 therefore always creates a fresh Protein +
Sequence pair for a new external identity, and fails closed when the identity already
exists in an incompatible shape.

---

## 18. Explicit deferrals (not silently postponed)

```text
external refresh -> new revisions
inactive / secondary accession reconciliation
UniProt isoforms (P12345-2) and isoform alignment
UniProt ID Mapping (RefSeq/PDB/GeneID/Ensembl -> UniProt)
RCSB/PDB search and structure import
mmCIF/PDB download and PDB external-identity reconciliation
UniProt <-> PDB cross-link import
GO / domain / active-site / binding-site / PTM / pathway / disease annotation import
protein feature graph
external sequence alignment
RAG / vector retrieval / embeddings / semantic memory / pgvector
external Web search
Agent import Tool and automatic import
background sync / crawling / provider cache tables
```

Nothing is silently postponed: each is a named future design question with its own
authority semantics.

---

## 19. Verification

Machine gates (see `IMPLEMENTATION_STATE.md` for the recorded evidence):

* deterministic UniProt driver tests over an injected `httpx.MockTransport` — CI
  never touches live UniProt;
* SQLite application/API/Agent regressions and the PostgreSQL 16 acceptance suite
  (initial import, repeat import, cross-Project reuse, same-Project and cross-Project
  races, identity/mapping/link uniqueness, edge shape, atomic rollback,
  changed-snapshot conflict, Project Search integration);
* `alembic check` drift-clean on SQLite and PostgreSQL 16 with **no new migration**
  (Phase 14 adds no table and no column);
* regenerated OpenAPI + frontend contracts (idempotent);
* frontend typecheck/test/build/`check:contracts`;
* a Playwright browser vertical slice (discovery → external labelling → Import →
  Protein/Sequence objects → Project Search → relation inspection → Agent context) plus
  the browser negatives (viewer cannot import, provider outage, hostile text inert,
  repeat import idempotent, changed snapshot reports a conflict, search-only leaves
  nothing durable).
