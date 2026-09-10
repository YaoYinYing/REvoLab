import { useEffect, useRef, useState } from 'react'
import { Bot, GitCommitHorizontal, MessageSquarePlus, Send } from 'lucide-react'

import { projectApi } from '../api/backend'
import { useObjects, useResources } from '../api/hooks'
import type {
  AgentTurnRead,
  ConversationMessageRead,
  ConversationRead,
} from '../api/types'
import { Button } from '../components/buttons'
import { Badge, Empty, ErrorBox, Field, Loading, Section } from '../components/ui'
import {
  AGENT_TERMINATION_REASON_FINAL_RESPONSE,
  AGENT_TOOL_CALL_STATUS_COMPLETED,
  AGENT_TOOL_CALL_STATUS_FAILED,
  AGENT_TOOL_CALL_STATUS_PENDING,
  RESOURCE_KIND_ARTIFACT,
} from '../contracts/enums'

function traceTone(status: string): 'neutral' | 'good' | 'warn' {
  if (status === AGENT_TOOL_CALL_STATUS_COMPLETED) return 'good'
  if (status === AGENT_TOOL_CALL_STATUS_PENDING) return 'warn'
  if (status === AGENT_TOOL_CALL_STATUS_FAILED) return 'warn'
  return 'neutral'
}

const MESSAGE_PAGE_LIMIT = 200

