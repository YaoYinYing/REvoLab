# ADR-0018: Project Search Is an Authorization-Aware Read Projection

> **Status: Proposed — pending human acceptance.**
> Accepted only by explicit human acceptance; a green test suite does not promote
> this ADR's status.

## Context

Phases 1–11 established canonical Project truth plus the two ways to *use* it: an
explicit `ContextSelection` for one Agent turn and a bounded `ProjectContext` built
from current truth by the ContextBuilder. What was missing is **discovery**: a
member who knows a Project contains something relevant but does not know its
identity had no way to find it. Existing reads are enumerations (`list_objects`,
`list_evidence`, …) with no lexical retrieval.

The obvious shortcuts are all unsafe in this architecture:

- copying searchable text into a new `SearchDocument` truth creates a
  synchronization problem and a **stale-authorization** risk (a copied
  `visibility` column can outlive the link that granted it);
- a Project-wide scan materialized in Python does not scale and makes
  authorization/ranking impossible to bound;
- embeddings/vector/RAG retrieval would introduce a second representation of
  scientific meaning and an implicit "search feeds the Agent" path;
- letting a search result enter Agent context automatically would conflate
  discovery with authorization.

What was not yet decided is **who owns search, what a search result means, how
authorization is enforced, and how retrieval connects to context**.

## Decision

### Search is an application/query sub-boundary, not a tenth Core domain

`revolab.search` is an application-level read projection consumed by Presentation
and Agent Context — the same placement family as the Conversations, Notebook, and
Action Handoff sub-boundaries. It adds no Core domain and no Project → domain edge;
the accepted nine-domain DAG is unchanged. It imports the existing read lens
(`readable_membership`, `ProjectResourceLink`) and canonical ORM rows, and never
imports FastAPI or the Agent.

Search owns only: query parsing and bounds, authorized retrieval, ranking, bounded
plain-text snippets, and the typed SearchHit projection. Every returned row stays
owned by its canonical domain.

### Canonical rows are the only truth; no copied SearchDocument

Each corpus is queried directly from its canonical table under the existing read
lens. A central denormalized `SearchDocument` table is **prohibited** because
`canonical row + copied search row = synchronization problem + stale authorization
risk`. Deleting, revising, or archiving a canonical object therefore requires no
update to a second hand-maintained store. A future denormalized index may only be
introduced by a separate ADR defining its freshness and authorization semantics.

### A SearchHit is a read projection, never truth, authority, or context

```text
SearchHit {
    target_kind      # backend-owned closed enum (NOT ResourceKind)
    target_id        # canonical REvoLab identity
    title            # bounded plain text
    snippet?         # bounded plain text, inert
    matched_field?   # presentation metadata
    private          # true only for an Actor-private conversation hit
}
```

The numeric relevance is deliberately **not** exposed: ordered results are the
contract, and a score would invite reading relevance as scientific confidence.
`SearchTargetKind` is a retrieval/presentation classifier; it is not `ResourceKind`
(which stays the global-resource registry vocabulary) and it is not overloaded for
search convenience.

### Authorization before disclosure, re-derived on every query

The Project must be active, the Actor must hold a readable membership, global
resources are reachable only through this Project's `ProjectResourceLink`, Project
Evidence/Decision rows must be current and unarchived, Note search uses only the
current latest revision, and the conversation corpus is Actor × Project scoped.
An unauthorized row is never ranked, counted, snippeted, or reported as a hidden
result: a unique query matching only an inaccessible resource looks like no result.
`GlobalResourceRegistry` existence is never Project visibility, so no
cross-Project leakage or global-resource existence oracle is possible.

### Private conversations are searchable only by their own Actor, and never by the Agent

```text
PROJECT_SHARED     Project-shared context
MY_CONVERSATIONS   the calling Actor's OWN durable working memory
ALL                explicit union (human workspace only)
```

The Agent-facing `project.search` Tool accepts **no scope**: it is structurally
fixed to `PROJECT_SHARED`. Conversation hits are marked private and are not
selectable into shared Agent context. There is no "all users' conversations" scope.

### PostgreSQL is acceptance truth; no derived index in this phase

Matching is one deterministic parameterized token-substring predicate. PostgreSQL
and SQLite share the same semantic contract (authorization, target classes, bounds,
SearchHit shape); case folding is the database's `lower()`, so PostgreSQL folds per
its locale while SQLite folds ASCII only (an accepted substrate limitation).
PostgreSQL adds native `to_tsvector`/`ts_rank` (configuration `simple`, language
neutral) as an ordering signal, and an exact canonical UUID is matched against the
identity column under the same authorization filter. Every corpus filters, authorizes, ranks, and caps inside one bounded SQL
statement; the Project is never materialized in Python. Phase 12 adds no migration:
a persisted search index would be a second derived representation requiring its own
freshness/authorization ADR.

### Search results enter Agent context only through the existing explicit selection

`Search → ContextSelection → ContextBuilder` is preserved unchanged. A search result
never auto-enters context, is never persisted as Agent memory, and is never stored
as hidden conversation context. The human "Add to Agent context" action reuses the
canonical `ContextSelectionCreate`; Phase 12 extends it minimally with typed
`evidence_ids`, `decision_ids`, and `reference_ids` (reference identity cards only —
not a generic resource-id bag) for target kinds that were previously reachable only
transitively. Search-time authorization is never a cached grant: the ContextBuilder
re-validates every selected id against current truth and fails closed.

### Snippets are bounded inert plain text

Snippets are extracted in the application (never as `ts_headline` markup) as
bounded plain text with control characters removed. Hostile stored Markdown/HTML
remains inert; nothing is rendered as raw HTML. Snippet text returned to a model
stays on the untrusted-data side of the tool-result boundary.

### Phase 12 deliberately excludes semantic and external retrieval

No embeddings, vector database, pgvector, RAG, semantic memory, background indexing
service, workflow infrastructure, second context model, or external
biological/Web search. Phase 12 establishes the contract those future capabilities
must plug in behind, not around.

## Consequences

- Discovery has exactly one durable owner and one canonical direction:
  `authorized query → bounded SearchHit references → explicit selection →
  ContextBuilder`.
- The Agent gains a bounded read-only `project.search` Tool that reuses the human
  search service; the Tool's autonomy/side-effect classes (`automatic`,
  `read_only`) make it non-mutating by construction, and its result consumes the
  existing tool-result budget.
- Authorization is enforced in SQL before disclosure, so an existence oracle via
  ranking, counts, snippets, or "hidden result" reporting does not arise.
- No migration or index is added; `alembic upgrade head` and `alembic check` remain
  drift-clean on SQLite and PostgreSQL 16.
- Search semantics live in one normative document
  (`docs/architecture/PROJECT_SEARCH_RETRIEVAL.md`); consuming documents point at it
  and do not restate the contract.
- Explicitly deferred (and owned by future ADRs): persisted search indexes,
  semantic/vector retrieval, historical Note-revision search, external
  knowledge search/import, and cross-Actor conversation search.
