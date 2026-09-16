import { useMemo, useState } from 'react'
import { BookOpen, Download, FlaskConical } from 'lucide-react'

import { projectApi } from '../api/backend'
import { apiErrorMessage } from '../api/client'
import { useLiteratureDiscovery, useMyMembership, useProviders, useResources } from '../api/hooks'
import type { LiteratureCandidateRead, ProviderRead, ReferenceRead } from '../api/types'
import type { LiteratureDiscoveryQuery } from '../api/backend'
import { Button } from '../components/buttons'
import { Badge, Empty, ErrorBox, Field, Loading } from '../components/ui'
import {
  CAPABILITY_AVAILABILITY_AVAILABLE,
  CAPABILITY_KIND_LITERATURE_DISCOVERY,
  RESOURCE_KIND_LITERATURE,
  ROLE_MEMBER,
  ROLE_OWNER,
} from '../contracts/enums'

const MAX_QUERY_CHARS = 300
const IMPORTED_PAGE_SIZE = 50

/** External candidate presentation line: authors · journal · year. */
function citationLine(candidate: LiteratureCandidateRead): string {
  const parts: string[] = []
  if (candidate.authors && candidate.authors.length > 0) {
    const authors = candidate.authors.slice(0, 3).join(', ')
    parts.push(candidate.authors.length > 3 ? `${authors}, et al.` : authors)
  }
  if (candidate.journal) parts.push(candidate.journal)
  if (candidate.publication_year) parts.push(String(candidate.publication_year))
  return parts.join(' · ')
}

function literatureCapability(provider: ProviderRead) {
  return provider.capabilities?.find(
    (capability) => capability.kind === CAPABILITY_KIND_LITERATURE_DISCOVERY,
  )
}

/**
 * Literature / Discover: external literature discovery and EXPLICIT import.
 *
 * This surface makes the Phase-12/13 boundary visible. External candidates are
 * EPHEMERAL provider data ("not yet in Project"); only an explicit human Import
 * creates a durable `LiteratureReference` + `ProjectResourceLink`, after which it
 * becomes visible to the ordinary Project search. Importing is never
 * interpretation: `Use as Evidence` is a separate explicit step that hands off to
 * the EXISTING Evidence creation surface.
 *
 * Provider selection comes from the Provider Catalog filtered by the
 * backend-owned `literature_discovery` capability kind — never from a hard-coded
 * provider key.
 */
