# External Literature Discovery & Explicit Import

> **Status: Proposed — pending human acceptance** (Phase 13; normative owner of the
> external-literature discovery/import sub-boundary). Related ADR:
> `adr/ADR-0019-external-literature-discovery.md` (**Proposed — pending human
> acceptance**). Both are promoted only by explicit human acceptance at PR review;
> a green test suite does not promote them.
>
> This document is normative for external literature discovery and import. It is an
> **application sub-boundary consumed by Presentation and Agent Context** — the same
> placement family as `PROJECT_SEARCH_RETRIEVAL.md`, `PROJECT_CONVERSATIONS.md`,
> `PROJECT_NOTEBOOK.md`, and `AGENT_ACTION_HANDOFF.md`. It does **not** introduce a
> tenth Core domain, and the accepted nine-domain DAG is unchanged.

Related: `SYSTEM_ARCHITECTURE.md`, `DOMAIN_BOUNDARIES.md`,
`PROVIDER_CAPABILITIES.md`, `EVIDENCE_PROVENANCE.md`, `SCIENTIFIC_GRAPH.md`,
`PROJECT_TOOL_HARNESS.md`, `AGENT_CONTEXT.md`, `PROJECT_SEARCH_RETRIEVAL.md`,
`WORKSPACE_INFORMATION_ARCHITECTURE.md`, ADR-0008, ADR-0012, ADR-0013, ADR-0017,
ADR-0018.

---

## 1. The invariant

> **Discover externally, import explicitly, interpret separately.**

> **External candidate ≠ Project truth.**

> **Imported LiteratureReference ≠ Evidence.**

> **Provider/resolver ≠ durable identity authority.**

The whole slice is:

```text
external literature provider (NCBI PubMed)
        ↓  bounded read-only discovery
ephemeral LiteratureCandidate          (never persisted, never Project truth)
        ↓  explicit human Import
CURRENT provider re-resolution of (authority=pubmed, native_id=PMID)
        ↓
canonical global LiteratureReference + ProjectResourceLink
        ↓  (already searchable by Phase 12)
Project Search visibility
        ↓  explicit "Use as Evidence"
existing canonical Evidence operation
        ↓  (existing Decision draft → committed)
Project knowledge
```

---

## 2. What is external discovery?

**External discovery** is a bounded, read-only read of a REMOTE system for
publications REvoLab does not already know about. It is the exact complement of
Phase-12 Project Search:

```text
Project Search              = search what REvoLab already knows
External Literature Discovery = discover publications REvoLab does not yet know
```

They are never merged into one hidden search engine. `project.search` does not call
PubMed, and remote PubMed results never appear as an ordinary `SearchHit`. The
contracts are distinguishable by type:

```text
SearchHit            → a canonical REvoLab resource already in Project context
LiteratureCandidate  → an ephemeral external discovery candidate
```

Discovery is an **operational read**, not a scientific relationship: there is no
`discovered_by` provenance edge and no `ImportRecord` node (section 11).

---

## 3. What is a LiteratureCandidate? Who owns it?

A `LiteratureCandidate` is provider-neutral, bounded, **untrusted** bibliographic
presentation data returned by one external read:

```text
provider_key         the resolver/provider that produced it ("ncbi")
authority            the DURABLE identity namespace ("pubmed")
native_id            the identifier within that authority (a PMID)
title, authors, journal, publication_year, doi   (optional, bounded)
```

- **Owner:** the Provider / Capability domain owns the neutral value object
  (`revolab.capabilities.LiteratureCandidate`); the concrete provider owns the
  vocabulary that produced it.
- **Not** a `LiteratureReference`, `ExternalReference`, `Evidence`, `ProjectNote`,
  Agent memory, or `SearchHit`.
- There is deliberately **no** `metadata: dict[str, Any]` and no raw provider JSON:
  provider-specific field names (ESummary `pubdate`, `articleids`,
  `fulljournalname`, …) never leave the driver.

