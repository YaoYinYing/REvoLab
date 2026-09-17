import { useMemo, useState } from 'react'
import { Dna, Download, FlaskConical } from 'lucide-react'

import { projectApi } from '../api/backend'
import { apiErrorMessage } from '../api/client'
import { useMyMembership, useProteinDiscovery, useProviders } from '../api/hooks'
import type { ProteinCandidateRead, ProviderRead } from '../api/types'
import type { ProteinDiscoveryQuery } from '../api/backend'
import { Button } from '../components/buttons'
import { Badge, Empty, ErrorBox, Field, Loading } from '../components/ui'
import {
  CAPABILITY_AVAILABILITY_AVAILABLE,
  CAPABILITY_KIND_PROTEIN_DISCOVERY,
  ROLE_MEMBER,
  ROLE_OWNER,
} from '../contracts/enums'

// Presentation-only mirror of the backend bound. The wire contract is the
// authority: the backend fails closed on an over-bound query/limit (and the
// provider clamps the result count), so drift here can never widen the surface.
const MAX_QUERY_CHARS = 300
const RESULT_LIMIT = 10

/**
 * Data-source attribution for external protein metadata.
 *
 * This is legal/terms presentation, NOT capability semantics: the generic frontend
 * still discovers protein providers by the backend-owned `protein_discovery`
 * capability kind and never branches on a provider key to decide what a capability
 * DOES. UniProt's license requires attribution of the source database, so the
 * provider that supplied the metadata is attributed here with a link.
 */
const PROVIDER_ATTRIBUTION: Record<string, { label: string; url: string }> = {
  uniprot: {
    label: 'UniProt (CC BY 4.0)',
    url: 'https://www.uniprot.org/help/license',
  },
}

function proteinCapability(provider: ProviderRead) {
  return provider.capabilities?.find(
    (capability) => capability.kind === CAPABILITY_KIND_PROTEIN_DISCOVERY,
  )
}

/** External candidate presentation line: gene · organism · length. */
function descriptorLine(candidate: ProteinCandidateRead): string {
  const parts: string[] = []
  if (candidate.gene_name) parts.push(candidate.gene_name)
  if (candidate.organism_name) parts.push(candidate.organism_name)
  if (candidate.sequence_length) parts.push(`${candidate.sequence_length} aa`)
  if (candidate.reviewed === true) parts.push('reviewed')
  else if (candidate.reviewed === false) parts.push('unreviewed')
  return parts.join(' · ')
}

/**
 * Objects / Discover proteins: external protein discovery and EXPLICIT import.
 *
 * This surface makes the Phase-14 boundary visible. External candidates are
 * EPHEMERAL provider data ("not yet in Project"); only an explicit human Import
 * creates the canonical Protein + Sequence ScientificObjects, after which they
 * become visible to the ordinary Project search and object detail surfaces.
 * Importing is never interpretation: it creates no Evidence and no Decision.
 *
 * Provider selection comes from the Provider Catalog filtered by the backend-owned
 * `protein_discovery` capability kind — never from a hard-coded provider key.
 */
