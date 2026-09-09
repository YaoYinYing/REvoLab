import { useMemo, useState } from 'react'
import { Bot, GitCommitHorizontal } from 'lucide-react'

import { projectApi } from '../api/backend'
import { useAgentTools, useObjects, useProjectContext } from '../api/hooks'
import type { DecisionRead, ToolDescriptorRead } from '../api/types'
import { Button } from '../components/buttons'
import { Badge, Empty, ErrorBox, Field, Loading, Section } from '../components/ui'
import {
  AGENT_TOOL_AUTONOMY_AUTOMATIC,
  AGENT_TOOL_AUTONOMY_EXPLICIT_ACTION,
  AGENT_TOOL_AUTONOMY_POLICY,
  DECISION_STATUS_COMMITTED,
  DECISION_STATUS_DRAFT,
  DEFAULT_SELECT_TARGET_KIND,
} from '../contracts/enums'

function autonomyTone(autonomy: string): 'neutral' | 'good' | 'warn' {
  if (autonomy === AGENT_TOOL_AUTONOMY_AUTOMATIC) return 'good'
  if (autonomy === AGENT_TOOL_AUTONOMY_POLICY) return 'neutral'
  if (autonomy === AGENT_TOOL_AUTONOMY_EXPLICIT_ACTION) return 'warn'
  return 'neutral'
}

function ToolRow({ tool }: { tool: ToolDescriptorRead }) {
  return (
    <div className="list-row" key={tool.id}>
      <div className="list-row-head">
        <Bot size={15} />
        <strong>{tool.id}</strong>
        <Badge tone={autonomyTone(tool.autonomy)}>{tool.autonomy}</Badge>
        {tool.available ? <Badge tone="good">available</Badge> : <Badge tone="warn">unavailable</Badge>}
      </div>
      <p>{tool.description}</p>
      <small className="mono">
        {tool.source}
        {tool.provider_key ? ` · ${tool.provider_key}` : ''}
        {tool.availability_reason ? ` · ${tool.availability_reason}` : ''}
      </small>
    </div>
  )
}

