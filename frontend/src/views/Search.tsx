import { useMemo, useState } from 'react'
import { Search as SearchIcon, ShieldAlert } from 'lucide-react'

import { useProjectSearch } from '../api/hooks'
import type { SearchHitRead, SearchScope, SearchTargetKind } from '../api/types'
import { Button } from '../components/buttons'
import { Badge, Empty, ErrorBox, Field, Loading } from '../components/ui'
import {
  DECISION_STATUS_COMMITTED,
  SEARCH_SCOPE_ALL,
  SEARCH_SCOPE_MY_CONVERSATIONS,
  SEARCH_SCOPE_PROJECT_SHARED,
  SEARCH_SCOPES,
} from '../contracts/enums'
import { contextItemFromHit, kindsForScope, targetKindLabel } from './agentContext'

const RESULT_LIMIT = 20
const QUERY_MAX = 200

function scopeLabel(scope: SearchScope): string {
  switch (scope) {
    case SEARCH_SCOPE_PROJECT_SHARED:
      return 'Project shared context'
    case SEARCH_SCOPE_MY_CONVERSATIONS:
      return 'My private conversations'
    case SEARCH_SCOPE_ALL:
      return 'Shared context + my conversations'
    default:
      return scope
  }
}

type SearchRequest = {
  q: string
  scope: SearchScope
  target_kinds?: SearchTargetKind[]
  limit: number
}

/**
 * Project-level search surface (Phase 12).
 *
 * Search DISCOVERS candidate references. It never creates truth and never enters
 * Agent context by itself: only the explicit "Add to Agent context" action does,
 * and it does so through the existing canonical `ContextSelectionCreate`.
 * Conversation hits are Actor-private working memory and are never selectable
 * into shared Agent context.
 */
