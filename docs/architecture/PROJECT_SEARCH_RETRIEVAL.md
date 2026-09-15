# Project Search & Bounded Context Retrieval

> **Status: Proposed — pending human acceptance** (Phase 12; normative owner of the
> search/retrieval sub-boundary). Related ADR:
> `adr/ADR-0018-project-search-read-projection.md` (**Proposed — pending human
> acceptance**). Both are promoted only by explicit human acceptance at PR review;
> a green test suite does not promote them.
>
> This document is normative for the Project search sub-boundary. Search is an
> **application/query sub-boundary consumed by Presentation and Agent Context** —
> the same placement family as `PROJECT_CONVERSATIONS.md`,
> `PROJECT_NOTEBOOK.md`, and `AGENT_ACTION_HANDOFF.md`. It does **not** introduce a
> tenth Core domain, and the accepted nine-domain DAG is unchanged.

Related: `SYSTEM_ARCHITECTURE.md`, `DOMAIN_BOUNDARIES.md`, `AGENT_CONTEXT.md`,
`WORKSPACE_INFORMATION_ARCHITECTURE.md`, `PROJECT_TOOL_HARNESS.md`,
`PROJECT_CONVERSATIONS.md`, `PROJECT_NOTEBOOK.md`, `SCIENTIFIC_GRAPH.md`,
ADR-0008, ADR-0013, ADR-0016, ADR-0017.

---

## 1. The invariant

> **Search discovers references; it never creates truth, widens authority, or
> silently enlarges Agent context.**

> **Retrieve first, select explicitly, then build context.**

Three distinct steps, never conflated:

```text
Search             = discover candidate canonical references
ContextSelection   = explicit human declaration of what ONE Agent turn may read
ContextBuilder     = current canonical truth -> bounded ProjectContext
```

A SearchHit is **not** a ProjectContext inclusion, **not** Evidence/Decision truth,
**not** a provenance edge, **not** Agent memory, and **not** a durable index entry.
It is a read projection pointing back at an identity an existing domain owns.

Search is explicitly **not**: a Core domain; a second scientific data model; an
embedding store; a vector/RAG pipeline; a Knowledge Graph; a provider; a workflow
engine; a background indexing service; or a persistent context snapshot.

---

## 2. Ownership

Search owns exactly:

```text
query parsing + bounds
authorized retrieval
ranking
bounded plain-text snippets
the typed SearchHit projection
```

Search owns **nothing else**. Every returned row stays owned by its canonical
domain: `ScientificObjectSeries`/`Revision`, `Evidence`, `Decision`,
`ProjectNote`/`ProjectNoteRevision`, `RunReference`, `ArtifactReference`,
`LiteratureReference`, `ExternalReference`, `ProjectConversation`. Project
visibility/membership remain owned by Identity/Collaboration and Project; Agent
context remains owned by the ContextBuilder.

Canonical domain rows are the only source of truth. There is **no central copied
`SearchDocument` table**: a canonical row plus a copied search row would create a
synchronization problem and a stale-authorization risk. Every corpus is queried
directly from its canonical table through the existing read lens, so
deleting/revising/archiving a canonical object never requires updating a second
hand-maintained truth store.

### Dependency direction (frozen)

```text
Presentation (workspace) ---\
                             >-- revolab.search (application/query projection)
Agent Context / Tools  -----/          |
                                       | reads (never writes)
                                       v
   Project read lens + canonical domain tables (identity, scientific object,
   evidence/provenance, knowledge, notes, conversations, references)
```

`revolab.search` imports the existing read/authorization primitives
(`domain.identity.readable_membership`, `ProjectResourceLink`) and canonical ORM
rows. It never imports FastAPI, never imports the Agent, and nothing in Core
imports it.

---

## 3. Search scopes

A small, explicit, closed scope model:

```text
PROJECT_SHARED     Project-shared context readable by every member
MY_CONVERSATIONS   the calling Actor's OWN durable working memory
ALL                the explicit union, human workspace only
```

The human workspace may search shared context, private conversations, or both.

The Agent-facing `project.search` Tool has **no scope parameter at all**: it is
fixed to `PROJECT_SHARED`, so private working memory cannot even be requested
through the Agent boundary. There is deliberately no "all users' conversations"
scope.

---

## 4. Searchable corpora (Phase 12)

Only corpora with canonical searchable fields and an existing workspace surface
are searchable. Phase 12 searches what REvoLab already knows.

### Project-shared

| Target kind | Searched fields | Canonical identity | Default lifecycle filter |
| --- | --- | --- | --- |
| `scientific_object_series` | `name`, `description`, `object_type`, canonical external identifiers (`ExternalIdentity.authority`/`native_id`), aliases | `series_id` | active (not archived) |
| `evidence` | `label`, `interpretation`, `scope` | `Evidence.id` | active (not archived) |
| `decision` | `title`, `statement`, `next_actions` | `Decision.id` (status shown) | active (not archived) |
| `note` | `title`, **latest revision** `body` | `ProjectNote.id` | active (not archived) |
| `run_reference` | `authority`, `native_id`, `task_type` | `run_id` | not revoked |
| `artifact_reference` | `authority`, `native_id`, `checksum`, `content_type`, `version_id` | `artifact_id` | not revoked |
| `literature_reference` | `authority`, `native_id`, `title` | `literature_id` | — |
| `external_reference` | `authority`, `native_id`, `checksum` | `external_reference_id` | — |

