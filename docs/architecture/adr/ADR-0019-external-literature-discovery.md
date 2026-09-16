# ADR-0019: External Literature Discovery Uses Ephemeral Candidates and Explicit Import

> **Status: Proposed — pending human acceptance.**
> Accepted only by explicit human acceptance at PR review; a green test suite does
> not promote this ADR's status.

## Context

Phases 1–12 established canonical Project truth, the two ways to *use* it
(`ContextSelection` → `ContextBuilder`, and Phase-12 Project Search over what
REvoLab already knows), and the Provider / Capability boundary (ADR-0012). What was
missing is **external discovery**: finding knowledge REvoLab does not yet know.

The obvious shortcuts are all unsafe or out of scope:

- silently making `project.search` call PubMed would merge two different semantics
  (search what we know vs. discover what we do not) and leak remote results into
  Project context;
- persisting discovery candidates in a cache/`pubmed_records` table would create a
  second, stale source of truth and premature copyright/content-store questions;
- trusting the browser's or model's copy of a title/authors/journal would let a
  tampered or stale payload become durable bibliographic truth;
- automatically creating `Evidence` on import would conflate a publication
  identity fact with a Project interpretation;
- giving the Agent an import Tool (or generalizing `ActionRequest`) would cross the
  human authority boundary for a case whose result-reference semantics are not yet
  designed.

What was not yet decided is **who owns external discovery, what a candidate means,
what is persisted, how identity differs from the resolver, and how a candidate
becomes Project context**.

## Decision

### External discovery is an application sub-boundary, not a tenth Core domain

`revolab.literature` is an application-level discovery/import service consumed by
Presentation and Agent Context — the same placement family as `revolab.search`,
`revolab.notes`, and `revolab.actions`. It adds no Core domain and no Project →
domain edge; the accepted nine-domain DAG is unchanged. The provider-neutral
capability value objects live in `revolab.capabilities`, the provider-specific HTTP
vocabulary lives only in `revolab.drivers.ncbi`, and the capability is reached only
through the shared provider-invocation gate (`revolab.domain.compute` +
`revolab.domain.discovery`).

### One new Core capability kind, forced by a real provider

`CapabilityKind.LITERATURE_DISCOVERY` is added because a real provider (NCBI
PubMed) now realizes it, with the small provider-neutral Protocol

```text
LiteratureDiscoveryCapability
    search(query, limit, credential_lease) -> LiteratureSearchResult
    resolve(authority, native_id, credential_lease) -> LiteratureCandidate
```

No generic knowledge/search/citation-graph capability is introduced.

### A LiteratureCandidate is ephemeral, bounded, and provider-neutral

It carries `provider_key`, `authority`, `native_id`, and bounded presentation
fields only. It is never persisted, never a `SearchHit`, never Project truth, and it
never carries raw provider JSON or provider-specific field names.

### Discovery persists nothing; import is explicit and re-resolves identity

Discovery authorizes current Project read access, resolves current provider
availability, invokes the capability, re-bounds the result, and returns a typed
projection — with zero durable writes. Import requires current Project mutation
authority, then re-resolves the stable `(authority, native_id)` at the CURRENT
provider and verifies the returned identity matches the request before
get-or-creating the global `LiteratureReference` and this Project's
`ProjectResourceLink`. The request carries no bibliographic metadata at all.

### Authority is not the resolver

The provider is `ncbi`; the durable authority is `pubmed`. Identity is
`(authority, native_id)` and `UNIQUE(authority, native_id)` is the backstop; a
future resolver may resolve the same identity unchanged. A DOI is presentation data,
never a silent identity switch.

### The durable reference stays intentionally small

`LiteratureReference` keeps `authority`/`native_id`/`title`. Phase 13 adds no
`metadata_json`/`authors_json`/`abstract`/`provider_payload` column and no
candidate/cache table. For a NEW reference the resolver's bounded title populates
`title`; an EXISTING reference is linked unchanged (title disagreement is never
identity disagreement).

### Provider-confirmed import is a distinct trusted path

`services._persist_literature_reference_from_resolver` is reachable only after a
successful provider resolution. It does not weaken the request-derived
`create_literature_reference` share-authority rule, and it is never exposed as an
API accepting arbitrary trusted metadata. Cross-Project import reuses ONE global
reference; concurrent import resolves to the committed winner with no raw
`IntegrityError`.

### Import is not interpretation

Import creates no `Evidence`, `Decision`, `Note`, `ExternalReference`, or
`ScientificObject`. Interpreting a publication is a separate explicit human act
through the EXISTING canonical Evidence operation, handed off from the imported list
as `source_kind = literature_reference`, `source_id = <literature_id>`.

### The Agent may cross only the bounded read-only external boundary

The provider capability is projected into the canonical ToolCatalog as
`ncbi.literature.search` (`provider`, `remote`, `read_only`, `automatic`), and the
Agent loop may execute exactly the remote read-only Tools registered in
`revolab.tools.remote_reads` (matched by capability SUFFIX, never by provider key).
This deliberately narrows the Phase-8 rule that no remote tool is Agent-executable:
every other remote tool still fails closed. The Agent gets no import Tool, no
automatic persistence, and no `ActionRequest` generalization; hostile provider text
is ordinary untrusted tool-result data that can change neither authority nor the
ToolCatalog.

### Phase 12 remains the only Project-search engine

Project Search and external discovery are never merged. An imported reference
becomes Phase-12 searchable automatically because there is no search index: the
canonical row plus its `ProjectResourceLink` are sufficient. Before import, an
external candidate is absent from Project Search.

### The NCBI driver complies with current official usage policy

Fixed official host, required `tool`/`email` operators on every request,
conservative no-key pacing below 3 requests/second, one ESearch plus one batched
ESummary, bounded query/results/fields/response bytes/timeouts, TLS on, no redirect
following, and no API-key feature in this phase.

## Consequences

- Discovery has exactly one owner and one canonical direction:
  `authorized read-only discovery → ephemeral candidates → explicit human import →
  re-resolved global reference + Project link → Phase-12 search → explicit Evidence`.
- Provider vocabulary and network access stay behind the driver; Core knows only the
  closed capability kind and the neutral candidate shape.
- Provider outage degrades live discovery only; stored references remain valid
  Project context.
- No migration is added: `CapabilityKind` is not a persisted column, and no table or
  column changes, so `alembic upgrade head`/`alembic check` stay drift-clean on
  SQLite and PostgreSQL 16.
- Semantics live in one normative document
  (`docs/architecture/EXTERNAL_LITERATURE_DISCOVERY.md`); consuming documents point
  at it.
- Explicitly deferred (and owned by future ADRs): semantic/vector retrieval and RAG,
  a generic knowledge-provider framework, UniProt/RCSB entity import, DOI/PMID
  identity reconciliation, full-text/PDF/abstract ingestion, persistent search
  caches and saved searches, systematic-review workflow, literature pagination,
  Agent-proposed import via `ActionRequest`, and NCBI API-key support.

## Rejected alternatives

- Folding PubMed into `project.search` (conflates "known" with "discovered", leaks
  remote results into Project context).
- Persisting candidates in a cache/`pubmed_records` table (second stale truth,
  premature content-store/copyright commitments).
- Persisting client-supplied bibliographic metadata on import (tamperable, and makes
  the provider merely decorative).
- Auto-creating Evidence on import (conflates fact with interpretation).
- An Agent import Tool / generalized `ActionRequest` (crosses the human authority
  boundary before its result-reference semantics are designed).
- Widening `LiteratureReference` with provider payload columns (a copy of external
  truth in Core; identity is `(authority, native_id)`).