export function AgentView({ actorId, projectId }: { actorId: string; projectId: string }) {
  const { data: objects } = useObjects(actorId, projectId)
  const { data: artifacts } = useResources(actorId, projectId, RESOURCE_KIND_ARTIFACT)
  const [selectedSeriesId, setSelectedSeriesId] = useState('')
  const [selectedArtifactId, setSelectedArtifactId] = useState('')
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [conversations, setConversations] = useState<ConversationRead[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ConversationMessageRead[]>([])
  const [totalMessages, setTotalMessages] = useState(0)
  const [turn, setTurn] = useState<AgentTurnRead | null>(null)

  // Synchronous view scope: updated on every render so an in-flight turn from a
  // previous Actor x Project can be discarded even before the reset effect runs.
  const scopeRef = useRef(`${actorId}:${projectId}`)
  scopeRef.current = `${actorId}:${projectId}`
  // The currently selected conversation, tracked synchronously so an in-flight
  // turn for conversation A is discarded once the user opens conversation B.
  const activeConversationRef = useRef(activeConversationId)
  activeConversationRef.current = activeConversationId

  useEffect(() => {
    let cancelled = false
    if (!actorId || !projectId) return
    // Conversation state is Actor x Project scoped: switching either boundary
    // must not leak one project's conversation into another.
    setLoading(true)
    setMessages([])
    setTotalMessages(0)
    setTurn(null)
    setActiveConversationId(null)
    setActionError(null)
    setInput('')
    setSelectedSeriesId('')
    setSelectedArtifactId('')
    projectApi(actorId)
      .listConversations(projectId)
      .then((res) => {
        if (cancelled) return
        const list = res.data ?? []
        setConversations(list)
        const first = list[0]?.id ?? null
        if (first && activeConversationRef.current == null) {
          setActiveConversationId(first)
          activeConversationRef.current = first
        }
        if (first) {
          const requestConversationId = first
          return projectApi(actorId)
            .getConversation(projectId, first, { limit: MESSAGE_PAGE_LIMIT, latest: true })
            .then((detail) => {
              if (cancelled) return
              if (
                activeConversationRef.current !== requestConversationId ||
                scopeRef.current !== `${actorId}:${projectId}`
              ) {
                return
              }
              if (detail.data) {
                setMessages(detail.data.messages ?? [])
                setTotalMessages(detail.data.total_messages ?? 0)
              }
            })
        }
        return undefined
      })
      .catch(() => {
        if (!cancelled) setActionError('Could not load project conversations.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [actorId, projectId])

  async function openConversation(conversationId: string) {
    if (!actorId || !projectId) return
    setActiveConversationId(conversationId)
    setActionError(null)
    const detail = await projectApi(actorId).getConversation(projectId, conversationId, {
      limit: MESSAGE_PAGE_LIMIT,
      latest: true,
    })
    if (detail.error || !detail.data) {
      setActionError('Could not load the selected conversation.')
      return
    }
    if (
      scopeRef.current !== `${actorId}:${projectId}` ||
      activeConversationRef.current !== conversationId
    ) {
      return
    }
    setMessages(detail.data.messages ?? [])
    setTotalMessages(detail.data.total_messages ?? 0)
    setTurn(null)
  }

  async function newConversation() {
    if (!actorId || !projectId || busy || loading) return
    setActionError(null)
    const created = await projectApi(actorId).createConversation(projectId)
    if (created.error || !created.data) {
      setActionError('Could not create a conversation.')
      return
    }
    setConversations((current) => [created.data!, ...current])
    setActiveConversationId(created.data!.id)
    activeConversationRef.current = created.data!.id
    setMessages([])
    setTotalMessages(0)
    setTurn(null)
    setInput('')
  }

  async function send(event: React.FormEvent) {
    event.preventDefault()
    if (!input.trim() || busy || loading || !actorId || !projectId) return
    const userMessage = input.trim()
    setActionError(null)
    setBusy(true)
    setInput('')
    const requestScope = scopeRef.current

    let conversationId = activeConversationId
    if (!conversationId) {
      const created = await projectApi(actorId).createConversation(projectId)
      if (created.error || !created.data) {
        setBusy(false)
        setActionError('Could not create a conversation.')
        return
      }
      conversationId = created.data.id
      setConversations((current) => [created.data!, ...current])
      setActiveConversationId(conversationId)
      activeConversationRef.current = conversationId
    }

    const requestConversationId = conversationId
    const res = await projectApi(actorId).createConversationTurn(projectId, conversationId, {
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
    })
    setBusy(false)
    if (
      scopeRef.current !== requestScope ||
      activeConversationRef.current !== requestConversationId
    ) {
      return
    }
    if (res.error || !res.data) {
      setActionError('The Agent turn failed. Check the model runtime and project context.')
      return
    }
    const persisted = res.data
    setTurn(persisted.turn)
    const nextUser = persisted.user_message
    const nextAssistant = persisted.assistant_message
    const newCount = (nextUser ? 1 : 0) + (nextAssistant ? 1 : 0)
    setMessages((current) => {
      const existing = new Set(current.map((message) => message.id))
      const next = [...current]
      if (nextUser && !existing.has(nextUser.id)) next.push(nextUser)
      if (nextAssistant && !existing.has(nextAssistant.id)) next.push(nextAssistant)
      return next
    })
    setTotalMessages((current) => current + newCount)
  }

  const pendingActions = turn?.pending_actions ?? []
  const trace = turn?.tool_trace ?? []

  return (
    <div className="view">
      <div className="view-header">
        <h1>Agent</h1>
        <p>
          The Agent reads freshly assembled bounded Project context and acts only through canonical
          Project Tools. A conversation here is durable working memory — never durable project truth.
          A Decision created by the Agent is a <Badge tone="warn">draft</Badge> until you commit it.
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

      {loading && conversations.length === 0 && messages.length === 0 ? (
        <Loading label="Loading conversations…" />
      ) : (
        <Section title="Conversations (persisted working memory)">
          <div className="list-row">
            <Button type="button" onClick={newConversation} disabled={busy || loading}>
              <MessageSquarePlus size={15} /> New conversation
            </Button>
          </div>
          {conversations.length === 0 ? <Empty label="No conversations yet. Start one to talk to the Agent." /> : null}
          {conversations.map((conversation) => (
            <div
              className={`list-row conversation-row ${conversation.id === activeConversationId ? 'active' : ''}`}
              key={conversation.id}
            >
              <button type="button" className="conversation-link" onClick={() => openConversation(conversation.id)}>
                <strong>{conversation.title}</strong>
                <small>{conversation.updated_at.slice(0, 16).replace('T', ' ')}</small>
              </button>
            </div>
          ))}
        </Section>
      )}

      <Section title="Conversation">
        {messages.length === 0 ? <Empty label="Ask the Agent to analyze the selected context." /> : null}
        {totalMessages - messages.length > 0 ? (
          <p className="muted-note">
            Showing the latest {messages.length} of {totalMessages} messages.
          </p>
        ) : null}
        {messages.map((message) => (
          <div
            className={`list-row ${message.role === 'assistant' ? 'assistant-row' : ''}`}
            key={message.id}
          >
            <div className="list-row-head">
              {message.role === 'assistant' ? <Bot size={15} /> : null}
              <strong>{message.role === 'assistant' ? 'Agent' : 'You'}</strong>
              {message.role === 'assistant' && message.termination_reason && message.termination_reason !== AGENT_TERMINATION_REASON_FINAL_RESPONSE ? (
                <Badge tone="warn">{message.termination_reason}</Badge>
              ) : null}
            </div>
            <p className="pre-line">{message.content}</p>
            {(message.tool_trace ?? []).length > 0 ? (
              <small className="muted-note">
                Tools:{' '}
                {(message.tool_trace ?? []).map((entry) => entry.tool_id).join(', ')}
              </small>
            ) : null}
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
            <Button type="submit" disabled={busy || loading || !input.trim()}>
              <Send size={15} /> {busy ? 'Running…' : 'Send'}
            </Button>
            {actionError ? <span className="inline-error">{actionError}</span> : null}
          </div>
        </form>
      </Section>

      {turn ? (
        <Section title="Last turn">
          <p>
            Termination:{' '}
            <Badge tone={turn.termination_reason === AGENT_TERMINATION_REASON_FINAL_RESPONSE ? 'good' : 'warn'}>
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
