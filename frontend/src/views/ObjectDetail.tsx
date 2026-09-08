import { useState } from 'react'
import { ArrowLeft, Boxes, FileText, GitBranch, Plus } from 'lucide-react'

import { projectApi } from '../api/backend'
import type {
  DecisionRead,
  EvidenceRead,
  ObjectDetailRead,
  Polarity,
  EvidenceKind,
} from '../api/types'
import { Button } from '../components/buttons'
import { Badge, Empty, ErrorBox, EnumSelect, Field, Loading, Section } from '../components/ui'
import {
  DECISION_STATUS_COMMITTED,
  DECISION_STATUS_DRAFT,
  DEFAULT_CITED_AS,
  DEFAULT_EVIDENCE_KIND,
  DEFAULT_EVIDENCE_ROLE,
  DEFAULT_EVIDENCE_TARGET_KIND,
  DEFAULT_POLARITY,
  DEFAULT_SELECT_TARGET_KIND,
  EVIDENCE_KINDS,
  POLARITIES,
} from '../contracts/enums'

function EvidenceForm({
  actorId,
  projectId,
  detail,
  onDone,
}: {
  actorId: string
  projectId: string
  detail: ObjectDetailRead
  onDone: () => void
}) {
  const latest = detail.visible_revisions.at(-1)
  const [kind, setKind] = useState<EvidenceKind>(DEFAULT_EVIDENCE_KIND)
  const [interpretation, setInterpretation] = useState('')
  const [polarity, setPolarity] = useState<Polarity>(DEFAULT_POLARITY)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!latest) return null

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    const res = await projectApi(actorId).createEvidence(projectId, {
      kind,
      role: DEFAULT_EVIDENCE_ROLE,
      interpretation: interpretation || null,
      polarity,
      target_kind: DEFAULT_EVIDENCE_TARGET_KIND,
      target_id: latest!.revision_id,
    })
    setBusy(false)
    if (res.error || !res.data) {
      setError('Evidence creation failed.')
      return
    }
    setInterpretation('')
    onDone()
  }

  return (
    <form className="stack-form compact" onSubmit={onSubmit}>
      <div className="form-grid">
        <Field label="Kind">
          <EnumSelect value={kind} options={EVIDENCE_KINDS} onChange={setKind} />
        </Field>
        <Field label="Polarity">
          <EnumSelect value={polarity} options={POLARITIES} onChange={setPolarity} />
        </Field>
      </div>
      <Field label="Interpretation">
        <input
          value={interpretation}
          onChange={(event) => setInterpretation(event.target.value)}
          placeholder="What does this evidence say about the object?"
        />
      </Field>
      <div className="form-actions">
        <Button type="submit" disabled={busy}>
          {busy ? 'Recording…' : 'Record evidence'}
        </Button>
        {error ? <span className="inline-error">{error}</span> : null}
      </div>
      <small className="hint">Target: latest visible revision {latest.revision_id.slice(0, 8)}…</small>
    </form>
  )
}