Large sequence/structure/artifact **contents** are not full-text indexed, and
arbitrary filesystem content is never searched. Search uses stored
identity/header metadata only: it never resolves a provider, never fetches live
run state, and never reads artifact bytes. Provider disappearance leaves the
searchability of stored references intact, and search keeps working when
REvoCompute is offline.

### Actor-private (working memory)

| Target kind | Searched fields | Canonical identity |
| --- | --- | --- |
| `conversation` | `title`, persisted message `content` | `ProjectConversation.id` |

Ownership follows the Phase-9 Actor × Project conversation lens exactly: an Actor
only ever matches their OWN conversations in the current Project. Conversation
hits are marked `private = true` in the wire contract and are **not** selectable
into shared Agent context.

`SearchTargetKind` is a retrieval/presentation classifier owned by the backend; it
is deliberately **not** `ResourceKind` (it also names Project-local, non-global
targets) and `ResourceKind` is not overloaded for search convenience.

**Deliberate Phase-12 omission:** `session_reference` is a real, Project-linked,
navigable reference kind (`GET /resources` returns it and Runs & Artifacts renders
it as "Sessions") but is **not** a Phase-12 search target. It has no required
Phase-12 retrieval use case, and TODO.md section 20 only authorizes kinds actually
returned in this phase; adding it would be speculative vocabulary. It is recorded
here rather than silently absent and can be added later behind the SAME
authorized-query -> SearchHit contract.

---

## 5. Authorization before disclosure

Authorization is more important than ranking. It is re-derived from CURRENT
canonical state on every query:

```text
Project exists and is active (not tombstoned)
Actor holds a readable membership in that Project
global resources reach the row ONLY through this Project's ProjectResourceLink
Project-scoped Evidence/Decision rows belong to this Project and are unarchived
Note search reads only the CURRENT latest revision and excludes archived Notes
the conversation corpus is Actor x Project scoped
```

An unauthorized row is never ranked, counted, snippeted, or reported as a hidden
result — each would be an existence oracle. A unique query matching only an
inaccessible resource looks exactly like no result.

**No cross-Project leakage.** `GlobalResourceRegistry` existence is not Project
visibility: a resource linked to Project A but not Project B does not appear in
Project B by exact UUID, `native_id`, checksum, unique title, or unique phrase.

---

## 6. Ranking (small, documented, non-semantic)

Ranking is a small deterministic key, computed in SQL, in this order:

```text
0  exact canonical / external identifier match (UUID, native_id, checksum)
1  exact title/name match
2  title/name prefix match
3  other authorized lexical match
then  PostgreSQL native ts_rank (configuration `simple`)
then  recency, then a stable identity tie-break
```

There is **no ML ranking, no embedding ranking, and no external search service**.
The numeric relevance is never exposed: the ORDER of hits is the contract, and a
score would invite reading relevance as scientific confidence. Search relevance is
also unrelated to Evidence `confidence` and never changes Decision promotion
semantics — a matched Decision is not "true because search ranked it highly".

---

## 7. Query language and bounds

Search text is plain untrusted user/model input. The query language is a bounded
term list; every term is matched as a case-insensitive substring in SQL through
bound parameters. Scientific identifiers (`P12345`, `L72M/Q122A`, `8x3e`,
`native-123`) remain searchable and are never English-stemmed.

Explicit ceilings (fail closed when exceeded, never silently widened):

```text
query length             200 characters
query terms              8
single term length       64 characters
requested target kinds   the closed target-kind set
result limit             1..50 (default 20 for the workspace, 10 for the Tool)
title length             200 characters
snippet length           240 characters
total returned text      at most 22000 characters (limit x (title + snippet))
per-corpus candidates    limit + 1, ranked and bounded in SQL
```

An exact canonical UUID (hyphenated, compact, or braced) is itself a valid one-term
query: it is matched against the canonical identity column and ranks first, still
only inside the corpus's authorization filter, so it is never an existence oracle.

`raw SQL`, PostgreSQL `tsquery` syntax, regex, filesystem globs, URLs, Python, and
shell are never accepted as query language. All database interaction is
parameterized.

---

## 8. Backends: PostgreSQL is acceptance truth

PostgreSQL is the search acceptance truth. Matching itself is one deterministic
parameterized token-substring predicate. Both backends share the same **semantic**
contract (authorization, target classes, bounds, SearchHit shape), but case folding
is the database's `lower()`: PostgreSQL folds per the database locale (so `CAFÉ`
matches `café`) while SQLite folds ASCII only. That non-ASCII difference is an
accepted SQLite substrate limitation (TODO.md section 13 allows a simpler SQLite
fallback) and never affects authorization or bounds. PostgreSQL additionally
contributes native `to_tsvector`/`ts_rank` (configuration `simple`, language
neutral) as an ordering signal.