export function AgentView({ actorId, projectId }: { actorId: string; projectId: string }) {
  const { data: objects } = useObjects(actorId, projectId)
  const [selectedSeriesId, setSelectedSeriesId] = useState<string>('')
  const [statement, setStatement] = useState('')
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [proposal, setProposal] = useState<DecisionRead | null>(null)

  const selection = useMemo(
    () =>
      selectedSeriesId
        ? {
            series_ids: [selectedSeriesId],
            include_relations: true,
            include_evidence: true,
            include_decisions: true,
            include_references: true,
            include_provider_capabilities: false,
            graph_depth: 0,
            max_series: 50,
            max_revisions: 200,
            max_relations: 200,
            max_evidence: 100,
            max_decisions: 100,
            max_references: 100,
          }
        : null,
    [selectedSeriesId],
  )
  const context = useProjectContext(actorId, projectId, selection)
  const tools = useAgentTools(actorId, projectId)

  async function recordDraft(event: React.FormEvent) {
    event.preventDefault()
    if (!selection) return
    setActionError(null)
    setBusy(true)
    const res = await projectApi(actorId).createAgentProposal(projectId, {
      title: title || 'Agent proposal',
      statement,
      next_actions: [],
      cites: [],
      selects: [{ target_id: selectedSeriesId, target_kind: DEFAULT_SELECT_TARGET_KIND }],
    })
    setBusy(false)
    if (res.error || !res.data) {
      setActionError('Recording the proposal draft failed.')
      return
    }
    setProposal(res.data as DecisionRead)
    setStatement('')
    setTitle('')
  }

  async function commitDraft() {
    if (!proposal) return
    setActionError(null)
    setBusy(true)
    const res = await projectApi(actorId).commitDecision(projectId, proposal.id)
    setBusy(false)
    if (res.error || !res.data) {
      setActionError('Commit failed. Only the existing authorized commit path promotes truth.')
      return
    }
    setProposal(res.data as DecisionRead)
  }

  const proposalIsDraft = proposal?.status === DECISION_STATUS_DRAFT

  return (
    <div className="view">
      <div className="view-header">
        <h1>Agent</h1>
        <p>
          The Agent reads bounded Project context and proposes — it never owns truth. A proposal is a
          Decision <Badge tone="warn">draft</Badge> until an authorized actor explicitly commits it.
        </p>
      </div>

      <Section title="Selected context">
        <Field label="Scientific object">
          <select
            value={selectedSeriesId}
            onChange={(event) => {
              setSelectedSeriesId(event.target.value)
              setProposal(null)
            }}
            aria-label="Select object for agent context"
          >
            <option value="">— choose an object —</option>
            {objects?.map((object) => (
              <option key={object.series_id} value={object.series_id}>
                {object.name} ({object.object_type})
              </option>
            ))}
          </select>
        </Field>
        {!selection ? <Empty label="Select an object to assemble bounded Project context." /> : null}
        {context.loading ? <Loading label="Assembling context…" /> : null}
        <ErrorBox message={context.error} />
        {context.data ? (
          <div className="context-summary">
            <p>
              <strong>{context.data.project_name}</strong> · {context.data.membership_role} lens
            </p>
            <small className="mono">
              series {context.data.budget.series_count} · revisions {context.data.budget.revision_count}
              {' · '}relations {context.data.budget.relation_count} · evidence {context.data.budget.evidence_count}
              {' · '}decisions {context.data.budget.decision_count} · references{' '}
              {context.data.budget.reference_count}
              {context.data.budget.truncated ? ' · TRUNCATED' : ''}
            </small>
            {(context.data.loaded_skill_ids ?? []).length > 0 ? (
              <p>Loaded skills: {(context.data.loaded_skill_ids ?? []).join(', ')}</p>
            ) : null}
          </div>
        ) : null}
      </Section>

      <Section title="Available tools">
        {tools.loading ? <Loading /> : null}
        <ErrorBox message={tools.error} />
        {(tools.data?.tools ?? []).length === 0 ? <Empty label="No tools available." /> : null}
        {(tools.data?.tools ?? []).map((tool) => <ToolRow key={tool.id} tool={tool} />)}
      </Section>

      <Section title="Proposal → decision draft → explicit commit">
        <form className="stack-form" onSubmit={recordDraft}>
          <Field label="Proposal title">
            <input
              placeholder="e.g. Select variant for experimental validation"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
            />
          </Field>
          <Field label="Proposed conclusion">
            <textarea
              rows={3}
              placeholder="This variant is the current experimental candidate."
              value={statement}
              onChange={(event) => setStatement(event.target.value)}
              required
            />
          </Field>
          <div className="form-actions">
            <Button type="submit" disabled={busy || !selection}>
              {busy ? 'Recording…' : 'Record draft'}
            </Button>
            {proposalIsDraft ? (
              <Button onClick={commitDraft} disabled={busy}>
                <GitCommitHorizontal size={15} /> Commit (authorized)
              </Button>
            ) : null}
            {actionError ? <span className="inline-error">{actionError}</span> : null}
          </div>
        </form>

        {proposal ? (
          <div className={`list-row ${proposalIsDraft ? 'decision-row' : ''}`}>
            <div className="list-row-head">
              <strong>{proposal.title}</strong>
              <Badge tone={proposal.status === DECISION_STATUS_COMMITTED ? 'good' : 'warn'}>
                {proposal.status}
              </Badge>
            </div>
            <p>{proposal.statement}</p>
            <small className="mono">decision {proposal.id.slice(0, 8)}…</small>
          </div>
        ) : null}

        <p className="truth-boundary">
          Agent proposal ≠ committed Project Knowledge. A draft is conversation-adjacent project state;
          only the explicit commit path materializes knowledge edges as truth.
        </p>
      </Section>
    </div>
  )
}