function DecisionForm({
  actorId,
  projectId,
  detail,
  onDone,
}: {
  actorId: string
  projectId: string
  detail: ObjectDetailRead
  onDone: () => void
}) {
  const [title, setTitle] = useState('')
  const [statement, setStatement] = useState('')
  const [citeIds, setCiteIds] = useState<string[]>([])
  const [selectSeries, setSelectSeries] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    const res = await projectApi(actorId).createDecision(projectId, {
      title,
      statement,
      next_actions: [],
      cites: citeIds.map((evidence_id) => ({ evidence_id, cited_as: DEFAULT_CITED_AS })),
      selects: selectSeries
        ? [{ target_id: detail.series.series_id, target_kind: DEFAULT_SELECT_TARGET_KIND }]
        : [],
    })
    setBusy(false)
    if (res.error || !res.data) {
      setError('Decision draft creation failed.')
      return
    }
    setTitle('')
    setStatement('')
    setCiteIds([])
    onDone()
  }

  function toggleCite(id: string) {
    setCiteIds((ids) => (ids.includes(id) ? ids.filter((value) => value !== id) : [...ids, id]))
  }

  return (
    <form className="stack-form compact" onSubmit={onSubmit}>
      <Field label="Title">
        <input
          placeholder="Decision title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          required
          maxLength={200}
        />
      </Field>
      <Field label="Statement">
        <textarea
          rows={2}
          value={statement}
          onChange={(event) => setStatement(event.target.value)}
          required
          placeholder="Decision statement"
        />
      </Field>
      {detail.evidence.length > 0 ? (
        <fieldset className="check-list">
          <legend>Cite evidence</legend>
          {detail.evidence.map((item) => (
            <label key={item.id}>
              <input type="checkbox" checked={citeIds.includes(item.id)} onChange={() => toggleCite(item.id)} />
              <span>{item.kind}: {item.interpretation ?? item.label ?? 'evidence'}</span>
            </label>
          ))}
        </fieldset>
      ) : null}
      <label className="check-line">
        <input type="checkbox" checked={selectSeries} onChange={(event) => setSelectSeries(event.target.checked)} />
        <span>Select this object ({detail.series.object_type}: {detail.series.name})</span>
      </label>
      <div className="form-actions">
        <Button type="submit" disabled={busy}>
          {busy ? 'Saving…' : 'Save draft'}
        </Button>
        {error ? <span className="inline-error">{error}</span> : null}
      </div>
      <small className="hint">A decision stays a draft until explicitly committed.</small>
    </form>
  )
}