---

## 4. What is a LiteratureReference? Who owns it?

A `LiteratureReference` is the durable, global citation identity card owned by the
Evidence / Provenance domain (`literature_references`):

```text
literature_id   opaque UUID (global resource identity)
authority       "pubmed"
native_id       the PMID
title           bounded presentation text
```

Identity is `(authority, native_id)` — never the title, never the provider, never a
DOI. `UNIQUE(authority, native_id)` is the database backstop.

The table stays intentionally small. Phase 13 adds **no** `metadata_json`,
`authors_json`, `journal_json`, `abstract`, or `provider_payload` column.

---

## 5. What is persisted? What is deliberately NOT persisted?

Only on an explicit successful **Import**:

```text
LiteratureReference      if new globally
ProjectResourceLink      if new in this Project
ResourceStewardship      only when this import created the global reference
```

Never persisted by Phase 13:

```text
LiteratureCandidate / discovery results / search cache / pubmed_records tables
abstracts, full text, PDFs, PMC records
Evidence, Decision, Note, ExternalReference, ScientificObject (as import side effects)
an ImportRecord / discovered_by provenance node
```

A page reload may require performing the search again. That is acceptable and
deliberate: a candidate is ephemeral output from a remote read.

---

## 6. How does a candidate become Project context? Why does import not create Evidence?

```text
human clicks "Import to Project"
    ↓  current Project MUTATION authority (owner/member)
    ↓  current provider availability (READY driver + policy)
    ↓  resolve(authority, native_id) at the CURRENT provider
    ↓  verify the returned durable identity EXACTLY matches the request
    ↓  canonical global LiteratureReference get-or-create
    ↓  ProjectResourceLink
```

The import request carries **stable identity only**
(`provider_key`, `authority`, `native_id`). The browser/model never supplies the
canonical title/authors/journal that will be persisted — the server re-resolves
them, so a tampered payload, a stale search result, a forged title, or a forged
PMID cannot become durable truth.

Import does **not** create Evidence because the two are categorically different:

```text
LiteratureReference  = the publication identity FACT
Evidence             = the Project's INTERPRETATION of that publication
```

Interpreting a paper (role, polarity, confidence, scope, target) is a separate,
explicit human act through the EXISTING canonical Evidence operation. The workspace
makes the distinction visible: the external list offers `Import to Project`; the
imported list offers `Use as Evidence`, which hands off to the existing Evidence
creation surface prefilled with `source_kind = literature_reference` and
`source_id = <literature_id>`.

---

## 7. How does authority differ from resolver/provider?

```text
provider / resolver = how REvoLab obtains the record   -> "ncbi"
authority           = who defines the durable identity -> "pubmed"
```

The first concrete provider is:

```text
provider_key = "ncbi"                display_name = "NCBI PubMed"
durable authority = "pubmed"         native_id = PMID
```

Identity must **not** become `authority = "ncbi"` merely because NCBI is the
current resolver. A future resolver could resolve the same `pubmed:12345678`
identity without changing any stored `LiteratureReference`; the driver declares the
authorities it can resolve (`Driver.authorities`), and the registry refuses to
REGISTER a second driver claiming an authority an already-registered driver
declared (a stricter, registration-time check — driver state is irrelevant).

A DOI that a PubMed record happens to expose is presentation data only. Phase 13
never silently switches identity to DOI and never creates two references for one
publication; DOI↔PMID identity reconciliation is explicitly deferred (section 14).

---

## 8. What happens if the provider becomes unavailable?

```text
NCBI unavailable
    ↓
external discovery / import resolution unavailable  (typed PROVIDER_UNAVAILABLE)

existing LiteratureReference
    ↓
still valid Project context — searchable, readable, citable
```

Provider outage never deletes, revokes, or invalidates a stored reference. The
driver is not hot-unloaded (ADR-0005); live calls simply fail with a typed
`CapabilityError` and the Provider Catalog reports the capability unavailable.

