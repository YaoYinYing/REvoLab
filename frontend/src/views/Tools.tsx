import { useMemo, useState } from 'react'
import { FlaskConical, Play, Zap } from 'lucide-react'

import { projectApi } from '../api/backend'
import { useResources, useTools } from '../api/hooks'
import { apiErrorMessage } from '../api/client'
import type { ReferenceRead, ToolDescriptorRead, ToolResultRead } from '../api/types'
import { Button } from '../components/buttons'
import { Badge, Empty, ErrorBox, Field, Loading, Section } from '../components/ui'
import {
  AGENT_TOOL_AUTONOMY_EXPLICIT_ACTION,
  RESOURCE_KIND_ARTIFACT,
  TOOL_EXECUTION_CLASS_LOCAL,
  TOOL_EXECUTION_CLASS_REMOTE,
  TOOL_RESULT_KIND_ARTIFACT,
  TOOL_RESULT_KIND_EPHEMERAL,
  TOOL_SIDE_EFFECT_CREATES_DERIVED_RESULT,
} from '../contracts/enums'

// Presentation grouping only: which catalog tools belong to the local analysis
// surface. Tool names, descriptions, availability and schemas all come from the
// canonical catalog below — these ids are a view filter, not a schema copy.
const ANALYSIS_TOOL_IDS = new Set(['table.describe', 'table.select', 'plot.xy'])

type JsonSchema = Record<string, unknown> & {
  type?: string | string[]
  anyOf?: JsonSchema[]
  properties?: Record<string, JsonSchema>
}

function resolveType(descriptor: JsonSchema): string {
  const raw = descriptor.type
  if (Array.isArray(raw)) {
    if (raw.includes('array')) return 'array'
    if (raw.includes('integer') || raw.includes('number')) return 'number'
    return 'string'
  }
  if (typeof raw === 'string') return raw
  // Pydantic optional fields render as anyOf: [real, null] with no top-level type.
  if (Array.isArray(descriptor.anyOf)) {
    const nonNull = descriptor.anyOf.filter((option) => {
      const optionType = (option as JsonSchema).type
      return optionType === 'array' || optionType === 'integer' || optionType === 'number'
    })
    if (nonNull.length > 0) return resolveType(nonNull[0] as JsonSchema)
  }
  return 'string'
}

function ToolRow({ tool }: { tool: ToolDescriptorRead }) {
  return (
    <div className="list-row">
      <div className="list-row-head">
        {tool.execution_class === TOOL_EXECUTION_CLASS_LOCAL ? <Zap size={15} /> : <Play size={15} />}
        <strong>{tool.name}</strong>
        <small className="mono">{tool.id}</small>
        <Badge tone={tool.execution_class === TOOL_EXECUTION_CLASS_LOCAL ? 'good' : 'neutral'}>
          {tool.execution_class}
        </Badge>
        <Badge>{tool.side_effect_class}</Badge>
        <Badge tone={tool.autonomy === AGENT_TOOL_AUTONOMY_EXPLICIT_ACTION ? 'warn' : 'neutral'}>
          {tool.autonomy}
        </Badge>
        {tool.available ? <Badge tone="good">available</Badge> : <Badge tone="warn">unavailable</Badge>}
      </div>
      <p>{tool.description}</p>
      <small className="mono">
        {tool.provider_key ? `${tool.source} · ${tool.provider_key}` : tool.source}
        {tool.availability_reason ? ` · ${tool.availability_reason}` : ''}
      </small>
    </div>
  )
}

function SchemaInput({
  name,
  descriptor,
  value,
  onChange,
}: {
  name: string
  descriptor: JsonSchema
  value: unknown
  onChange: (name: string, value: unknown) => void
}) {
  const type = resolveType(descriptor)
  if (type === 'array') {
    const raw = Array.isArray(value) ? value.join(', ') : ''
    return (
      <input
        value={raw}
        onChange={(event) =>
          onChange(
            name,
            event.target.value
              .split(',')
              .map((item) => item.trim())
              .filter(Boolean),
          )
        }
        placeholder="comma separated"
      />
    )
  }
  if (type === 'integer' || type === 'number') {
    return (
      <input
        type="number"
        value={typeof value === 'number' ? String(value) : ''}
        onChange={(event) => onChange(name, event.target.value === '' ? undefined : Number(event.target.value))}
      />
    )
  }
  return (
    <input
      value={typeof value === 'string' ? value : ''}
      onChange={(event) => onChange(name, event.target.value === '' ? undefined : event.target.value)}
    />
  )
}

