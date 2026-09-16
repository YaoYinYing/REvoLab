---
name: project-context
version: 0.2.0
description: Build project-scoped context for an agent interaction.
---

# Project Context

Select a project, relevant object subtree, relations, evidence, decisions, and next actions. Keep context bounded to the active scientific question. Chat history is not project source of truth; promote durable conclusions into structured records.

## Three distinct steps — never conflate them

```text
Search             discover candidate canonical references (read projection)
ContextSelection   explicit declaration of what ONE Agent turn may read
ContextBuilder     current canonical truth -> bounded ProjectContext
```

- A search result is a **candidate reference**, not truth, not authority, and not
  a context inclusion. Retrieval never enlarges what an Agent turn may read.
- Only an explicit `ContextSelectionCreate` authorizes inclusion BY IDENTITY. To
  include something found by search, add its canonical id to the matching typed
  selection field — never a generic opaque id bag.
- `ContextBuilder` alone turns current canonical truth into `ProjectContext`, and
  it re-validates every explicitly selected identity against the current Project
  read lens on every build. Search-time authorization is a query result, never a
  cached grant.

## Canonical sources

Inspect before acting; do not copy contracts:

- `docs/architecture/PROJECT_SEARCH_RETRIEVAL.md` (search/retrieval semantics)
- `docs/architecture/AGENT_CONTEXT.md` (what enters context automatically vs explicitly)
- `docs/architecture/adr/ADR-0018-project-search-read-projection.md`
- the live OpenAPI contract (`/openapi.json`, `frontend/src/contracts/openapi.json`)
  for `ContextSelectionCreate`, `ProjectSearchResultsRead`, and `SearchHitRead`

`SearchScope` and `SearchTargetKind` are backend-owned enums. Never restate their
values here or in application code; consume the generated contract instead.

## Boundary

Search finds what REvoLab already knows. Private conversations are working memory,
are searchable only by their own Actor, and are never shared Agent context. Do not
introduce a second context model (`SearchContext`, `SearchMemory`,
`RetrievalContext`), RAG/embeddings, or automatic per-turn search.
