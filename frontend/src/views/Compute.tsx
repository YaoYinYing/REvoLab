import { useMemo, useState } from 'react'
import { FlaskConical, Play, RefreshCw } from 'lucide-react'

import { projectApi } from '../api/backend'
import { useComputeTaskKindSchema, useComputeTaskKinds, useObjects, useProviders } from '../api/hooks'
import type {
  ComputeArtifactRead,
  ComputeRunStatusRead,
  ComputeSubmissionRead,
  ObjectSummaryRead,
} from '../api/types'
import { Badge, Empty, ErrorBox, Field, Loading, Section } from '../components/ui'
import {
  CAPABILITY_AVAILABILITY_AVAILABLE,
  CAPABILITY_KIND_COMPUTE,
  RESOURCE_KIND_REVISION,
} from '../contracts/enums'

type JsonSchema = Record<string, unknown> & {
  type?: string
  properties?: Record<string, JsonSchema>
  required?: string[]
}

/**
 * Minimal schema-driven parameter form. Walks the provider-returned JSON Schema
 * (Draft 2020-12) and renders one control per property. No provider vocabulary
 * is known to this view: every label/type/choice comes from the schema data.
 */
function SchemaForm({
  schema,
  values,
  onChange,
}: {
  schema: JsonSchema | null
  values: Record<string, unknown>
  onChange: (name: string, value: unknown) => void
}) {
  const properties = schema?.properties ?? {}
  const required = schema?.required ?? []
  return (
    <div className="stack-form">
      {Object.entries(properties).map(([name, descriptor]) => {
        const type = descriptor.type ?? 'string'
        const title = (descriptor.title as string) ?? name
        const choices = Array.isArray(descriptor.enum) ? (descriptor.enum as string[]) : []
        return (
          <Field key={name} label={`${title}${required.includes(name) ? ' *' : ''}`}>
            {choices.length > 0 ? (
              <select
                value={String(values[name] ?? descriptor.default ?? choices[0] ?? '')}
                onChange={(event) => onChange(name, event.target.value)}
              >
                {choices.map((choice) => (
                  <option key={choice} value={choice}>
                    {choice}
                  </option>
                ))}
              </select>
            ) : type === 'boolean' ? (
              <select
                value={String(values[name] ?? descriptor.default ?? false)}
                onChange={(event) => onChange(name, event.target.value === 'true')}
              >
                <option value="true">true</option>
                <option value="false">false</option>
              </select>
            ) : type === 'integer' || type === 'number' ? (
              <input
                type="number"
                value={String(values[name] ?? descriptor.default ?? '')}
                onChange={(event) => onChange(name, Number(event.target.value))}
              />
            ) : (
              <input
                value={String(values[name] ?? descriptor.default ?? '')}
                onChange={(event) => onChange(name, event.target.value)}
              />
            )}
          </Field>
        )
      })}
    </div>
  )
}

function ArtifactRow({
  actorId,
  projectId,
  artifact,
}: {
  actorId: string
  projectId: string
  artifact: ComputeArtifactRead
}) {
  const [preview, setPreview] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function resolve() {
    setBusy(true)
    setPreview(null)
    const res = await projectApi(actorId).resolveComputeArtifact(projectId, artifact.resource_id)
    setBusy(false)
    if (res.error || res.data === undefined) {
      setPreview('(resolution failed)')
      return
    }
    setPreview(res.data)
  }

  return (
    <div className="list-row">
      <div className="list-row-head">
        <FlaskConical size={15} />
        <strong>{artifact.native_id}</strong>
        <Badge>{artifact.content_type ?? 'artifact'}</Badge>
      </div>
      <small className="mono">
        checksum {artifact.checksum ? artifact.checksum.slice(0, 12) : '—'}
        {artifact.size != null ? ` · size ${artifact.size}` : ''}
      </small>
      <div className="form-actions">
        <button type="button" className="quiet-button" onClick={resolve} disabled={busy}>
          {busy ? 'Resolving…' : 'Resolve bytes'}
        </button>
      </div>
      {preview !== null ? <pre className="payload">{preview}</pre> : null}
    </div>
  )
}