---

## 9. How does the Agent use the discovery capability?

The real provider capability is projected into the canonical ToolCatalog:

```text
ncbi.literature.search      (suffix: .literature.search)
source            = provider
execution_class   = remote
side_effect_class = read_only
autonomy          = automatic
input             = { query, bounded limit }     (no url, host, scope, import flag)
```

The Agent may **automatically** invoke it and reason over the bounded candidates. It
calls the SAME application service the human workspace calls — there is no
Agent-specific provider path.

The Agent may NOT:

```text
persist a LiteratureReference automatically
create Evidence merely because a candidate exists
treat a candidate as Project truth
import on the user's behalf
```

Phase 13 adds **no** Agent import Tool and does **not** generalize `ActionRequest`
for this import. Human explicit import is enough to prove the architecture.

`revolab.tools.remote_reads` is the ONE place that links a remote read-only Tool id
to the application service it invokes, and it does so by the stable capability
SUFFIX (never the provider key prefix), so Core never branches on provider
vocabulary. A remote Tool is Agent-executable as a read only when it is both listed
there AND projected with `automatic` + `read_only`; every other remote tool still
fails closed at the Agent boundary.

---

## 10. What requires a human action?

```text
automatic (Agent and human)   bounded external discovery (read-only)
explicit human (owner/member) Import to Project  -> durable LiteratureReference + link
explicit human (owner/member) Use as Evidence    -> canonical Evidence creation
explicit human (owner/member) Decision commit     -> the Phase-11 promotion gate
```

A **viewer may discover** literature when ordinary read policy permits (the
capability kind is a read-only kind) but may **not** import or interpret. Project
tombstone or membership revocation takes effect immediately: authorization is
re-derived on every call, and search-time access is never a cached mutation grant.

---

## 11. Import semantics (identity, idempotency, provenance, atomicity)

- **Cross-Project deduplication.** `LiteratureReference(authority, native_id)` is
  global. Two Projects importing `pubmed:12345678` receive the SAME global identity
  and independent `ProjectResourceLink` rows. No duplicate publication object.
- **Idempotency.** Re-importing the same publication in one Project yields the same
  reference and one link. Concurrent imports cannot leak a raw `IntegrityError`:
  the unique `(authority, native_id)` and `(project_id, resource_id)` constraints are
  the backstop, and EVERY link path (the create+link block, the existing-reference
  fast path, and the loser-recovery link) is guarded, so a losing racer rolls back
  and resolves to the committed winner.
- **Trusted path.** Provider import uses a narrowly named trusted application/domain
  helper (`services._persist_literature_reference_from_resolver`) reachable ONLY
  after a successful provider resolve. It is never an API accepting arbitrary
  trusted client metadata, and it does NOT weaken the existing request-derived
  `create_literature_reference` share-authority rule (possessing
  `(authority, native_id)` is still not authority to make an existing global
  reference visible in another Project through the generic path).
- **New vs existing.** For a NEW global reference the resolver's bounded title
  populates the existing `title` column. An EXISTING reference is linked UNCHANGED:
  a differing current provider title is refreshable presentation data, never a
  durable-identity conflict, and the stored title is never overwritten.
- **Provenance.** A `LiteratureReference` is a canonical graph node; discovery is an
  operational read, so no `discovered_by` edge and no `ImportRecord` node are
  created. Once the user interprets the publication, the existing Evidence source
  relation is sufficient.
- **Atomicity.** A remote read has no external side effect, so ordinary local
  transaction rollback suffices: if resolve fails there is no reference and no link;
  if reference persistence fails there is no orphan link. No distributed-transaction
  machinery is invented.
- **Existing manual API preserved.** `POST /projects/{project_id}/literature`
  remains the request-derived manual reference-entry API with its share-authority
  semantics. The new provider import path is explicitly and separately named
  (`POST /projects/{project_id}/literature/import`).