export function LiteratureView({
  actorId,
  projectId,
  onUseAsEvidence,
}: {
  actorId: string
  projectId: string
  onUseAsEvidence: (reference: ReferenceRead) => void
}) {
  const providers = useProviders(actorId, projectId)
  const membership = useMyMembership(actorId, projectId)
  const imported = useResources(actorId, projectId, RESOURCE_KIND_LITERATURE, {
    limit: IMPORTED_PAGE_SIZE,
  })

  const litProviders = useMemo(
    () => (providers.data ?? []).filter((provider) => literatureCapability(provider) !== undefined),
    [providers.data],
  )
  const [providerKey, setProviderKey] = useState<string | null>(null)
  const activeProvider =
    litProviders.find((provider) => provider.key === providerKey) ?? litProviders[0] ?? null
  const availability = activeProvider ? literatureCapability(activeProvider)?.availability : undefined
  const providerReady = availability === CAPABILITY_AVAILABILITY_AVAILABLE

  const [query, setQuery] = useState('')
  const [request, setRequest] = useState<LiteratureDiscoveryQuery | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [importingKey, setImportingKey] = useState<string | null>(null)
  const [importError, setImportError] = useState<string | null>(null)

  const discovery = useLiteratureDiscovery(actorId, projectId, request)
  const canMutate =
    membership.data?.role === ROLE_OWNER || membership.data?.role === ROLE_MEMBER

  function submit(event: React.FormEvent) {
    event.preventDefault()
    setFormError(null)
    setImportError(null)
    const text = query.trim()
    if (!text) {
      setFormError('Enter a search query.')
      setRequest(null)
      return
    }
    if (text.length > MAX_QUERY_CHARS) {
      setFormError(`Search queries are limited to ${MAX_QUERY_CHARS} characters.`)
      setRequest(null)
      return
    }
    if (!activeProvider) {
      setFormError('No literature discovery provider is available for this project.')
      setRequest(null)
      return
    }
    if (!providerReady) {
      setFormError('The selected provider is currently unavailable.')
      setRequest(null)
      return
    }
    setRequest({ provider_key: activeProvider.key, q: text, limit: 10 })
  }

  async function importCandidate(candidate: LiteratureCandidateRead) {
    setImportError(null)
    setImportingKey(candidate.native_id)
    // Only the STABLE identity is sent: the server re-resolves the publication at
    // the current provider and decides the persisted bibliographic data.
    const res = await projectApi(actorId).importLiterature(projectId, {
      provider_key: candidate.provider_key,
      authority: candidate.authority,
      native_id: candidate.native_id,
    })
    setImportingKey(null)
    if (res.error || !res.data) {
      setImportError(apiErrorMessage(res.error, res.response))
      return
    }
    imported.reload()
  }

  const showResults = !discovery.loading && discovery.data !== null
  const importedRows = imported.data ?? []

  return (
    <div className="view">
      <div className="view-header">
        <h1>Literature</h1>
        <p>
          Discover publications REvoLab does not yet know, then explicitly import one to make it
          Project context. An external candidate is not Project truth.
        </p>
      </div>

      <section className="content-section">
        <form className="search-form" role="search" onSubmit={submit}>
          <Field label="Provider">
            <select
              value={activeProvider?.key ?? ''}
              onChange={(event) => {
                setProviderKey(event.target.value)
                setRequest(null)
              }}
              disabled={litProviders.length === 0}
            >
              {litProviders.length === 0 ? <option value="">No provider installed</option> : null}
              {litProviders.map((provider) => {
                const capability = literatureCapability(provider)
                return (
                  <option key={provider.key} value={provider.key}>
                    {provider.name}
                    {capability?.availability === CAPABILITY_AVAILABILITY_AVAILABLE
                      ? ''
                      : ` (${capability?.availability ?? 'unavailable'})`}
                  </option>
                )
              })}
            </select>
          </Field>
          <Field label="Query">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              maxLength={MAX_QUERY_CHARS}
              placeholder="e.g. enzyme active-site redesign"
              aria-label="Literature search query"
            />
          </Field>
          <Button type="submit" disabled={discovery.loading || litProviders.length === 0}>
            {discovery.loading ? 'Searching…' : 'Search'}
          </Button>
        </form>
        <ErrorBox message={formError ?? membership.error} />
        <ErrorBox message={discovery.error} />

        {showResults ? (
          <section className="content-section">
            <div className="list-row-head">
              <BookOpen size={15} />
              <strong>External candidates</strong>
              <Badge tone="warn">external · not yet in Project</Badge>
            </div>
            {discovery.data && discovery.data.candidates && discovery.data.candidates.length > 0 ? (
              <div className="list">
                {discovery.data.candidates.map((candidate) => (
                  <div className="list-row" key={`${candidate.authority}:${candidate.native_id}`}>
                    <div className="list-row-head">
                      <strong>{candidate.title ?? '(untitled record)'}</strong>
                    </div>
                    {citationLine(candidate) ? <small>{citationLine(candidate)}</small> : null}
                    <small className="mono">
                      {candidate.authority}:{candidate.native_id}
                      {candidate.doi ? ` · doi ${candidate.doi}` : ''}
                    </small>
                    <div className="form-actions">
                      {canMutate ? (
                        <Button
                          type="button"
                          onClick={() => importCandidate(candidate)}
                          disabled={importingKey === candidate.native_id}
                        >
                          <Download size={14} />{' '}
                          {importingKey === candidate.native_id ? 'Importing…' : 'Import to Project'}
                        </Button>
                      ) : (
                        <small className="hint">Import requires owner or member membership.</small>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <Empty label="No external candidates matched that query." />
            )}
          </section>
        ) : null}

        <ErrorBox message={importError} />

        <section className="content-section">
          <div className="list-row-head">
            <FlaskConical size={15} />
            <strong>Imported literature</strong>
            <Badge tone="good">Project context</Badge>
          </div>
          {imported.loading ? <Loading /> : null}
          <ErrorBox message={imported.error} />
          {!imported.loading && importedRows.length === 0 ? (
            <Empty label="No publication has been imported into this project yet." />
          ) : null}
          <div className="list">
            {importedRows.map((reference) => (
              <div className="list-row" key={reference.resource_id}>
                <div className="list-row-head">
                  <strong>{reference.title ?? '(untitled record)'}</strong>
                  <Badge>literature</Badge>
                </div>
                <small className="mono">
                  {reference.authority}:{reference.native_id}
                </small>
                <div className="form-actions">
                  {canMutate ? (
                    <Button type="button" kind="quiet" onClick={() => onUseAsEvidence(reference)}>
                      Use as Evidence
                    </Button>
                  ) : (
                    <small className="hint">
                      Interpreting a publication as Evidence requires owner or member membership.
                    </small>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
      </section>
    </div>
  )
}
