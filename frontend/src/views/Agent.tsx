import { useEffect, useRef, useState } from 'react'
import { Ban, Bot, BookmarkPlus, Play, Send, MessageSquarePlus } from 'lucide-react'

import { projectApi } from '../api/backend'
import { apiErrorMessage } from '../api/client'
import {
  useConversationActionRequests,
  useMyMembership,
  useNotes,
  useObjects,
  useResources,
} from '../api/hooks'
import type {
  ActionRequestRead,
  AgentTurnRead,
  ConversationMessageRead,
  ConversationRead,
  NoteRead,
} from '../api/types'
import { Button } from '../components/buttons'
import { Badge, Empty, ErrorBox, Field, Loading, Section } from '../components/ui'
import { contextItemsToSelection, targetKindLabel, type AgentContextItem } from './agentContext'
import {
  ACTION_REQUEST_STATUS_AMBIGUOUS,
  ACTION_REQUEST_STATUS_EXECUTING,
  ACTION_REQUEST_STATUS_FAILED,
  ACTION_REQUEST_STATUS_PENDING,
  ACTION_REQUEST_STATUS_REJECTED,
  ACTION_REQUEST_STATUS_SUCCEEDED,
  AGENT_TERMINATION_REASON_FINAL_RESPONSE,
  AGENT_TOOL_CALL_STATUS_COMPLETED,
  AGENT_TOOL_CALL_STATUS_FAILED,
  CONVERSATION_ROLE_ASSISTANT,
  CONVERSATION_ROLE_USER,
  AGENT_TOOL_CALL_STATUS_PENDING,
  RESOURCE_KIND_ARTIFACT,
  ROLE_MEMBER,
  ROLE_OWNER,
  TOOL_EXECUTION_CLASS_REMOTE,
} from '../contracts/enums'

/** Presentation-only tone for a durable Action Request state. The state VALUES
 * come from the generated contract; never a bare literal. */
function actionTone(status: string): 'neutral' | 'good' | 'warn' {
  if (status === ACTION_REQUEST_STATUS_SUCCEEDED) return 'good'
  if (status === ACTION_REQUEST_STATUS_PENDING) return 'warn'
  if (status === ACTION_REQUEST_STATUS_AMBIGUOUS) return 'warn'
  if (status === ACTION_REQUEST_STATUS_FAILED) return 'warn'
  if (status === ACTION_REQUEST_STATUS_REJECTED) return 'neutral'
  return 'neutral'
}

/** The canonical bounded arguments rendered for human review. Compute
 * submissions highlight provider, task kind and input identities; credential
 * material is never part of an Action Request at all. */
