import { useEffect, useState } from 'react'
import { Archive, Bot, History, Link2, NotebookPen, Plus, Save } from 'lucide-react'

import { projectApi } from '../api/backend'
import { apiErrorMessage } from '../api/client'
import {
  useDecisions,
  useEvidence,
  useMyMembership,
  useNoteDetail,
  useNoteRevisions,
  useNotes,
  useObjects,
  useResources,
} from '../api/hooks'
import type { NoteMentionCreate, NoteMentionRead } from '../api/types'
import { Button } from '../components/buttons'
import { Markdown } from '../components/Markdown'
import { Badge, Empty, ErrorBox, Field, LoadMore, Loading, Section } from '../components/ui'
import { ROLE_MEMBER, ROLE_OWNER } from '../contracts/enums'

const PAGE_SIZE = 50
const REVISION_PAGE_SIZE = 100
// Server-side hard caps (`le=200` on both endpoints): never request beyond them.
const NOTES_MAX = 200
const REVISIONS_MAX = 200

/** Encode a mention target as a stable option value. */
function mentionOptionToPayload(value: string): NoteMentionCreate {
  const separator = value.indexOf(':')
  const kind = value.slice(0, separator)
  const id = value.slice(separator + 1)
  if (kind === 'evidence') return { evidence_id: id }
  if (kind === 'decision') return { decision_id: id }
  return { resource_id: id }
}

function mentionKey(mention: NoteMentionRead): string {
  if (mention.evidence_id) return `evidence:${mention.evidence_id}`
  if (mention.decision_id) return `decision:${mention.decision_id}`
  return `resource:${mention.resource_id}`
}

function mentionLabel(mention: NoteMentionRead): string {
  if (mention.resolved) return mention.label ?? 'referenced entity'
  if (mention.evidence_id) return 'unresolved evidence'
  if (mention.decision_id) return 'unresolved decision'
  return 'unresolved reference'
}

function mentionPayloadKey(mention: NoteMentionCreate): string {
  if (mention.evidence_id) return `evidence:${mention.evidence_id}`
  if (mention.decision_id) return `decision:${mention.decision_id}`
  return `resource:${mention.resource_id}`
}