- **Pre-existing sibling race (out of Phase-13 scope).** The provider-trusted
  import path guards EVERY link insert (section 11). The request-derived generic
  reference paths retain their long-standing shape: `create_literature_reference` →
  `_link_existing_reference`, and the Phase-4 trusted run/artifact helpers, still do
  a read-then-insert `ProjectResourceLink` without a uniqueness guard. Two
  simultaneous generic-link requests for the SAME existing reference in the same
  Project could therefore still surface a raw `IntegrityError`. This is a
  pre-existing property of accepted Phase-1/4 code, NOT of the Phase-13 import path,
  and was recorded by the delta review as an out-of-scope residual; routing those
  paths through the same guarded link helper is a separate, deliberately deferred
  follow-up (it changes accepted generic-link behavior).
- **Presentation field, not authority.** The import response reuses the existing
  typed `ReferenceRead`, whose `read_only` flag is derived from current
  stewardship. It is presentation metadata with no Project attribution and grants no
  authority: a second Project that imports an already-stewarded public identity sees
  `read_only = true`, which is the same honest signal the existing reference list
  already exposes for every visible reference.
- **Residual, contract-sanctioned.** The request-derived
  `POST /projects/{project_id}/literature` can still pre-create an arbitrary
  `(authority, native_id)` with a client-supplied title. That is the accepted manual
  reference-entry path (`TODO.md` section 46). A later real provider import of the
  same identity links that reference UNCHANGED (identity is `(authority, native_id)`,
  never the title). This is deliberate and documented; tightening manual
  reference-entry authority is a separate change to an accepted contract.
- **Process-local pacing.** The request pacer is per-process (per driver instance),
  so a multi-worker deployment paces independently per worker; the conservative
  0.34 s default leaves headroom under NCBI's per-IP ceiling, but an operator
  running many workers must account for the aggregate rate. The health probe shares
  the same pacer and the same response-byte bound.

---

## 12. NCBI usage requirements are architecture requirements

The driver complies with the CURRENT official E-utilities usage policy (inspected
2026-09; `https://www.ncbi.nlm.nih.gov/books/NBK25497/`,
`.../NBK25499/`):

```text
fixed official host      https://eutils.ncbi.nlm.nih.gov/entrez/eutils/
identification           a `tool` string and an operator `email` on EVERY request
rate                     no more than 3 requests/second/IP without an API key
                         -> a conservative default minimum interval of 0.34 s
batching                 ONE ESearch + ONE batched ESummary per search
bounds                   query chars, result count, field lengths, response bytes,
                         connect/read timeout
read-only                no PDF, no full text, no PMC, no abstract persistence
notice                   the NCBI Disclaimer and Copyright notice must be evident
                         to users -> the Literature discovery surface shows an
                         unobtrusive attribution line linking to
                         https://www.ncbi.nlm.nih.gov/About/disclaimer.html
```

`tool` and `email` are **operator configuration** (`REVOLAB_NCBI_TOOL`,
`REVOLAB_NCBI_EMAIL`), never Project data and never a hard-coded developer address.
A half-configured deployment fails loudly at startup rather than probing NCBI
anonymously. NCBI's policy additionally requires the operator to **register** the
`tool`/`email` values with NCBI (sending them is necessary but not sufficient);
that registration is a deployment/operator step outside this repository and must be
completed before a deployment is considered compliant.

**Disclaimer / copyright notice.** NCBI's policy requires its Disclaimer and
Copyright notice to be evident to users of any software that uses the E-utilities.
The Literature discovery surface therefore renders a small, unobtrusive attribution
line naming the providing provider and linking to the official notice
(`frontend/src/views/Literature.tsx`). This is legal/terms attribution, not
capability semantics: the generic frontend still selects providers by the
backend-owned `literature_discovery` capability kind and never branches on a provider
key to decide what a capability does.