function describeAction(action: ActionRequestRead): string[] {
  const args = action.arguments ?? {}
  const lines: string[] = []
  if (typeof args.provider_key === 'string') lines.push(`provider: ${args.provider_key}`)
  if (typeof args.task_kind === 'string') lines.push(`task kind: ${args.task_kind}`)
  const inputs = Array.isArray(args.inputs) ? args.inputs : []
  for (const input of inputs) {
    if (input && typeof input === 'object') {
      const record = input as Record<string, unknown>
      if (typeof record.resource_id === 'string') {
        lines.push(`input ${String(record.kind ?? '')}: ${record.resource_id}`)
      }
    }
  }
  if (args.params && typeof args.params === 'object') {
    lines.push(`parameters: ${JSON.stringify(args.params)}`)
  }
  if (typeof args.decision_id === 'string') lines.push(`decision: ${args.decision_id}`)
  if (lines.length === 0) lines.push(JSON.stringify(args))
  return lines
}

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
  initialContextItems = [],
}: {
  actorId: string
  projectId: string
  initialNoteIds?: string[]
  /**
   * Phase-12 explicit search -> Agent-context handoff. Each item is projected
   * into the canonical `ContextSelectionCreate` at send time; nothing is added
   * to context implicitly by searching, and private conversation hits can never
   * appear here.
   */
  initialContextItems?: AgentContextItem[]
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
  const [contextItems, setContextItems] = useState<AgentContextItem[]>(initialContextItems)
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
  const [actionBusyId, setActionBusyId] = useState<string | null>(null)
  const [actionNotice, setActionNotice] = useState<string | null>(null)
  // A handed-off Note that is a valid current-Project note but falls outside the
  // newest selector page is resolved by id rather than silently dropped.
  const [handoffNote, setHandoffNote] = useState<NoteRead | null>(null)

  const { data: membership } = useMyMembership(actorId, projectId)
  // Execute/reject are offered only to a currently mutation-capable membership;
  // authority itself is always re-enforced by the backend at execution time.
  const canDecideActions =
    membership?.role === ROLE_OWNER || membership?.role === ROLE_MEMBER
  const {
    data: actionRequests,
    reload: reloadActionRequests,
  } = useConversationActionRequests(actorId, projectId, activeConversationId, {
    limit: 50,
  })

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
    setContextItems(initialContextItems)
    setSavedMessageId(null)
    setHandoffNote(null)
    setActionBusyId(null)
    setActionNotice(null)
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
    setActionNotice(null)
    setActionBusyId(null)
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
    // The ONE canonical ContextSelection: the single selectors plus the explicit
    // search hand-off items, projected into typed id lists. This is not a second
    // context model, and nothing here was added by searching alone.
    const handoff = contextItemsToSelection(contextItems)
    const union = (...groups: (string[] | null | undefined)[]) => [
      ...new Set(groups.flatMap((group) => group ?? [])),
    ]
    const seriesIds = union(selectedSeriesId ? [selectedSeriesId] : [], handoff.series_ids)
    const artifactIds = union(selectedArtifactId ? [selectedArtifactId] : [], handoff.artifact_ids)
    const noteIds = union(effectiveNoteId ? [effectiveNoteId] : [], handoff.note_ids)
    const res = await projectApi(actorId).createConversationTurn(projectId, conversationId, {
      message: userMessage,
      selection: {
        ...(seriesIds.length ? { series_ids: seriesIds } : {}),
        ...(artifactIds.length ? { artifact_ids: artifactIds } : {}),
        ...(noteIds.length ? { note_ids: noteIds } : {}),
        ...(handoff.evidence_ids?.length ? { evidence_ids: handoff.evidence_ids } : {}),
        ...(handoff.decision_ids?.length ? { decision_ids: handoff.decision_ids } : {}),
        ...(handoff.reference_ids?.length ? { reference_ids: handoff.reference_ids } : {}),
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
    // A turn may have persisted NEW Action Requests: re-read the durable truth
    // instead of trusting the ephemeral response payload.
    void reloadActionRequests()
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
   * Explicit human authorization. `execute`/`reject` are requested ONLY from a
   * user click on a pending Action Request — never on render, reload, navigation
   * or a model response. The backend re-derives all authority from current truth.
   */
  async function decideAction(action: ActionRequestRead, decision: 'execute' | 'reject') {
    if (actionBusyId || busy) return
    const requestScope = scopeRef.current
    const requestConversation = activeConversationRef.current
    setActionBusyId(action.id)
    setActionNotice(null)
    setActionError(null)
    const api = projectApi(actorId)
    const res =
      decision === 'execute'
        ? await api.executeActionRequest(projectId, action.id)
        : await api.rejectActionRequest(projectId, action.id)
    // A stale response from a previous Actor x Project x conversation scope must
    // never repopulate the current one.
    if (
      scopeRef.current !== requestScope ||
      activeConversationRef.current !== requestConversation
    ) {
      return
    }
    setActionBusyId(null)
    if (res.error || !res.data) {
      setActionError(apiErrorMessage(res.error, res.response))
      void reloadActionRequests()
      return
    }
    const updated = res.data as ActionRequestRead
    if (updated.status === ACTION_REQUEST_STATUS_SUCCEEDED) {
      setActionNotice(
        updated.result_run_id
          ? `Executed. Canonical run reference ${updated.result_run_id} — see Runs & Artifacts.`
          : updated.result_decision_id
            ? `Executed. Committed decision ${updated.result_decision_id}.`
            : 'Executed.',
      )
    } else if (updated.status === ACTION_REQUEST_STATUS_AMBIGUOUS) {
      setActionNotice(
        'The provider outcome is uncertain. This action will NOT be retried automatically; reconcile with the provider or propose a new action.',
      )
    } else if (updated.status === ACTION_REQUEST_STATUS_FAILED) {
      setActionNotice(`Execution failed with no external side effect: ${updated.status_reason ?? ''}`)
    } else {
      setActionNotice(`Action is now ${updated.status}.`)
    }
    void reloadActionRequests()
  }

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
        {contextItems.length > 0 ? (
          <div className="context-selection" aria-label="Selected for agent context">
            <span className="field-label">Added from search (visible before sending)</span>
            <div className="chip-row">
              {contextItems.map((item) => (
                <span className="chip" key={`${item.target_kind}:${item.target_id}`}>
                  <span className="chip-kind">{targetKindLabel(item.target_kind)}</span>
                  {item.title}
                  <button
                    type="button"
                    className="chip-remove"
                    aria-label={`Remove ${item.title} from agent context`}
                    onClick={() =>
                      setContextItems((current) =>
                        current.filter(
                          (candidate) =>
                            !(
                              candidate.target_kind === item.target_kind &&
                              candidate.target_id === item.target_id
                            ),
                        ),
                      )
                    }
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          </div>
        ) : null}
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
            <Section title="Proposed by this turn (not executed)">
              <p className="truth-boundary">
                The Agent proposed these actions but did NOT execute them. Each one is persisted as a
                durable Action Request below; nothing is authorized until you explicitly execute it.
              </p>
              {pendingActions.map((action, index) => (
                <div className="list-row" key={index}>
                  <div className="list-row-head">
                    <strong className="mono">{action.tool_id}</strong>
                    <Badge tone="warn">{action.autonomy}</Badge>
                  </div>
                  <p>{action.summary}</p>
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

      <Section title="Action requests (durable intent, never authority)">
        <p className="truth-boundary">
          A durable Action Request records what the Agent proposed. It is never permission to execute:
          executing re-checks your current membership, the current Tool, provider availability,
          credentials and resource visibility, then runs the canonical path.
        </p>
        {actionNotice ? <p className="muted-note">{actionNotice}</p> : null}
        {activeConversationId == null ? (
          <Empty label="Open a conversation to see its durable action requests." />
        ) : null}
        {activeConversationId != null && (actionRequests ?? []).length === 0 ? (
          <Empty label="No action requests in this conversation yet." />
        ) : null}
        {(actionRequests ?? []).map((action) => (
          <div className="list-row" key={action.id}>
            <div className="list-row-head">
              <strong className="mono">{action.tool_id}</strong>
              <Badge tone={actionTone(action.status)}>{action.status}</Badge>
              <Badge>{action.execution_class}</Badge>
              <Badge>{action.side_effect_class}</Badge>
            </div>
            <ul className="muted-note">
              {describeAction(action).map((line, index) => (
                <li key={index} className="mono">
                  {line}
                </li>
              ))}
            </ul>
            <p className="muted-note">
              {action.execution_class === TOOL_EXECUTION_CLASS_REMOTE
                ? 'Execute submits this task to the provider through the canonical compute path.'
                : 'Execute runs this local explicit action through the canonical closed tool runtime.'}
            </p>
            {action.status_reason ? (
              <p className="inline-error">{action.status_reason}</p>
            ) : null}
            {action.result_run_id ? (
              <small className="muted-note">
                Canonical run reference {action.result_run_id} — visible in Runs &amp; Artifacts.
              </small>
            ) : null}
            {action.result_decision_id ? (
              <small className="muted-note">Committed decision {action.result_decision_id}.</small>
            ) : null}
            {action.status === ACTION_REQUEST_STATUS_PENDING ? (
              <div className="list-row-head">
                <Button
                  type="button"
                  onClick={() => decideAction(action, 'execute')}
                  disabled={!canDecideActions || actionBusyId !== null || busy}
                  aria-label={`Execute action request ${action.id}`}
                >
                  <Play size={13} /> Execute
                </Button>
                <Button
                  type="button"
                  kind="quiet"
                  onClick={() => decideAction(action, 'reject')}
                  disabled={!canDecideActions || actionBusyId !== null || busy}
                  aria-label={`Reject action request ${action.id}`}
                >
                  <Ban size={13} /> Reject
                </Button>
                {!canDecideActions ? (
                  <small className="muted-note">Owner/member membership required to decide.</small>
                ) : null}
              </div>
            ) : null}
            {action.status === ACTION_REQUEST_STATUS_EXECUTING ? (
              <small className="muted-note">
                Execution claimed; the outcome is not yet recorded. It will not be retried automatically.
              </small>
            ) : null}
          </div>
        ))}
      </Section>

      <p className="truth-boundary">
        Agent output ≠ committed Project Knowledge. A Decision created by the Agent is always a{' '}
        <Badge tone="warn">draft</Badge> until you commit it through the Decisions view.
      </p>
    </div>
  )
}