export function ComputeView({
  actorId,
  projectId,
  initialRevisionId,
}: {
  actorId: string
  projectId: string
  initialRevisionId: string | null
}) {
  const providers = useProviders(actorId, projectId)
  const objects = useObjects(actorId, projectId)
  const [providerKey, setProviderKey] = useState<string | null>(null)
  const [kindId, setKindId] = useState<string | null>(null)
  const [inputRevisionId, setInputRevisionId] = useState<string | null>(initialRevisionId)
  const [params, setParams] = useState<Record<string, unknown>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submission, setSubmission] = useState<ComputeSubmissionRead | null>(null)
  const [status, setStatus] = useState<ComputeRunStatusRead | null>(null)
  const [artifacts, setArtifacts] = useState<ComputeArtifactRead[]>([])

  const computeProviders = useMemo(
    () =>
      (providers.data ?? []).filter((provider) =>
        (provider.capabilities ?? []).some(
          (capability) =>
            capability.kind === CAPABILITY_KIND_COMPUTE &&
            capability.availability === CAPABILITY_AVAILABILITY_AVAILABLE,
        ),
      ),
    [providers.data],
  )

  const effectiveProvider = providerKey ?? computeProviders[0]?.key ?? null
  const taskKinds = useComputeTaskKinds(actorId, projectId, effectiveProvider)
  const effectiveKind = kindId ?? taskKinds.data?.[0]?.kind_id ?? null
  const schema = useComputeTaskKindSchema(actorId, projectId, effectiveProvider, effectiveKind)

  const latestInput = useMemo(() => {
    const revisionId = inputRevisionId ?? initialRevisionId
    if (!revisionId) return null
    return revisionId
  }, [inputRevisionId, initialRevisionId])

  async function submit() {
    if (!effectiveProvider || !effectiveKind || !latestInput) return
    setBusy(true)
    setError(null)
    const res = await projectApi(actorId).createComputeSubmission(projectId, {
      provider_key: effectiveProvider,
      task_kind: effectiveKind,
      inputs: [{ kind: RESOURCE_KIND_REVISION, resource_id: latestInput }],
      params: params as Record<string, string | number | boolean>,
    })
    setBusy(false)
    if (res.error || !res.data) {
      setError('Submission failed.')
      return
    }
    setSubmission(res.data)
    setArtifacts([])
    await refreshStatus(res.data.run_resource_id)
  }

  async function refreshStatus(runId: string) {
    const res = await projectApi(actorId).getComputeRunStatus(projectId, runId)
    if (res.error || !res.data) {
      setError('Could not resolve live run status.')
      return
    }
    setStatus(res.data)
  }

  async function refreshArtifacts() {
    if (!submission) return
    const res = await projectApi(actorId).refreshComputeArtifacts(projectId, submission.run_resource_id)
    if (res.error || !res.data) {
      setError('Artifact discovery failed.')
      return
    }
    setArtifacts(res.data)
  }

  const inputOptions = objects.data ?? []

  return (
    <div className="view">
      <div className="view-header">
        <h1>Compute</h1>
        <p>
          Run a schema-driven computation inside this project. Task and parameter schemas come
          from the selected provider as data — REvoLab does not hard-code any provider&apos;s
          task or runner vocabulary.
        </p>
      </div>

      {providers.loading ? <Loading label="Loading providers…" /> : null}
      {computeProviders.length === 0 && !providers.loading ? (
        <Empty label="No compute-capable provider is currently available to you in this project." />
      ) : null}

      {computeProviders.length > 0 ? (
        <Section title="1 · Provider & task kind">
          <div className="form-grid">
            <Field label="Provider">
              <select
                value={effectiveProvider ?? ''}
                onChange={(event) => {
                  setProviderKey(event.target.value)
                  setKindId(null)
                  setParams({})
                  setSubmission(null)
                }}
              >
                {computeProviders.map((provider) => (
                  <option key={provider.key} value={provider.key}>
                    {provider.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Task kind">
              <select
                value={effectiveKind ?? ''}
                onChange={(event) => {
                  setKindId(event.target.value)
                  setParams({})
                }}
              >
                {effectiveKind ? null : <option value="">—</option>}
                {(taskKinds.data ?? []).map((kind) => (
                  <option key={kind.kind_id} value={kind.kind_id}>
                    {kind.display_name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          {schema.data ? <p className="hint">{schema.data.description}</p> : null}
        </Section>
      ) : null}

      <Section title="2 · Parameters (schema-driven)">
        <SchemaForm
          schema={(schema.data?.parameter_schema as JsonSchema) ?? null}
          values={params}
          onChange={(name, value) => setParams((current) => ({ ...current, [name]: value }))}
        />
      </Section>

      <Section title="3 · Input (project resource)">
        <Field label="Scientific object (latest revision)">
          <select
            value={latestInput ?? ''}
            onChange={(event) => setInputRevisionId(event.target.value)}
          >
            {latestInput ? null : <option value="">—</option>}
            {inputOptions
              .filter((object: ObjectSummaryRead) => object.latest_revision)
              .map((object) => (
                <option key={object.latest_revision!.revision_id} value={object.latest_revision!.revision_id}>
                  {object.name} · r{object.latest_revision!.revision_seq}
                </option>
              ))}
          </select>
        </Field>
      </Section>

      <Section
        title="4 · Submission"
        actions={
          <button
            type="button"
            className="btn btn-primary"
            onClick={submit}
            disabled={busy || !effectiveProvider || !effectiveKind || !latestInput}
          >
            <Play size={14} /> {busy ? 'Submitting…' : 'Submit'}
          </button>
        }
      >
        <ErrorBox message={error} />
      </Section>

      {submission ? (
        <Section title="Run reference">
          <div className="identity-grid">
            <div>
              <dt>Authority</dt>
              <dd className="mono">{submission.authority}</dd>
            </div>
            <div>
              <dt>Native id</dt>
              <dd className="mono">{submission.native_id}</dd>
            </div>
            <div>
              <dt>Task type</dt>
              <dd className="mono">{submission.task_type ?? '—'}</dd>
            </div>
          </div>
          <div className="form-actions">
            <button
              type="button"
              className="quiet-button"
              onClick={() => refreshStatus(submission.run_resource_id)}
            >
              <RefreshCw size={14} /> Refresh status
            </button>
            <button type="button" className="quiet-button" onClick={refreshArtifacts}>
              Discover artifacts
            </button>
          </div>
          {status ? (
            <div className="list-row">
              <Badge tone={status.available ? 'good' : 'warn'}>
                {status.available ? (status.status ?? 'live') : 'unavailable'}
              </Badge>
              {status.detail ? <span className="hint">{status.detail}</span> : null}
            </div>
          ) : null}
        </Section>
      ) : null}

      {artifacts.length > 0 ? (
        <Section title={`Artifacts (${artifacts.length})`}>
          {artifacts.map((artifact) => (
            <ArtifactRow key={artifact.resource_id} actorId={actorId} projectId={projectId} artifact={artifact} />
          ))}
        </Section>
      ) : null}
    </div>
  )
}
