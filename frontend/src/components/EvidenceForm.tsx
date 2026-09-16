import { useState } from 'react'

import { projectApi } from '../api/backend'
import type {
  EvidenceKind,
  EvidenceRead,
  EvidenceRole,
  EvidenceTargetKind,
  Polarity,
  ResourceKind,
} from '../api/types'
import { Button } from './buttons'
import { EnumSelect, Field } from './ui'
import {
  CONFIDENCES,
  DEFAULT_EVIDENCE_KIND,
  DEFAULT_EVIDENCE_ROLE,
  DEFAULT_EVIDENCE_TARGET_KIND,
  DEFAULT_POLARITY,
  EVIDENCE_KINDS,
  EVIDENCE_ROLES,
  POLARITIES,
} from '../contracts/enums'

/**
 * One canonical Evidence-creation form, reused by EVERY surface that creates
 * Evidence (the object-detail surface and the literature "Use as Evidence"
 * hand-off). There is deliberately no second Evidence form: both go through the
 * same canonical `POST /evidence` operation.
 *
 * A target is required by the canonical domain model (Evidence is an
 * INTERPRETATION of a publication/run/observation about a Project target), so the
 * form either receives a fixed target or offers a bounded target picker.
 * `sourceKind`/`sourceId` are an optional provenance pin (e.g. an imported
 * LiteratureReference); the browser never supplies a title or any other
 * bibliographic metadata.
 */
export type EvidenceTargetOption = {
  /** The canonical target kind (backend-owned enum value). */
  kind: EvidenceTargetKind
  /** The canonical REvoLab identity of the target. */
  id: string
  /** Presentation-only description. */
  label: string
}

export function EvidenceForm({
  actorId,
  projectId,
  targetKind = null,
  targetId = null,
  targetLabel = null,
  targetOptions = null,
  sourceKind = null,
  sourceId = null,
  sourceLabel = null,
  defaultKind = DEFAULT_EVIDENCE_KIND,
  interpretationPlaceholder = 'What does this evidence say about the object?',
  onCreated,
  onCancel,
}: {
  actorId: string
  projectId: string
  targetKind?: EvidenceTargetKind | null
  targetId?: string | null
  targetLabel?: string | null
  targetOptions?: EvidenceTargetOption[] | null
  sourceKind?: ResourceKind | null
  sourceId?: string | null
  sourceLabel?: string | null
  defaultKind?: EvidenceKind
  /** Presentation-only placeholder; the stored value is unchanged. */
  interpretationPlaceholder?: string
  onCreated: (evidence: EvidenceRead) => void
  onCancel?: () => void
}) {
  const fixed = targetKind !== null && targetId !== null
  const [kind, setKind] = useState<EvidenceKind>(defaultKind)
  const [role, setRole] = useState<EvidenceRole>(DEFAULT_EVIDENCE_ROLE)
  const [interpretation, setInterpretation] = useState('')
  const [polarity, setPolarity] = useState<Polarity>(DEFAULT_POLARITY)
  // '' means "unset": `confidence` is an optional wire field.
  const [confidence, setConfidence] = useState<string>('')
  const [scope, setScope] = useState('')
  const [selected, setSelected] = useState<string>(
    targetOptions && targetOptions.length > 0
      ? `${targetOptions[0].kind}:${targetOptions[0].id}`
      : '',
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const options = targetOptions ?? []
  const chosen = fixed
    ? { kind: targetKind as EvidenceTargetKind, id: targetId as string }
    : (() => {
        const match = options.find((option) => `${option.kind}:${option.id}` === selected)
        return match ? { kind: match.kind, id: match.id } : null
      })()

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    if (!chosen) {
      setError('Choose which project target this evidence is about.')
      return
    }
    setBusy(true)
    const res = await projectApi(actorId).createEvidence(projectId, {
      kind,
      role,
      interpretation: interpretation || null,
      polarity,
      confidence: confidence ? (confidence as (typeof CONFIDENCES)[number]) : null,
      scope: scope || null,
      source_kind: sourceKind,
      source_id: sourceId,
      target_kind: chosen.kind,
      target_id: chosen.id,
    })
    setBusy(false)
    if (res.error || !res.data) {
      setError('Evidence creation failed.')
      return
    }
    setInterpretation('')
    onCreated(res.data as EvidenceRead)
  }

  return (
    <form className="stack-form compact" onSubmit={onSubmit}>
      <div className="form-grid">
        <Field label="Kind">
          <EnumSelect value={kind} options={EVIDENCE_KINDS} onChange={setKind} />
        </Field>
        <Field label="Role">
          <EnumSelect value={role} options={EVIDENCE_ROLES} onChange={setRole} />
        </Field>
        <Field label="Polarity">
          <EnumSelect value={polarity} options={POLARITIES} onChange={setPolarity} />
        </Field>
        <Field label="Confidence">
          <select value={confidence} onChange={(event) => setConfidence(event.target.value)}>
            <option value="">unset</option>
            {CONFIDENCES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Field label="Interpretation">
        <input
          value={interpretation}
          onChange={(event) => setInterpretation(event.target.value)}
          placeholder={interpretationPlaceholder}
        />
      </Field>
      <Field label="Scope">
        <input
          value={scope}
          onChange={(event) => setScope(event.target.value)}
          placeholder="Optional scope (e.g. this construct, in vitro)"
        />
      </Field>
      {fixed ? null : (
        <Field label="About project target">
          {options.length === 0 ? (
            <small className="hint">
              This project has no scientific object or decision to attach evidence to yet.
            </small>
          ) : (
            <select value={selected} onChange={(event) => setSelected(event.target.value)}>
              {options.map((option) => (
                <option key={`${option.kind}:${option.id}`} value={`${option.kind}:${option.id}`}>
                  {option.label}
                </option>
              ))}
            </select>
          )}
        </Field>
      )}
      <div className="form-actions">
        <Button type="submit" disabled={busy || (!fixed && options.length === 0)}>
          {busy ? 'Recording…' : 'Record evidence'}
        </Button>
        {onCancel ? (
          <Button type="button" kind="quiet" onClick={onCancel}>
            Cancel
          </Button>
        ) : null}
        {error ? <span className="inline-error">{error}</span> : null}
      </div>
      <small className="hint">
        {sourceKind && sourceId
          ? `Source: ${sourceLabel ?? sourceKind} (${sourceId.slice(0, 8)}…)`
          : 'Source: none'}
        {fixed && targetId ? ` · Target: ${targetLabel ?? targetId.slice(0, 8) + '…'}` : ''}
      </small>
    </form>
  )
}