export function ToolsView({ actorId, projectId }: { actorId: string; projectId: string }) {
  const tools = useTools(actorId, projectId)
  const artifacts = useResources(actorId, projectId, RESOURCE_KIND_ARTIFACT)
  const [selectedToolId, setSelectedToolId] = useState<string>('table.describe')
  const [selectedArtifactId, setSelectedArtifactId] = useState<string>('')
  const [params, setParams] = useState<Record<string, unknown>>({})
  const [persist, setPersist] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ToolResultRead | null>(null)

  const localTools = useMemo(
    () => (tools.data?.tools ?? []).filter((tool) => tool.execution_class === TOOL_EXECUTION_CLASS_LOCAL),
    [tools.data],
  )
  const remoteTools = useMemo(
    () => (tools.data?.tools ?? []).filter((tool) => tool.execution_class === TOOL_EXECUTION_CLASS_REMOTE),
    [tools.data],
  )
  const analysisTools = useMemo(() => localTools.filter((tool) => ANALYSIS_TOOL_IDS.has(tool.id)), [localTools])
  const selectedTool = analysisTools.find((tool) => tool.id === selectedToolId) ?? analysisTools[0]

  const inputSchema = (selectedTool?.input_schema as JsonSchema) ?? null
  const inputProperties = Object.entries(inputSchema?.properties ?? {}).filter(
    ([name]) => name !== 'artifact_id',
  )

  function selectTool(id: string) {
    setSelectedToolId(id)
    setParams({})
    setResult(null)
  }

  async function run() {
    setError(null)
    if (!selectedTool) {
      setError('Select an analysis tool.')
      return
    }
    if (!selectedArtifactId) {
      setError('Select an artifact to analyze.')
      return
    }
    if (!selectedTool.available) {
      setError('This tool is not available to you in this project.')
      return
    }
    setBusy(true)
    const input: Record<string, unknown> = { artifact_id: selectedArtifactId, ...params }
    const res = await projectApi(actorId).invokeTool(projectId, {
      tool_id: selectedTool.id,
      input,
      persist: selectedTool.side_effect_class === TOOL_SIDE_EFFECT_CREATES_DERIVED_RESULT && persist,
    })
    setBusy(false)
    if (res.error || !res.data) {
      setError(apiErrorMessage(res.error, res.response))
      return
    }
    setResult(res.data as ToolResultRead)
  }

  function selectArtifact(id: string) {
    setSelectedArtifactId(id)
    setResult(null)
  }

  return (
    <div className="view">
      <div className="view-header">
        <h1>Analyze</h1>
        <p>
          Closed, bounded local analysis over project artifacts. A result is either ephemeral or an
          explicitly persisted derived artifact — never automatically project truth.
        </p>
      </div>

      <Section title="Available tools">
        {tools.loading ? <Loading /> : null}
        <ErrorBox message={tools.error} />
        {(tools.data?.tools ?? []).length === 0 ? <Empty label="No tools available." /> : null}
        {(tools.data?.tools ?? []).map((tool) => <ToolRow key={tool.id} tool={tool} />)}
      </Section>

      <Section title="Local analysis">
        <div className="stack-form">
          <Field label="Tool">
            <select
              value={selectedTool?.id ?? ''}
              onChange={(event) => selectTool(event.target.value)}
              aria-label="Tool"
            >
              {analysisTools.map((tool) => (
                <option key={tool.id} value={tool.id} disabled={!tool.available}>
                  {tool.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Artifact">
            {artifacts.loading ? (
              <Loading label="Loading artifacts…" />
            ) : (
              <div className="artifact-picker">
                {artifacts.data?.map((artifact: ReferenceRead) => (
                  <label key={artifact.resource_id} className="artifact-chip">
                    <input
                      type="radio"
                      name="analysis-artifact"
                      checked={selectedArtifactId === artifact.resource_id}
                      onChange={() => selectArtifact(artifact.resource_id)}
                    />
                    <span className="mono">{artifact.native_id.slice(0, 16)}…</span>
                  </label>
                ))}
              </div>
            )}
          </Field>
          {inputProperties.map(([name, descriptor]) => (
            <Field key={name} label={name}>
              <SchemaInput
                name={name}
                descriptor={descriptor}
                value={params[name]}
                onChange={(field, value) => {
                  setParams((current) => ({ ...current, [field]: value }))
                  setResult(null)
                }}
              />
            </Field>
          ))}
          {selectedTool?.side_effect_class === TOOL_SIDE_EFFECT_CREATES_DERIVED_RESULT ? (
            <label className="field-inline">
              <input type="checkbox" checked={persist} onChange={(event) => setPersist(event.target.checked)} />
              Persist result as a derived artifact (owner/member)
            </label>
          ) : null}
          <div className="form-actions">
            <Button type="button" onClick={run} disabled={busy || selectedTool?.available === false}>
              {busy ? 'Running…' : 'Run analysis'}
            </Button>
            {error ? <span className="inline-error">{error}</span> : null}
          </div>
        </div>
      </Section>

      <Section title="Result">
        {result ? (
          <div className="result-box">
            <div className="list-row-head">
              <FlaskConical size={15} />
              <strong>{result.tool_id}</strong>
              <Badge tone={result.result_kind === TOOL_RESULT_KIND_ARTIFACT ? 'good' : 'neutral'}>
                {result.result_kind}
              </Badge>
              {result.result_kind === TOOL_RESULT_KIND_EPHEMERAL ? (
                <Badge>ephemeral (not persisted)</Badge>
              ) : null}
              {result.persisted && result.resource_id ? (
                <small className="mono">artifact {result.resource_id.slice(0, 8)}…</small>
              ) : null}
            </div>
            <pre className="result-json">{JSON.stringify(result.value, null, 2)}</pre>
          </div>
        ) : (
          <Empty label="Run an analysis to see its typed result." />
        )}
      </Section>

      <Section title="Remote compute tools">
        {remoteTools.length === 0 ? (
          <Empty label="No remote compute tools. The REvoCompute path appears here when available." />
        ) : (
          remoteTools.map((tool) => <ToolRow key={tool.id} tool={tool} />)
        )}
        <p className="muted">
          Remote tools execute through the existing Compute workflow; the local runtime never executes
          them directly.
        </p>
      </Section>
    </div>
  )
}
