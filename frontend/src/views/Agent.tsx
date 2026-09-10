import { useEffect, useState } from 'react'
import { Bot, GitCommitHorizontal, Send } from 'lucide-react'

import { projectApi } from '../api/backend'
import { useObjects, useResources } from '../api/hooks'
import type { AgentChatMessageCreate, AgentTurnRead } from '../api/types'
import { Button } from '../components/buttons'
import { Badge, Empty, ErrorBox, Field, Loading, Section } from '../components/ui'
import {
  AGENT_TERMINATION_REASON_FINAL_RESPONSE,
  AGENT_TOOL_CALL_STATUS_COMPLETED,
  AGENT_TOOL_CALL_STATUS_FAILED,
  AGENT_TOOL_CALL_STATUS_PENDING,
  RESOURCE_KIND_ARTIFACT,
} from '../contracts/enums'

interface Message {
  role: 'user' | 'assistant'
  content: string
}

function traceTone(status: string): 'neutral' | 'good' | 'warn' {
  if (status === AGENT_TOOL_CALL_STATUS_COMPLETED) return 'good'
  if (status === AGENT_TOOL_CALL_STATUS_PENDING) return 'warn'
  if (status === AGENT_TOOL_CALL_STATUS_FAILED) return 'warn'
  return 'neutral'
}

