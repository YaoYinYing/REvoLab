import { useEffect, useRef, useState } from 'react'
import { Bot, BookmarkPlus, GitCommitHorizontal, MessageSquarePlus, Send } from 'lucide-react'

import { projectApi } from '../api/backend'
import { apiErrorMessage } from '../api/client'
import { useNotes, useObjects, useResources } from '../api/hooks'
import type {
  AgentTurnRead,
  ConversationMessageRead,
  ConversationRead,
  NoteRead,
} from '../api/types'
import { Button } from '../components/buttons'
import { Badge, Empty, ErrorBox, Field, Loading, Section } from '../components/ui'
import {
  AGENT_TERMINATION_REASON_FINAL_RESPONSE,
  AGENT_TOOL_CALL_STATUS_COMPLETED,
  AGENT_TOOL_CALL_STATUS_FAILED,
  CONVERSATION_ROLE_ASSISTANT,
  CONVERSATION_ROLE_USER,
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

export function AgentView({
  actorId,
  projectId,
  initialNoteIds = [],
}: {
  actorId: string
  projectId: string
  initialNoteIds?: string[]
}) {
  const { data: objects } = useObjects(actorId, projectId)
  const { data: artifacts } = useResources(actorId, projectId, RESOURCE_KIND_ARTIFACT)
  // Include archived notes: archiving is non-destructive and an explicitly
  // handed-off archived note must not be silently dropped by the selector.
  const { data: notes, reload: reloadNotes } = useNotes(actorId, projectId, {
    include_archived: true,
  })
  const [selectedSeriesId, setSelectedSeriesId] = useState('')
  const [selectedArtifactId, setSelectedArtifactId] = useState('')
  const [selectedNoteId, setSelectedNoteId] = useState(initialNoteIds[0] ?? '')
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [conversations, setConversations] = useState<ConversationRead[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ConversationMessageRead[]>([])
  const [totalMessages, setTotalMessages] = useState(0)
  const [turn, setTurn] = useState<AgentTurnRead | null>(null)
  const [savedMessageId, setSavedMessageId] = useState<string | null>(null)
  // A handed-off Note that is a valid current-Project note but falls outside the
  // newest selector page is resolved by id rather than silently dropped.
  const [handoffNote, setHandoffNote] = useState<NoteRead | null>(null)

  const noteInList = notes?.some((note) => note.id === selectedNoteId) ?? false
  // Only a Note that actually belongs to the current Project may be selected: a
  // Project switch clears the hand-off and a stale id is never sent.
  const effectiveNoteId =
    noteInList || handoffNote?.id === selectedNoteId ? selectedNoteId : ''

  useEffect(() => {
    if (!actorId || !projectId || !selectedNoteId) return
    if (noteInList || handoffNote?.id === selectedNoteId) return
    if (!notes) return // wait for the page load before resolving a hand-off
    let cancelled = false
    projectApi(actorId)
      .getNote(projectId, selectedNoteId)
      .then((res) => {
        if (cancelled) return
        if (res.error || !res.data) {
          // Foreign/stale/unreadable id: fail closed, never send it.
          setSelectedNoteId('')
          return
        }
        setHandoffNote(res.data as NoteRead)
      })
      .catch(() => {
        if (!cancelled) setSelectedNoteId('')
      })
    return () => {
      cancelled = true
    }
  }, [actorId, projectId, selectedNoteId, noteInList, notes, handoffNote?.id])

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
    activeConversationRef.current = null
    setActionError(null)
    setInput('')
    setSelectedSeriesId('')
    setSelectedArtifactId('')
    setSelectedNoteId(initialNoteIds[0] ?? '')
    setSavedMessageId(null)
    setHandoffNote(null)
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
    // Keep the synchronous ref in lockstep with the state update so the guard
    // below is not render-order dependent.
    activeConversationRef.current = conversationId
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
        ...(effectiveNoteId ? { note_ids: [effectiveNoteId] } : {}),
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
        max_notes: 10,
        max_note_chars: 4000,
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

  /**
   * Explicit human capture: Conversation -> Project Note. The user clicks this
   * button for one visible message; nothing is promoted in the background. The
   * captured content is an ordinary Note body, so it enters `Note` (shared working
   * knowledge) — never Evidence/Decision truth.
   */
  async function saveMessageToNote(message: ConversationMessageRead) {
    if (busy) return
    setBusy(true)
    setActionError(null)
    const firstLine = message.content.split('\n')[0].trim()
    const title = firstLine.length > 0 ? firstLine.slice(0, 80) : 'From conversation'
    const res = await projectApi(actorId).createNote(projectId, {
      title,
      body: message.content,
      mentions: [],
    })
    setBusy(false)
    if (res.error || !res.data) {
      setActionError(apiErrorMessage(res.error, res.response))
      return
    }
    setSavedMessageId(message.id)
    reloadNotes()
  }

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
          <Field label="Project note (optional, read as untrusted data)">
            <select
              value={effectiveNoteId}
              onChange={(event) => setSelectedNoteId(event.target.value)}
              aria-label="Select note for agent context"
            >
              <option value="">— none —</option>
              {notes?.map((note) => (
                <option key={note.id} value={note.id}>
                  {note.title} (rev {note.latest_revision_seq})
                </option>
              ))}
              {handoffNote && !noteInList ? (
                <option key={handoffNote.id} value={handoffNote.id}>
                  {handoffNote.title} (rev {handoffNote.latest_revision_seq})
                </option>
              ) : null}
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
            className={`list-row ${message.role === CONVERSATION_ROLE_ASSISTANT ? 'assistant-row' : ''}`}
            key={message.id}
          >
            <div className="list-row-head">
              {message.role === CONVERSATION_ROLE_ASSISTANT ? <Bot size={15} /> : null}
              <strong>
                {message.role === CONVERSATION_ROLE_ASSISTANT
                  ? 'Agent'
                  : message.role === CONVERSATION_ROLE_USER
                    ? 'You'
                    : message.role}
              </strong>
              {message.role === CONVERSATION_ROLE_ASSISTANT && message.termination_reason && message.termination_reason !== AGENT_TERMINATION_REASON_FINAL_RESPONSE ? (
                <Badge tone="warn">{message.termination_reason}</Badge>
              ) : null}
            </div>
            <p className="pre-line">{message.content}</p>
            <div className="list-row-head">
              <Button
                type="button"
                kind="quiet"
                onClick={() => saveMessageToNote(message)}
                disabled={busy}
                aria-label={`Save message to project note ${message.id}`}
              >
                <BookmarkPlus size={13} /> Save to Project Note
              </Button>
              {savedMessageId === message.id ? (
                <small className="muted-note">Saved to Notes (working knowledge, not truth).</small>
              ) : null}
            </div>
            {(message.tool_trace ?? []).length > 0 ? (
              <small className="muted-note">
                Tools:{' '}
                {(message.tool_trace ?? []).map((entry, index) => (
                  // Reuse the canonical status→tone mapping so a persisted
                  // `pending` (proposed, not executed) reads as a warning, never
                  // as a failure, and keep the inert NOT-executed framing visible
                  // after reload.
                  <Badge key={`${entry.tool_id}-${index}`} tone={traceTone(entry.status)}>
                    {entry.tool_id} ({entry.status})
                    {entry.status === AGENT_TOOL_CALL_STATUS_PENDING && entry.pending_reason
                      ? ` — NOT executed: ${entry.pending_reason}`
                      : entry.error
                        ? ` — ${entry.error}`
                        : ''}
                  </Badge>
                ))}
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
