import { useMemo, useState } from 'react'
import { Box, Download } from 'lucide-react'

import { projectApi } from '../api/backend'
import { apiErrorMessage } from '../api/client'
import { useMyMembership, useProviders, useStructureDiscovery } from '../api/hooks'
import type { ProviderRead, StructureCandidateRead } from '../api/types'
import type { StructureDiscoveryQuery } from '../api/backend'
import { Button } from '../components/buttons'
import { Badge, Empty, ErrorBox, Field, Loading } from '../components/ui'
import {
  CAPABILITY_AVAILABILITY_AVAILABLE,
  CAPABILITY_KIND_STRUCTURE_DISCOVERY,
  ROLE_MEMBER,
  ROLE_OWNER,
} from '../contracts/enums'

// Presentation-only mirror of the backend bound. The wire contract is the
// authority: the backend fails closed on an over-bound query/limit (and the
// provider clamps the result count), so drift here can never widen the surface.
const MAX_QUERY_CHARS = 300
const RESULT_LIMIT = 10

/**
 * Data-source attribution for external PDB structure metadata.
 *
 * This is legal/terms presentation, NOT capability semantics: the generic frontend
 * still discovers structure providers by the backend-owned `structure_discovery`
 * capability kind and never branches on a provider key to decide what a capability
 * DOES. PDB archive data and the RCSB APIs are CC0 1.0 with attribution encouraged,
 * so the provider that supplied the metadata is attributed here with a link.
 */
const PROVIDER_ATTRIBUTION: Record<string, { label: string; url: string }> = {
  rcsb: {
    label: 'RCSB PDB (data are CC0 1.0)',
    url: 'https://www.rcsb.org/pages/policies',
  },
}

function structureCapability(provider: ProviderRead) {
  return provider.capabilities?.find(
    (capability) => capability.kind === CAPABILITY_KIND_STRUCTURE_DISCOVERY,
  )
}

/** External candidate presentation line: method · resolution · entities. */
function descriptorLine(candidate: StructureCandidateRead): string {
  const parts: string[] = []
  const methods = candidate.experimental_methods ?? []
  if (methods.length > 0) {
    parts.push(methods.join('; '))
  }
  if (candidate.resolution_angstrom) parts.push(`${candidate.resolution_angstrom} Å`)
  if (candidate.polymer_entity_count) {
    parts.push(`${candidate.polymer_entity_count} polymer entities`)
  }
  if (candidate.release_date) parts.push(`released ${candidate.release_date.slice(0, 10)}`)
  return parts.join(' · ')
}

/**
 * Objects / Discover structures: external PDB archive discovery and EXPLICIT import.
 *
 * This surface makes the Phase-15 boundary visible. External candidates are
 * EPHEMERAL provider data ("not yet in Project") and carry no coordinates; only an
 * explicit human Import re-resolves the entry, takes immutable ContentStore custody
 * of the canonical PDBx/mmCIF bytes, and creates the canonical Structure
 * ScientificObject, after which it becomes visible to the ordinary Project search
 * and object detail surfaces. Importing is never interpretation: it creates no
 * Evidence and no Decision, and the coordinates stay artifact bytes (no viewer, no
 * raw coordinate rendering).
 *
 * Provider selection comes from the Provider Catalog filtered by the backend-owned
 * `structure_discovery` capability kind — never from a hard-coded provider key.
 */
export function StructureDiscoveryView({
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

  const structureProviders = useMemo(
    () => (providers.data ?? []).filter((provider) => structureCapability(provider) !== undefined),
    [providers.data],
  )
  const [providerKey, setProviderKey] = useState<string | null>(null)
  const activeProvider =
    structureProviders.find((provider) => provider.key === providerKey) ??
    structureProviders[0] ??
    null
  const availability = activeProvider
    ? structureCapability(activeProvider)?.availability
    : undefined
  const providerReady = availability === CAPABILITY_AVAILABILITY_AVAILABLE

  const attribution = activeProvider ? PROVIDER_ATTRIBUTION[activeProvider.key] : undefined

  const [query, setQuery] = useState('')
  const [request, setRequest] = useState<StructureDiscoveryQuery | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [importingKey, setImportingKey] = useState<string | null>(null)
  const [importError, setImportError] = useState<string | null>(null)
  // The canonical objects an import produced or reused: shown as links into the
  // EXISTING object-detail surface (never a second Structure view).
  const [imported, setImported] = useState<
    { authority: string; native_id: string; structure: string; artifact: string } | null
  >(null)

  const discovery = useStructureDiscovery(actorId, projectId, request)
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
      setFormError('No structure discovery provider is available for this project.')
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

  async function importCandidate(candidate: StructureCandidateRead) {
    setImportError(null)
    setImportingKey(candidate.native_id)
    // Only the STABLE identity is sent: the server re-resolves the entry at the
    // current provider, downloads and bounds the coordinates itself, and decides
    // every persisted scientific value and byte.
    const res = await projectApi(actorId).importStructure(projectId, {
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
      structure: res.data.structure_series_id,
      artifact: res.data.coordinate_artifact_id,
    })
  }

  const showResults = !discovery.loading && discovery.data !== null

  return (
    <div className="content-section">
      <p className="scope-note">
        Search the external PDB archive. A candidate is <strong>not</strong> Project context and
        carries no coordinates: only an explicit Import takes custody of the canonical PDBx/mmCIF
        snapshot and creates the canonical Structure, and importing creates no Evidence.
      </p>
      <form className="search-form" role="search" onSubmit={submit}>
        <Field label="Provider">
          <select
            value={activeProvider?.key ?? ''}
            onChange={(event) => {
              setProviderKey(event.target.value)
              setRequest(null)
            }}
            disabled={structureProviders.length === 0}
          >
            {structureProviders.length === 0 ? (
              <option value="">No provider installed</option>
            ) : null}
            {structureProviders.map((provider) => {
              const capability = structureCapability(provider)
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
            placeholder="e.g. human hemoglobin"
            aria-label="Structure search query"
          />
        </Field>
        <Button type="submit" disabled={discovery.loading || structureProviders.length === 0}>
          {discovery.loading ? 'Searching…' : 'Search'}
        </Button>
      </form>
      {attribution ? (
        <p className="scope-note">
          <span>
            External structure records are provided by {activeProvider?.name}.{' '}
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
            <Box size={15} />
            <strong>External candidates</strong>
            <Badge tone="warn">external · not yet in Project</Badge>
          </div>
          {discovery.data && discovery.data.candidates && discovery.data.candidates.length > 0 ? (
            <div className="list">
              {discovery.data.candidates.map((candidate) => (
                <div className="list-row" key={`${candidate.authority}:${candidate.native_id}`}>
                  <div className="list-row-head">
                    <strong>{candidate.title ?? '(untitled entry)'}</strong>
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
            <Box size={15} />
            <strong>Imported to Project</strong>
            <Badge tone="good">Project context</Badge>
          </div>
          <small className="mono">
            {imported.authority}:{imported.native_id}
          </small>
          <small className="hint">Coordinate artifact available.</small>
          <small className="mono" title={imported.artifact}>
            artifact {imported.artifact}
          </small>
          <div className="form-actions">
            <Button type="button" kind="quiet" onClick={() => onOpenObject(imported.structure)}>
              Open Structure
            </Button>
          </div>
        </section>
      ) : null}

      {providers.loading ? <Loading /> : null}
    </div>
  )
}