export function AgentView({ actorId, projectId }: { actorId: string; projectId: string }) {
  const { data: objects } = useObjects(actorId, projectId)
  const { data: artifacts } = useResources(actorId, projectId, RESOURCE_KIND_ARTIFACT)
  const [selectedSeriesId, setSelectedSeriesId] = useState('')
  const [selectedArtifactId, setSelectedArtifactId] = useState('')
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [turn, setTurn] = useState<AgentTurnRead | null>(null)

  // Conversation is session-local AND project-scoped: switching project/actor
  // must not leak one project's conversation into another project's turn.
  useEffect(() => {
    setMessages([])
    setTurn(null)
    setActionError(null)
    setInput('')
    setSelectedSeriesId('')
    setSelectedArtifactId('')
  }, [projectId, actorId])

  async function send(event: React.FormEvent) {
    event.preventDefault()
    if (!input.trim() || busy) return
    const userMessage = input.trim()
    setActionError(null)
    setBusy(true)
    setInput('')

    const history: AgentChatMessageCreate[] = messages.slice(-20).map((message) => ({
      role: message.role,
      content: message.content,
    }))
    const res = await projectApi(actorId).createAgentTurn(projectId, {
      message: userMessage,
      selection: {
        ...(selectedSeriesId ? { series_ids: [selectedSeriesId] } : {}),
        ...(selectedArtifactId ? { artifact_ids: [selectedArtifactId] } : {}),
        include_relations: true,
        include_evidence: true,
        include_decisions: true,
        include_references: true,
        include_provider_capabilities: false,
        graph_depth: 1,
        max_series: 50,
        max_revisions: 200,
        max_relations: 200,
        max_evidence: 100,
        max_decisions: 100,
        max_references: 100,
      },
      history,
    })
    setBusy(false)
    if (res.error || !res.data) {
      setActionError('The Agent turn failed. Check the model runtime and project context.')
      return
    }
    setTurn(res.data)
    setMessages((current) => [
      ...current,
      { role: 'user', content: userMessage },
      ...(res.data?.final_response ? [{ role: 'assistant' as const, content: res.data.final_response }] : []),
    ])
  }

  const pendingActions = turn?.pending_actions ?? []
  const trace = turn?.tool_trace ?? []

  return (
    <div className="view">
      <div className="view-header">
        <h1>Agent</h1>
        <p>
          The Agent reads a freshly assembled bounded Project context, reasons, and can call canonical
          Project Tools. Conversation here is ephemeral working memory — never durable project truth. A
          Decision created by the Agent is a <Badge tone="warn">draft</Badge> until you commit it.
        </p>
      </div>

      <Section title="Context">
        <div className="form-grid">
          <Field label="Scientific object (optional)">
            <select
              value={selectedSeriesId}
              onChange={(event) => setSelectedSeriesId(event.target.value)}
              aria-label="Select object for agent context"
            >
              <option value="">— none —</option>
              {objects?.map((object) => (
                <option key={object.series_id} value={object.series_id}>
                  {object.name} ({object.object_type})
                </option>
              ))}
            </select>
          </Field>
          <Field label="Tabular artifact (optional)">
            <select
              value={selectedArtifactId}
              onChange={(event) => setSelectedArtifactId(event.target.value)}
              aria-label="Select artifact for agent context"
            >
              <option value="">— none —</option>
              {artifacts?.map((artifact) => (
                <option key={artifact.resource_id} value={artifact.resource_id}>
                  {artifact.native_id ?? artifact.resource_id.slice(0, 8)}
                  {artifact.content_type ? ` · ${artifact.content_type}` : ''}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Section>

      <Section title="Conversation (session-local, not persisted)">
        {messages.length === 0 ? <Empty label="Ask the Agent to analyze the selected context." /> : null}
        {messages.map((message, index) => (
          <div className={`list-row ${message.role === 'assistant' ? 'assistant-row' : ''}`} key={index}>
            <div className="list-row-head">
              {message.role === 'assistant' ? <Bot size={15} /> : null}
              <strong>{message.role === 'assistant' ? 'Agent' : 'You'}</strong>
            </div>
            <p className="pre-line">{message.content}</p>
          </div>
        ))}

        <form className="stack-form" onSubmit={send}>
          <textarea
            rows={3}
            placeholder='e.g. "Describe this table and draft a conclusion based on it."'
            value={input}
            onChange={(event) => setInput(event.target.value)}
            required
          />
          <div className="form-actions">
            <Button type="submit" disabled={busy || !input.trim()}>
              <Send size={15} /> {busy ? 'Running…' : 'Send'}
            </Button>
            {actionError ? <span className="inline-error">{actionError}</span> : null}
          </div>
        </form>
      </Section>

      {turn ? (
        <Section title="Last turn">
          <p>
            Termination: <Badge tone={turn.termination_reason === AGENT_TERMINATION_REASON_FINAL_RESPONSE ? 'good' : 'warn'}>
              {turn.termination_reason}
            </Badge>
          </p>

          {trace.length > 0 ? (
            <Section title="Tools used">
              {trace.map((entry, index) => (
                <div className="list-row" key={index}>
                  <div className="list-row-head">
                    <Bot size={15} />
                    <strong className="mono">{entry.tool_id}</strong>
                    <Badge tone={traceTone(entry.status)}>{entry.status}</Badge>
                  </div>
                  {entry.error ? <p className="inline-error">{entry.error}</p> : null}
                  {entry.pending_action ? (
                    <p>
                      Proposed <strong>{entry.pending_action.tool_id}</strong> — NOT executed (
                      {entry.pending_action.reason}).
                    </p>
                  ) : null}
                </div>
              ))}
            </Section>
          ) : null}

          {pendingActions.length > 0 ? (
            <Section title="Pending explicit actions">
              <p className="truth-boundary">
                The Agent proposed these actions but did NOT execute them. Use the existing authorized
                surface (e.g. the Decisions view with <GitCommitHorizontal size={12} /> commit) to act.
              </p>
              {pendingActions.map((action, index) => (
                <div className="list-row" key={index}>
                  <div className="list-row-head">
                    <strong className="mono">{action.tool_id}</strong>
                    <Badge tone="warn">{action.autonomy}</Badge>
                  </div>
                  <p>{action.summary}</p>
                  <small className="mono">{JSON.stringify(action.arguments)}</small>
                </div>
              ))}
            </Section>
          ) : null}

          <p className="truth-boundary">
            Agent output ≠ committed Project Knowledge. A Decision draft stays a draft until the explicit
            authorized commit path materializes knowledge edges as truth.
          </p>
        </Section>
      ) : null}

      <p className="truth-boundary">
        Agent output ≠ committed Project Knowledge. A Decision created by the Agent is always a{' '}
        <Badge tone="warn">draft</Badge> until you commit it through the Decisions view.
      </p>
    </div>
  )
}