function DecisionRow({
  actorId,
  projectId,
  decision,
  onDone,
}: {
  actorId: string
  projectId: string
  decision: DecisionRead
  onDone: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function commit() {
    setBusy(true)
    setError(null)
    const res = await projectApi(actorId).commitDecision(projectId, decision.id)
    setBusy(false)
    if (res.error || !res.data) {
      setError('Commit failed.')
      return
    }
    onDone()
  }

  return (
    <div className="decision-row">
      <div className="decision-head">
        <strong>{decision.title}</strong>
        <Badge tone={decision.status === DECISION_STATUS_COMMITTED ? 'good' : 'warn'}>
          {decision.status}
          {decision.superseded ? ' · superseded' : ''}
        </Badge>
      </div>
      <p>{decision.statement}</p>
      {decision.status === DECISION_STATUS_DRAFT ? (
        <div className="form-actions">
          <Button kind="primary" onClick={commit} disabled={busy}>
            {busy ? 'Committing…' : 'Commit decision'}
          </Button>
          {error ? <span className="inline-error">{error}</span> : null}
        </div>
      ) : null}
    </div>
  )
}

export function ObjectDetailView({
  actorId,
  projectId,
  detail,
  loading,
  error,
  onBack,
  onChanged,
}: {
  actorId: string
  projectId: string
  detail: ObjectDetailRead | null
  loading: boolean
  error: string | null
  onBack: () => void
  onChanged: () => void
}) {
  const [showEvidenceForm, setShowEvidenceForm] = useState(false)
  const [showDecisionForm, setShowDecisionForm] = useState(false)

  if (loading) return <Loading label="Loading object…" />
  if (error || !detail) return <ErrorBox message={error ?? 'Object not found.'} />

  const latest = detail.visible_revisions.at(-1)
  const relatedEvidence = detail.evidence as EvidenceRead[]
  const relatedDecisions = detail.decisions as DecisionRead[]

  return (
    <div className="view">
      <div className="view-header">
        <button className="quiet-button" onClick={onBack}>
          <ArrowLeft size={14} /> Objects
        </button>
        <div>
          <div className="eyebrow">{detail.series.object_type.toUpperCase()} · SCIENTIFIC OBJECT</div>
          <h1>{detail.series.name}</h1>
          {detail.series.description ? <p>{detail.series.description}</p> : null}
        </div>
      </div>

      <section className="content-section">
        <div className="section-heading">
          <h2>Identity</h2>
        </div>
        <dl className="identity-grid">
          <div>
            <dt>Series identity</dt>
            <dd className="mono">{detail.series.series_id}</dd>
          </div>
          <div>
            <dt>Latest revision identity</dt>
            <dd className="mono">{latest ? latest.revision_id : '—'}</dd>
          </div>
          <div>
            <dt>Series type</dt>
            <dd>{detail.series.object_type}</dd>
          </div>
          <div>
            <dt>Latest revision seq</dt>
            <dd>{detail.latest_revision_seq ?? '—'}</dd>
          </div>
        </dl>
        <p className="hint">Series identity and Revision identity are distinct; content is immutable once referenced.</p>
      </section>

      {latest ? (
        <Section title={`Revision #${latest.revision_seq} (visible)`}>
          <dl className="identity-grid">
            <div>
              <dt>Checksum</dt>
              <dd className="mono">{latest.checksum.slice(0, 16)}…</dd>
            </div>
            <div>
              <dt>Schema version</dt>
              <dd>{latest.schema_version}</dd>
            </div>
            <div>
              <dt>Payload</dt>
              <dd>
                <pre className="payload">{JSON.stringify(latest.payload, null, 2)}</pre>
              </dd>
            </div>
          </dl>
        </Section>
      ) : null}

      <Section title="Relations (graph lens)">
        <div className="trail-grid">
          <div>
            <h3>Inbound provenance</h3>
            {detail.provenance.inbound.length === 0 ? <Empty label="No inbound edges." /> : (
              detail.provenance.inbound.map((edge) => (
                <div className="trail-row" key={edge.edge_id}>
                  <GitBranch size={15} />
                  <span>{edge.relation_type}</span>
                  <strong>{edge.source_kind}</strong>
                  <small className="mono">{edge.source_id.slice(0, 8)}…</small>
                </div>
              ))
            )}
          </div>
          <div>
            <h3>Outbound provenance</h3>
            {detail.provenance.outbound.length === 0 ? <Empty label="No outbound edges." /> : (
              detail.provenance.outbound.map((edge) => (
                <div className="trail-row" key={edge.edge_id}>
                  <GitBranch size={15} />
                  <span>{edge.relation_type}</span>
                  <strong>{edge.target_kind}</strong>
                  <small className="mono">{edge.target_id.slice(0, 8)}…</small>
                </div>
              ))
            )}
          </div>
        </div>
      </Section>

      <Section
        title="Evidence"
        actions={
          <button className="quiet-button" onClick={() => setShowEvidenceForm((value) => !value)}>
            <Plus size={14} /> {showEvidenceForm ? 'Close' : 'Add evidence'}
          </button>
        }
      >
        {showEvidenceForm ? <EvidenceForm actorId={actorId} projectId={projectId} detail={detail} onDone={() => { setShowEvidenceForm(false); onChanged() }} /> : null}
        {relatedEvidence.length === 0 ? <Empty label="No evidence targets this object." /> : (
          relatedEvidence.map((item) => (
            <div className="trail-row" key={item.id}>
              <Boxes size={15} />
              <span>{item.kind} · {item.polarity}</span>
              <strong>{item.interpretation ?? item.label ?? 'evidence'}</strong>
              {item.frozen ? <Badge tone="warn">frozen</Badge> : null}
            </div>
          ))
        )}
      </Section>

      <Section
        title="Decisions"
        actions={
          <button className="quiet-button" onClick={() => setShowDecisionForm((value) => !value)}>
            <Plus size={14} /> {showDecisionForm ? 'Close' : 'Draft decision'}
          </button>
        }
      >
        {showDecisionForm ? <DecisionForm actorId={actorId} projectId={projectId} detail={detail} onDone={() => { setShowDecisionForm(false); onChanged() }} /> : null}
        {relatedDecisions.length === 0 ? <Empty label="No decisions cite this object." /> : (
          relatedDecisions.map((decision) => (
            <DecisionRow key={decision.id} actorId={actorId} projectId={projectId} decision={decision} onDone={onChanged} />
          ))
        )}
      </Section>

      <div className="hint">
        <FileText size={13} /> Committed decisions are immutable truth; drafts require an explicit commit.
      </div>
    </div>
  )
}