export function NotebookView({
  actorId,
  projectId,
  onAddToAgentContext,
}: {
  actorId: string
  projectId: string
  onAddToAgentContext: (noteId: string) => void
}) {
  const [limit, setLimit] = useState(PAGE_SIZE)
  const [revisionLimit, setRevisionLimit] = useState(REVISION_PAGE_SIZE)
  const [showArchived, setShowArchived] = useState(false)
  const [selectedNoteId, setSelectedNoteId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const [showCreate, setShowCreate] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [newBody, setNewBody] = useState('')
  const [pendingMentions, setPendingMentions] = useState<NoteMentionCreate[]>([])
  const [mentionTarget, setMentionTarget] = useState('')

  const [editBody, setEditBody] = useState('')
  const [clearMentions, setClearMentions] = useState(false)

  const notes = useNotes(actorId, projectId, { limit, include_archived: showArchived })
  const detail = useNoteDetail(actorId, projectId, selectedNoteId)
  const revisions = useNoteRevisions(actorId, projectId, selectedNoteId, { limit: revisionLimit })
  const membership = useMyMembership(actorId, projectId)
  const { data: objects } = useObjects(actorId, projectId)
  const { data: evidence } = useEvidence(actorId, projectId, { limit: 100 })
  const { data: decisions } = useDecisions(actorId, projectId, { limit: 100 })
  const { data: resources } = useResources(actorId, projectId, null, { limit: 100 })

  // Owner/member may mutate; a viewer is read-only. This only hides affordances —
  // the backend re-enforces authority on every write.
  const canMutate =
    membership.data?.role === ROLE_OWNER || membership.data?.role === ROLE_MEMBER

  // Never render or mutate stale detail: the loaded detail must belong to the
  // currently selected Note (useAsync keeps the previous value during a reload).
  const selected = detail.data && detail.data.id === selectedNoteId ? detail.data : null
  // Revisions carry their note_id, so a stale page from the previous Note can be
  // filtered out instead of rendering under the new Note.
  const selectedRevisions = (revisions.data ?? []).filter(
    (revision) => revision.note_id === selectedNoteId,
  )

  useEffect(() => {
    // Clear the editor and any transient outcome on selection change so the form
    // can never briefly hold the previous Note's body/notice/error.
    setEditBody('')
    setClearMentions(false)
    setNotice(null)
    setActionError(null)
    setRevisionLimit(REVISION_PAGE_SIZE)
  }, [selectedNoteId])

  useEffect(() => {
    const latest = detail.data?.latest
    if (latest && detail.data?.id === selectedNoteId) {
      setEditBody(latest.body)
    }
  }, [detail.data?.id, detail.data?.latest?.revision_id, detail.data?.latest?.body, selectedNoteId])

  useEffect(() => {
    // Scope reset: a new Project must not keep a Note selected from another.
    setSelectedNoteId(null)
    setShowCreate(false)
    setNewTitle('')
    setNewBody('')
    setPendingMentions([])
    setNotice(null)
    setActionError(null)
  }, [actorId, projectId])

  const mentionOptions = [
    ...(objects ?? []).map((object) => ({
      value: `series:${object.series_id}`,
      label: `Object · ${object.name}`,
    })),
    ...(evidence ?? []).map((item) => ({
      value: `evidence:${item.id}`,
      label: `Evidence · ${item.label ?? item.kind}`,
    })),
    ...(decisions ?? []).map((item) => ({
      value: `decision:${item.id}`,
      label: `Decision · ${item.title}`,
    })),
    ...(resources ?? []).map((item) => ({
      value: `resource:${item.resource_id}`,
      label: `Reference · ${item.native_id ?? item.resource_id.slice(0, 8)}`,
    })),
  ]

  function addPendingMention() {
    if (!mentionTarget) return
    const payload = mentionOptionToPayload(mentionTarget)
    setPendingMentions((current) =>
      current.some((item) => mentionPayloadKey(item) === mentionPayloadKey(payload))
        ? current
        : [...current, payload],
    )
    setMentionTarget('')
  }

  async function createNote(event: React.FormEvent) {
    event.preventDefault()
    if (!newTitle.trim() || !newBody.trim() || busy) return
    setBusy(true)
    setActionError(null)
    setNotice(null)
    const res = await projectApi(actorId).createNote(projectId, {
      title: newTitle.trim(),
      body: newBody,
      mentions: pendingMentions,
    })
    setBusy(false)
    if (res.error || !res.data) {
      setActionError(apiErrorMessage(res.error, res.response))
      return
    }
    setNewTitle('')
    setNewBody('')
    setPendingMentions([])
    setShowCreate(false)
    setSelectedNoteId(res.data.id)
    setNotice('Note created.')
    notes.reload()
  }

  async function appendRevision(event: React.FormEvent) {
    event.preventDefault()
    const latest = selected?.latest
    if (!latest || !editBody.trim() || busy) return
    setBusy(true)
    setActionError(null)
    setNotice(null)
    const res = await projectApi(actorId).appendNoteRevision(projectId, latest.note_id, {
      base_revision_seq: latest.revision_seq,
      body: editBody,
      // Omitted `mentions` inherits the previous revision's links (the durable
      // default); an explicit [] clears them.
      ...(clearMentions ? { mentions: [] } : {}),
    })
    setBusy(false)
    if (res.error || !res.data) {
      setActionError(apiErrorMessage(res.error, res.response))
      return
    }
    setNotice(`Revision #${res.data.revision_seq} appended.`)
    setClearMentions(false)
    detail.reload()
    revisions.reload()
    notes.reload()
  }

  async function toggleArchive(archive: boolean) {
    if (!selectedNoteId || busy) return
    setBusy(true)
    setActionError(null)
    const res = await projectApi(actorId).patchNote(projectId, selectedNoteId, { archive })
    setBusy(false)
    if (res.error || !res.data) {
      setActionError(apiErrorMessage(res.error, res.response))
      return
    }
    detail.reload()
    notes.reload()
  }

  return (
    <div className="view">
      <div className="view-header">
        <h1>Notes</h1>
        <p>
          The Project Notebook: shared working knowledge for this Project. A Note is editable,
          versioned working text — it is not Evidence, not a Decision, and never scientific-graph
          truth. Editing appends an immutable revision.
        </p>
      </div>

      <Section
        title="Notebook"
        actions={
          <div className="list-row-head">
            <label className="check-line">
              <input
                type="checkbox"
                checked={showArchived}
                onChange={(event) => setShowArchived(event.target.checked)}
              />{' '}
              Show archived
            </label>
            {membership.data ? (
              canMutate ? null : <Badge tone="neutral">read-only (viewer)</Badge>
            ) : null}
            {canMutate ? (
              <Button type="button" kind="quiet" onClick={() => setShowCreate((value) => !value)}>
                <Plus size={15} /> New note
              </Button>
            ) : null}
          </div>
        }
      >
        {showCreate && canMutate ? (
          <form className="stack-form" onSubmit={createNote}>
            <Field label="Title">
              <input
                value={newTitle}
                onChange={(event) => setNewTitle(event.target.value)}
                maxLength={200}
                required
                aria-label="New note title"
                placeholder="What are we working on?"
              />
            </Field>
            <Field label="Body (Markdown)">
              <textarea
                rows={6}
                value={newBody}
                onChange={(event) => setNewBody(event.target.value)}
                required
                aria-label="New note body"
                placeholder="Bounded Markdown. Raw HTML is never rendered as active content."
              />
            </Field>
            <Field label="Link Project context (optional)">
              <div className="list-row-head">
                <select
                  value={mentionTarget}
                  onChange={(event) => setMentionTarget(event.target.value)}
                  aria-label="Mention target"
                >
                  <option value="">— none —</option>
                  {mentionOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <Button type="button" kind="quiet" onClick={addPendingMention} disabled={!mentionTarget}>
                  <Link2 size={14} /> Add mention
                </Button>
              </div>
            </Field>
            {pendingMentions.length > 0 ? (
              <div className="chip-row">
                {pendingMentions.map((mention) => (
                  <button
                    type="button"
                    key={mentionPayloadKey(mention)}
                    className="chip"
                    onClick={() =>
                      setPendingMentions((current) =>
                        current.filter((item) => mentionPayloadKey(item) !== mentionPayloadKey(mention)),
                      )
                    }
                  >
                    {mentionPayloadKey(mention)} ✕
                  </button>
                ))}
              </div>
            ) : null}
            <div className="form-actions">
              <Button type="submit" disabled={busy || !newTitle.trim() || !newBody.trim()}>
                <NotebookPen size={15} /> Create note
              </Button>
            </div>
          </form>
        ) : null}

        {notes.loading ? <Loading label="Loading notes…" /> : null}
        <ErrorBox message={notes.error} />
        {actionError ? <div className="inline-error">{actionError}</div> : null}
        {notice ? <p className="muted-note">{notice}</p> : null}
        {notes.data && notes.data.length === 0 ? (
          <Empty label="No notes in this project yet. Create one to start shared working notes." />
        ) : null}
        <div className="list">
          {(notes.data ?? []).map((note) => (
            <div className={`list-row note-row ${note.id === selectedNoteId ? 'active' : ''}`} key={note.id}>
              <button type="button" className="conversation-link" onClick={() => setSelectedNoteId(note.id)}>
                <strong>{note.title}</strong>
                <small>
                  rev {note.latest_revision_seq} · {note.revision_count} revision(s) ·{' '}
                  {note.updated_at.slice(0, 16).replace('T', ' ')}
                </small>
              </button>
              {note.archived_at ? <Badge tone="warn">archived</Badge> : null}
            </div>
          ))}
        </div>
        <LoadMore
          visible={(notes.data?.length ?? 0) === limit && limit < NOTES_MAX}
          onLoad={() => setLimit((value) => Math.min(value + PAGE_SIZE, NOTES_MAX))}
        />
      </Section>

      {selectedNoteId ? <ErrorBox message={detail.error} /> : null}
      {selectedNoteId && !selected && !detail.error ? <Loading label="Loading note…" /> : null}

      {selectedNoteId && selected ? (
        <Section
          title={selected.title}
          actions={
            <div className="list-row-head">
              <Button
                type="button"
                kind="quiet"
                onClick={() => onAddToAgentContext(selected.id)}
                aria-label="Add note to agent context"
              >
                <Bot size={14} /> Add to Agent context
              </Button>
              {canMutate ? (
                <Button
                  type="button"
                  kind="quiet"
                  onClick={() => toggleArchive(selected.archived_at === null)}
                  disabled={busy}
                >
                  <Archive size={14} /> {selected.archived_at === null ? 'Archive' : 'Unarchive'}
                </Button>
              ) : null}
            </div>
          }
        >
          <dl className="identity-grid">
            <div>
              <dt>Note</dt>
              <dd className="mono">{selected.id}</dd>
            </div>
            <div>
              <dt>Created by (audit)</dt>
              <dd className="mono">{selected.created_by_actor_id.slice(0, 8)}…</dd>
            </div>
            <div>
              <dt>Latest revision</dt>
              <dd>#{selected.latest_revision_seq}</dd>
            </div>
            <div>
              <dt>Revisions</dt>
              <dd>{selected.revision_count}</dd>
            </div>
          </dl>

          {selected.latest ? (
            <>
              <Markdown source={selected.latest.body} />
              <small className="muted-note">
                authored by <span className="mono">{selected.latest.created_by_actor_id.slice(0, 8)}…</span> at{' '}
                {selected.latest.created_at.slice(0, 16).replace('T', ' ')}
              </small>
              {(selected.latest.mentions ?? []).length > 0 ? (
                <div className="chip-row">
                  {(selected.latest.mentions ?? []).map((mention) => (
                    <span
                      key={mentionKey(mention)}
                      className={`chip ${mention.resolved ? '' : 'chip-warn'}`}
                      title={mention.resolved ? 'Project-visible reference' : 'target no longer visible'}
                    >
                      {mentionLabel(mention)}
                      {mention.resolved ? '' : ' (unresolved)'}
                    </span>
                  ))}
                </div>
              ) : null}
            </>
          ) : null}

          <p className="truth-boundary">
            A Note is Project working knowledge, not scientific truth. It does not become Evidence or a
            Decision, and it creates no provenance edge. Mentions are non-semantic references only.
          </p>

          <form className="stack-form" onSubmit={appendRevision}>
            <Field label="Edit (appends a new immutable revision)">
              <textarea
                rows={6}
                value={editBody}
                onChange={(event) => setEditBody(event.target.value)}
                aria-label="Edit note body"
                required
                disabled={!canMutate}
              />
            </Field>
            <div className="form-actions">
              {canMutate ? (
                <>
                  <Button type="submit" disabled={busy || !editBody.trim()}>
                    <Save size={15} /> Save revision
                  </Button>
                  <label className="check-line">
                    <input
                      type="checkbox"
                      checked={clearMentions}
                      onChange={(event) => setClearMentions(event.target.checked)}
                      aria-label="Clear context links on next revision"
                    />{' '}
                    Clear context links on next revision
                  </label>
                </>
              ) : (
                <span className="muted-note">Read-only: only owner/member may append revisions.</span>
              )}
            </div>
            <small className="muted-note">
              Saving a body-only edit keeps the current context links by default.
            </small>
          </form>

          <Section title="Revision history">
            {revisions.loading ? <Loading label="Loading revisions…" /> : null}
            <ErrorBox message={revisions.error} />
            {selectedRevisions.map((revision) => (
              <div className="list-row revision-row" key={revision.revision_id}>
                <div className="list-row-head">
                  <History size={14} />
                  <strong>Revision #{revision.revision_seq}</strong>
                  <small className="mono">
                    {revision.created_at.slice(0, 16).replace('T', ' ')} ·{' '}
                    {revision.created_by_actor_id.slice(0, 8)}…
                  </small>
                </div>
                <Markdown source={revision.body} />
              </div>
            ))}
            <LoadMore
              visible={
                (revisions.data?.length ?? 0) === revisionLimit && revisionLimit < REVISIONS_MAX
              }
              onLoad={() => setRevisionLimit((value) => Math.min(value + REVISION_PAGE_SIZE, REVISIONS_MAX))}
            />
          </Section>
        </Section>
      ) : null}
    </div>
  )
}