export function SearchView({
  actorId,
  projectId,
  onOpenHit,
  onAddToAgentContext,
}: {
  actorId: string
  projectId: string
  onOpenHit: (hit: SearchHitRead) => void
  onAddToAgentContext: (hit: SearchHitRead) => void
}) {
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState<SearchScope>(SEARCH_SCOPE_PROJECT_SHARED)
  const [kind, setKind] = useState<SearchTargetKind | ''>('')
  const [request, setRequest] = useState<SearchRequest | null>(null)
  const [formError, setFormError] = useState<string | null>(null)

  const { data, loading, error } = useProjectSearch(actorId, projectId, request)

  // Group by the backend-owned target kind, preserving the server's ordering
  // within each group. Ordering (not a numeric score) is the search contract.
  const groups = useMemo(() => {
    const grouped = new Map<SearchTargetKind, SearchHitRead[]>()
    for (const hit of data?.hits ?? []) {
      const bucket = grouped.get(hit.target_kind) ?? []
      bucket.push(hit)
      grouped.set(hit.target_kind, bucket)
    }
    return [...grouped.entries()]
  }, [data])

  function submit(event: React.FormEvent) {
    event.preventDefault()
    const trimmed = query.trim()
    if (!trimmed) {
      setFormError('Enter something to search for.')
      return
    }
    if (trimmed.length > QUERY_MAX) {
      setFormError(`Search text is limited to ${QUERY_MAX} characters.`)
      return
    }
    setFormError(null)
    setRequest({
      q: trimmed,
      scope,
      ...(kind ? { target_kinds: [kind] } : {}),
      limit: RESULT_LIMIT,
    })
  }

  function changeScope(next: SearchScope) {
    setScope(next)
    // A scope change invalidates the previous result set AND any target kind the
    // new scope does not allow (in BOTH directions: `conversation` is private-only
    // and the shared kinds are unavailable in MY_CONVERSATIONS). Never send a
    // request the backend must reject.
    if (kind && !kindsForScope(next).includes(kind)) {
      setKind('')
    }
    setRequest(null)
  }

  const privateScope =
    scope === SEARCH_SCOPE_MY_CONVERSATIONS || scope === SEARCH_SCOPE_ALL
  const hits = data?.hits ?? []
  const total = hits.length
  // Results render only for a settled request: a previous scope's hits must never
  // stay mounted under the new scope's heading while the new query is in flight.
  const showResults = !loading && data !== null

  return (
    <div className="view">
      <div className="view-header">
        <h1>Search</h1>
        <p>
          Find Project context you already have — objects, Evidence, Decisions, Notes and durable
          references. Results are candidate references: nothing enters an Agent turn until you add it
          explicitly.
        </p>
      </div>

      <section className="content-section">
        <form className="search-form" onSubmit={submit} role="search">
          <Field label="Search text">
            <input
              aria-label="Search project context"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="e.g. substrate positioning, P12345, native-123"
              maxLength={QUERY_MAX}
            />
          </Field>
          <Field label="Scope">
            <select
              aria-label="Search scope"
              value={scope}
              onChange={(event) => changeScope(event.target.value as SearchScope)}
            >
              {SEARCH_SCOPES.map((option) => (
                <option key={option} value={option}>
                  {scopeLabel(option)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Target kind">
            <select
              aria-label="Search target kind"
              value={kind}
              onChange={(event) => {
                setKind(event.target.value as SearchTargetKind | '')
                setRequest(null)
              }}
            >
              <option value="">All kinds</option>
              {kindsForScope(scope).map((option) => (
                <option key={option} value={option}>
                  {targetKindLabel(option)}
                </option>
              ))}
            </select>
          </Field>
          <div className="form-actions">
            <Button type="submit" disabled={loading}>
              <SearchIcon size={15} /> {loading ? 'Searching…' : 'Search'}
            </Button>
          </div>
        </form>

        <ErrorBox message={formError ?? error} />

        {privateScope ? (
          <p className="scope-note">
            <ShieldAlert size={14} /> This scope includes MY private conversation history — working
            memory, never shared Project knowledge and never part of an Agent turn.
          </p>
        ) : null}

        {loading ? <Loading label="Searching…" /> : null}

        {showResults && total === 0 ? (
          <Empty label="No authorized results for this query." />
        ) : null}

        {showResults && total > 0 ? (
          <div className="search-results">
            <div className="search-summary">
              {total} result{total === 1 ? '' : 's'}
              {data.truncated ? ' (bounded: more matches exist)' : ''}
            </div>
            {groups.map(([groupKind, hits]) => (
              <div className="search-group" key={groupKind}>
                <h2 className="search-group-title">{targetKindLabel(groupKind)}</h2>
                <div className="list">
                  {hits.map((hit) => {
                    const selectable = contextItemFromHit(hit) !== null
                    return (
                      <div
                        className={`list-row search-hit${hit.private ? ' search-hit-private' : ''}`}
                        key={`${hit.target_kind}:${hit.target_id}`}
                      >
                        <div className="list-row-head">
                          <strong>{hit.title}</strong>
                          {hit.private ? <Badge tone="warn">private working memory</Badge> : null}
                          {hit.status ? (
                            <Badge
                              tone={hit.status === DECISION_STATUS_COMMITTED ? 'good' : 'warn'}
                            >
                              {hit.status}
                            </Badge>
                          ) : null}
                          {hit.matched_field ? <Badge>{hit.matched_field}</Badge> : null}
                        </div>
                        {hit.snippet ? <p className="search-snippet">{hit.snippet}</p> : null}
                        <small className="mono">
                          {hit.target_kind} · {hit.target_id.slice(0, 8)}…
                        </small>
                        <div className="form-actions">
                          <Button onClick={() => onOpenHit(hit)}>Open</Button>
                          {selectable ? (
                            <Button onClick={() => onAddToAgentContext(hit)}>
                              Add to Agent context
                            </Button>
                          ) : null}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        ) : null}

        {request ? (
          <p className="scope-note">
            Search results are references only. Use “Add to Agent context” to include a result in your
            next Agent turn; search itself never changes what the Agent can read.
          </p>
        ) : null}
      </section>
    </div>
  )
}