**No API-key feature exists in Phase 13** (explicit non-goal): the slice works
within the official unauthenticated rate. Optional API-key support is a documented
future optimization only.

**Network safety:** the upstream host is FIXED. Callers may supply only `query`,
`limit`, and a `PMID` for resolve — never a URL, hostname, scheme, port, proxy,
redirect destination, HTTP method, or filesystem path. Query parameters are passed
through httpx encoding (never string-concatenated into a URL), TLS verification is
on, timeouts are bounded, the response is streamed under a byte ceiling, and
redirects are not followed, and `trust_env=False` makes the fixed-host boundary
literal (ambient `HTTPS_PROXY`/`ALL_PROXY`/`~/.netrc` cannot reroute it). The driver
must never become an SSRF primitive.

**Untrusted provider data:** all provider text is bounded and sanitized (control and
format characters removed, fields truncated to neutral limits); structurally
impossible payloads fail closed with a typed `CapabilityError` rather than crashing
into a 500. Provider text is never rendered as raw HTML and never becomes Agent
instructions.

**Offline CI:** tests and CI never depend on live NCBI. Deterministic tests drive
the real driver over an injected HTTP transport with canned bounded ESearch/ESummary
JSON (synthetic citation metadata only — no copyrighted abstract/full-text fixture),
and application/browser tests use an in-process fake realizing the SAME
`LiteratureDiscoveryCapability` boundary through the SAME Driver/Capability
registry. An opt-in manual live smoke may exist; it is not CI truth.

---

## 13. Capability and catalog

Phase 13 adds exactly ONE Core-owned capability kind, forced by the real provider:

```text
CapabilityKind.LITERATURE_DISCOVERY = "literature_discovery"
```

The provider-neutral Protocol is:

```text
LiteratureDiscoveryCapability
    search(query, limit, credential_lease) -> LiteratureSearchResult
    resolve(authority, native_id, credential_lease) -> LiteratureCandidate
```

It contains only what PubMed discovery/import requires. No
`BiologicalKnowledgeCapability`, `UniversalSearchCapability`,
`KnowledgeGraphCapability`, `SemanticSearchCapability`, or
`CitationGraphCapability` is added without a concrete second use case.

The Provider Catalog exposes the capability through the existing provider-neutral
contract (no NCBI special-case in generic catalog code), and discovery is a
read-only capability kind, so availability composes as usual:
`READY driver AND required credentials present AND project policy permits`. The
Phase-13 provider requires **no** credential kind, so the credential term is
trivially satisfied and a viewer gets `AVAILABLE`.

---

## 14. Explicit deferrals (not silently postponed)

```text
semantic/vector retrieval, embeddings, pgvector, RAG
a generic knowledge-provider framework beyond the real PubMed case
UniProt/RCSB external entity discovery/import
ExternalReference → ScientificObject imported_as slice
DOI/PMID identity reconciliation and citation-graph construction
full-text literature resolution, PDF acquisition, PMC fetch
abstract/full-text persistence and chunking
persistent external-search cache and saved searches / alerts
systematic-review workflow, background crawling, citation alerts, weekly polling
literature pagination beyond a bounded top-N
Agent-proposed import through ActionRequest
NCBI API-key support, NCBI account/OAuth integration
external Web search
provider configuration UI (operator configuration stays deployment/config)
```

---

## 15. Verification

Executable acceptance lives in:

```text
backend/tests/test_pubmed_driver.py        real driver over deterministic HTTP
backend/tests/test_literature.py           application service + HTTP surface
backend/tests/test_literature_agent.py     Agent catalog/tool/trust boundary
backend/tests/test_provider_availability.py  capability kind + availability
backend/tests/test_postgres_integration.py   concurrency / global identity / search
frontend/src/views/Literature.test.tsx     workspace surface
frontend/e2e/literature.spec.ts            browser vertical slice + negatives
```
