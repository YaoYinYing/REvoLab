import { useMemo, useState } from 'react'
import { FlaskConical, Play, Zap } from 'lucide-react'

import { projectApi } from '../api/backend'
import { useResources, useTools } from '../api/hooks'
import type { ReferenceRead, ToolDescriptorRead, ToolResultRead } from '../api/types'
import { Button } from '../components/buttons'
import { Badge, Empty, ErrorBox, Field, Loading, Section } from '../components/ui'
import {
  RESOURCE_KIND_ARTIFACT,
  TOOL_EXECUTION_CLASS_LOCAL,
  TOOL_EXECUTION_CLASS_REMOTE,
  TOOL_RESULT_KIND_ARTIFACT,
  TOOL_RESULT_KIND_EPHEMERAL,
} from '../contracts/enums'

function toneFor(executionClass: string): 'good' | 'neutral' {
  return executionClass === TOOL_EXECUTION_CLASS_LOCAL ? 'good' : 'neutral'
}

function ToolRow({ tool }: { tool: ToolDescriptorRead }) {
  return (
    <div className="list-row">
      <div className="list-row-head">
        {tool.execution_class === TOOL_EXECUTION_CLASS_LOCAL ? <Zap size={15} /> : <Play size={15} />}
        <strong>{tool.name}</strong>
        <small className="mono">{tool.id}</small>
        <Badge tone={toneFor(tool.execution_class)}>{tool.execution_class}</Badge>
        <Badge>{tool.side_effect_class}</Badge>
        <Badge tone={tool.autonomy === 'explicit_action' ? 'warn' : 'neutral'}>{tool.autonomy}</Badge>
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

type AnalysisToolId = 'table.describe' | 'table.select' | 'plot.xy'

export function ToolsView({ actorId, projectId }: { actorId: string; projectId: string }) {
  const tools = useTools(actorId, projectId)
  const artifacts = useResources(actorId, projectId, RESOURCE_KIND_ARTIFACT)
  const [selectedArtifactId, setSelectedArtifactId] = useState<string>('')
  const [toolId, setToolId] = useState<AnalysisToolId>('table.describe')
  const [columns, setColumns] = useState('')
  const [xColumn, setXColumn] = useState('')
  const [yColumns, setYColumns] = useState('')
  const [title, setTitle] = useState('')
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

  async function run() {
    setError(null)
    if (!selectedArtifactId) {
      setError('Select an artifact to analyze.')
      return
    }
    setBusy(true)
    const input: Record<string, unknown> = { artifact_id: selectedArtifactId }
    if (toolId === 'table.select') {
      if (columns.trim()) input.columns = columns.split(',').map((value) => value.trim()).filter(Boolean)
      input.limit = 50
    } else if (toolId === 'plot.xy') {
      input.x_column = xColumn
      input.y_columns = yColumns.split(',').map((value) => value.trim()).filter(Boolean)
      if (title.trim()) input.title = title
    }

    const res = await projectApi(actorId).invokeTool(projectId, {
      tool_id: toolId,
      input,
      persist,
    })
    setBusy(false)
    if (res.error || !res.data) {
      setError('The analysis failed.')
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
            <select value={toolId} onChange={(event) => setToolId(event.target.value as AnalysisToolId)}>
              <option value="table.describe">table.describe — summarize columns</option>
              <option value="table.select">table.select — select columns / persist</option>
              <option value="plot.xy">plot.xy — structured X-Y plot spec</option>
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
          {toolId === 'table.select' ? (
            <Field label="Columns (comma separated; empty = all)">
              <input value={columns} onChange={(event) => setColumns(event.target.value)} placeholder="x,y" />
            </Field>
          ) : null}
          {toolId === 'plot.xy' ? (
            <>
              <Field label="X column">
                <input value={xColumn} onChange={(event) => setXColumn(event.target.value)} placeholder="x" />
              </Field>
              <Field label="Y columns (comma separated)">
                <input value={yColumns} onChange={(event) => setYColumns(event.target.value)} placeholder="y" />
              </Field>
              <Field label="Plot title (optional)">
                <input value={title} onChange={(event) => setTitle(event.target.value)} />
              </Field>
            </>
          ) : null}
          {(toolId === 'table.select' || toolId === 'plot.xy') && localTools.some((tool) => tool.id === toolId) ? (
            <label className="field-inline">
              <input type="checkbox" checked={persist} onChange={(event) => setPersist(event.target.checked)} />
              Persist result as a derived artifact (owner/member)
            </label>
          ) : null}
          <div className="form-actions">
            <Button type="button" onClick={run} disabled={busy}>
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