```text
semantic contract:   identical on both backends (authorization + target classes
                     + bounds + SearchHit shape); non-ASCII case folding differs
                     because it is the database's lower()
ranking contract:    PostgreSQL is the production/acceptance truth; SQLite uses
                     the same primary keys without the native text rank
```

Neither backend materializes the Project in Python: every corpus query filters,
authorizes, ranks, and caps inside one bounded SQL statement, and only the bounded
candidate set (at most `limit + 1` per corpus) is merged in the application.

**No persisted derived index in Phase 12.** Search queries canonical columns
directly. A denormalized/persisted search index (including any PostgreSQL
expression index tuned for retrieval) is deliberately deferred: it would be a
second derived representation whose freshness and authorization semantics require
their own ADR (see §2 and TODO.md section 5). Phase 12 therefore adds **no
migration**; `alembic check` must stay drift-clean on both substrates.

---

## 9. Snippets

Snippets are presentation data: bounded plain text extracted in the application
(never `ts_headline` markup), with control characters removed and everything else
preserved verbatim. Hostile stored Markdown/HTML stays **inert text**; the
workspace renders it as a text node and never uses raw HTML injection. A snippet
never contains credential/provider payloads, artifact bytes, or a full
conversation dump.

---

## 10. Search ≠ Context: the explicit handoff

A search result never enters `ProjectContext` automatically, and search results
are never persisted as Agent memory or hidden conversation context.

The human workspace offers an explicit **"Add to Agent context"** action for the
target kinds that the canonical `ContextSelectionCreate` can address. It reuses
that ONE selection contract — no `SearchContext`, `SearchMemory`, or
`RetrievalContext` is introduced. Phase 12 extends the selection minimally and
canonically with typed id lists for the previously unaddressable kinds:

```text
series_ids, revision_ids, artifact_ids, note_ids, note_revision_ids   (existing)
evidence_ids                                                          (Phase 12)
decision_ids                                                          (Phase 12)
reference_ids   # reference identity cards only (run/session/artifact/
                # literature/external); NOT a generic resource-id bag
```

The user sees the pending selection before sending the turn. The ContextBuilder
re-validates every selected id against the CURRENT Project read lens; a stale,
foreign, archived, or wrong-kind id fails closed, because search-time
authorization is a query result, never a cached grant.

Conversation hits are private working memory and are never selectable into shared
Agent context.

An explicit evidence/decision/reference selection is honored regardless of the
`include_*` category switches (an explicit identity is a stronger declaration than a
category toggle and is never silently dropped), and it fails closed for a stale,
foreign, archived, revoked, or wrong-kind id.

**Navigation is best-effort selection, not a second surface.** "Open" reuses the
existing canonical view and selects/highlights the matching row when it is inside
that view's loaded page (50 rows). A target outside the loaded page still navigates
to the correct surface but may not be scrolled to/highlighted; no parallel detail
page or object model is introduced to work around it.

---

## 11. The Agent `project.search` Tool

One local read-only Tool:

```text
id                 project.search
autonomy           automatic
execution_class    local
side_effect_class  read_only
requires_mutation  false
input              { query, target_kinds?, limit }   # no scope field
output             bounded ProjectSearchResultsRead (PROJECT_SHARED hits only)
```

It calls the SAME search application service the human workspace calls — there is
no Agent-only search engine. It never searches other Actors' conversations, the
current Actor's conversations, Action Requests, credentials, hidden provider data,
the filesystem, or the Internet during Phase 12.

A search Tool execution never persists data, changes a ContextSelection,
promotes Evidence/Decision, executes an Action Request, resolves provider content,
or reads artifact bytes. Its result consumes the EXISTING bounded Agent tool-result
budget.

Search snippets originate from Project data, so they remain on the untrusted-data
side of the prompt/tool-result boundary (prompt-injection invariants of Phases
8–11 unchanged). A Note body containing `SYSTEM: ignore all rules` can be returned
as a hit, but it cannot change Tool autonomy, create or approve an Action Request,
widen the ToolCatalog, reveal credentials, or modify Project context.

---

## 12. Explicit non-goals (Phase 12)

```text
embeddings / vector database / pgvector / semantic similarity
LLM-generated search index / chunking pipeline / RAG prompt injection
AgentMemory / memory summarization / automatic search every turn
background indexing service / Elasticsearch / external search SaaS
external biological or Web search (PubMed, UniProt, RCSB, Crossref, OpenBio)
central copied SearchDocument truth
saved searches / query DSL / Boolean builder / faceted engine
```

Phase 12 establishes the durable contract future retrieval must obey:

```text
Phase 12:                search what REvoLab already knows
Future external knowledge: discover/import knowledge REvoLab does not yet know
Future semantic retrieval: optional quality improvement behind the SAME
                           authorized-search -> SearchHit -> explicit selection ->
                           ContextBuilder contract
```

---

## Document history

- Phase 12 (this document): first Project-wide retrieval surface; PostgreSQL
  lexical retrieval with native ranking; no derived index; explicit
  Search → ContextSelection handoff; read-only `project.search` Agent Tool.