export function ProteinDiscoveryView({
  actorId,
  projectId,
  onOpenObject,
}: {
  actorId: string
  projectId: string
  onOpenObject: (seriesId: string) => void
}) {
  const providers = useProviders(actorId, projectId)
  const membership = useMyMembership(actorId, projectId)

  const proteinProviders = useMemo(
    () => (providers.data ?? []).filter((provider) => proteinCapability(provider) !== undefined),
    [providers.data],
  )
  const [providerKey, setProviderKey] = useState<string | null>(null)
  const activeProvider =
    proteinProviders.find((provider) => provider.key === providerKey) ?? proteinProviders[0] ?? null
  const availability = activeProvider ? proteinCapability(activeProvider)?.availability : undefined
  const providerReady = availability === CAPABILITY_AVAILABILITY_AVAILABLE

  const attribution = activeProvider ? PROVIDER_ATTRIBUTION[activeProvider.key] : undefined

  const [query, setQuery] = useState('')
  const [request, setRequest] = useState<ProteinDiscoveryQuery | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [importingKey, setImportingKey] = useState<string | null>(null)
  const [importError, setImportError] = useState<string | null>(null)
  // The canonical objects an import produced or reused: shown as links into the
  // EXISTING object-detail surface (never a second object view).
  const [imported, setImported] = useState<
    { authority: string; native_id: string; protein: string; sequence: string } | null
  >(null)

  const discovery = useProteinDiscovery(actorId, projectId, request)
  const canMutate = membership.data?.role === ROLE_OWNER || membership.data?.role === ROLE_MEMBER

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
      setFormError('No protein discovery provider is available for this project.')
      setRequest(null)
      return
    }
    if (!providerReady) {
      setFormError('The selected provider is currently unavailable.')
      setRequest(null)
      return
    }
    setRequest({ provider_key: activeProvider.key, q: text, limit: RESULT_LIMIT })
  }

  async function importCandidate(candidate: ProteinCandidateRead) {
    setImportError(null)
    setImportingKey(candidate.native_id)
    // Only the STABLE identity is sent: the server re-resolves the record at the
    // current provider and decides every persisted scientific value.
    const res = await projectApi(actorId).importProtein(projectId, {
      provider_key: candidate.provider_key,
      authority: candidate.authority,
      native_id: candidate.native_id,
    })
    setImportingKey(null)
    if (res.error || !res.data) {
      setImportError(apiErrorMessage(res.error, res.response))
      return
    }
    setImported({
      authority: res.data.authority,
      native_id: res.data.native_id,
      protein: res.data.protein_series_id,
      sequence: res.data.sequence_series_id,
    })
  }

  const showResults = !discovery.loading && discovery.data !== null

  return (
    <div className="content-section">
      <p className="scope-note">
        Search an external protein database. A candidate is <strong>not</strong> Project context:
        only an explicit Import creates the canonical Protein and its canonical Sequence, and
        importing creates no Evidence.
      </p>
      <form className="search-form" role="search" onSubmit={submit}>
        <Field label="Provider">
          <select
            value={activeProvider?.key ?? ''}
            onChange={(event) => {
              setProviderKey(event.target.value)
              setRequest(null)
            }}
            disabled={proteinProviders.length === 0}
          >
            {proteinProviders.length === 0 ? <option value="">No provider installed</option> : null}
            {proteinProviders.map((provider) => {
              const capability = proteinCapability(provider)
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
            placeholder="e.g. protein_name:kinase AND organism_id:9606"
            aria-label="Protein search query"
          />
        </Field>
        <Button type="submit" disabled={discovery.loading || proteinProviders.length === 0}>
          {discovery.loading ? 'Searching…' : 'Search'}
        </Button>
      </form>
      {attribution ? (
        <p className="scope-note">
          <span>
            External protein records are provided by {activeProvider?.name}.{' '}
            <a href={attribution.url} target="_blank" rel="noreferrer noopener">
              {attribution.label}
            </a>
            .
          </span>
        </p>
      ) : null}
      <ErrorBox message={formError ?? membership.error} />
      <ErrorBox message={discovery.error} />

      {showResults ? (
        <section className="content-section">
          <div className="list-row-head">
            <FlaskConical size={15} />
            <strong>External candidates</strong>
            <Badge tone="warn">external · not yet in Project</Badge>
          </div>
          {discovery.data && discovery.data.candidates && discovery.data.candidates.length > 0 ? (
            <div className="list">
              {discovery.data.candidates.map((candidate) => (
                <div className="list-row" key={`${candidate.authority}:${candidate.native_id}`}>
                  <div className="list-row-head">
                    <strong>{candidate.protein_name ?? '(unnamed record)'}</strong>
                  </div>
                  {descriptorLine(candidate) ? <small>{descriptorLine(candidate)}</small> : null}
                  <small className="mono">
                    {candidate.authority}:{candidate.native_id}
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

      {imported ? (
        <section className="content-section">
          <div className="list-row-head">
            <Dna size={15} />
            <strong>Imported to Project</strong>
            <Badge tone="good">Project context</Badge>
          </div>
          <small className="mono">
            {imported.authority}:{imported.native_id}
          </small>
          <div className="form-actions">
            <Button type="button" kind="quiet" onClick={() => onOpenObject(imported.protein)}>
              Open Protein
            </Button>
            <Button type="button" kind="quiet" onClick={() => onOpenObject(imported.sequence)}>
              Open Sequence
            </Button>
          </div>
        </section>
      ) : null}

      {providers.loading ? <Loading /> : null}
    </div>
  )
}
