import { useEffect, useMemo, useRef, useState } from 'react'
import { Boxes, Plus } from 'lucide-react'

import type { EvidenceRead, EvidenceTargetKind, ResourceKind } from '../api/types'
import { useDecisions, useEvidence, useMyMembership, useObjects } from '../api/hooks'
import { Button } from '../components/buttons'
import { EvidenceForm, type EvidenceTargetOption } from '../components/EvidenceForm'
import { Badge, Empty, ErrorBox, LoadMore, Loading } from '../components/ui'
import { POLARITY_CONTRADICTS, ROLE_MEMBER, ROLE_OWNER } from '../contracts/enums'

const PAGE_SIZE = 50

/** The explicit hand-off from another canonical surface (e.g. an imported
 * LiteratureReference) into this EXISTING Evidence creation surface. */
export type EvidenceSourcePrefill = {
  source_kind: ResourceKind
  source_id: string
  label: string | null
}

export function EvidenceView({
  actorId,
  projectId,
  focusId = null,
  evidenceSource = null,
  onEvidenceSourceConsumed,
}: {
  actorId: string
  projectId: string
  /** A search-hit target to select/highlight in this existing surface. */
  focusId?: string | null
  /** Prefill handed off from another surface (literature "Use as Evidence"). */
  evidenceSource?: EvidenceSourcePrefill | null
  onEvidenceSourceConsumed?: () => void
}) {
  const [limit, setLimit] = useState(PAGE_SIZE)
  const { data, loading, error, reload } = useEvidence(actorId, projectId, { limit })
  const membership = useMyMembership(actorId, projectId)
  const objects = useObjects(actorId, projectId, { limit: 100 })
  const decisions = useDecisions(actorId, projectId, { limit: 100 })
  const [showForm, setShowForm] = useState(false)
  const hasMore = (data?.length ?? 0) === limit
  // Scroll the search-selected row into view without creating a parallel detail
  // surface: navigation reuses the canonical Evidence list.
  const focusRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (focusId) focusRef.current?.scrollIntoView({ block: 'center' })
  }, [focusId, data])

  // A hand-off opens the form immediately; the prefill is cleared by the parent
  // once consumed so it never silently reapplies on a later visit.
  useEffect(() => {
    if (evidenceSource) setShowForm(true)
  }, [evidenceSource])

  const targetOptions = useMemo<EvidenceTargetOption[]>(() => {
    const options: EvidenceTargetOption[] = []
    for (const object of objects.data ?? []) {
      if (!object.latest_revision) continue
      options.push({
        kind: 'scientific_object_revision' as EvidenceTargetKind,
        id: object.latest_revision.revision_id,
        label: `${object.name} · revision ${object.latest_revision.revision_seq}`,
      })
    }
    for (const decision of decisions.data ?? []) {
      options.push({
        kind: 'decision' as EvidenceTargetKind,
        id: decision.id,
        label: `Decision: ${decision.title} (${decision.status})`,
      })
    }
    return options
  }, [objects.data, decisions.data])

  const canMutate = membership.data?.role === ROLE_OWNER || membership.data?.role === ROLE_MEMBER
  const sourceKind = evidenceSource ? evidenceSource.source_kind : null

  function finish(evidence: EvidenceRead) {
    reload()
    if (evidenceSource && onEvidenceSourceConsumed) onEvidenceSourceConsumed()
    setShowForm(false)
  }

  return (
    <div className="view">
      <div className="view-header">
        <h1>Evidence</h1>
        <p>Interpreted claims scoped to this project. Source and target identity are immutable.</p>
      </div>
      <section className="content-section">
        {canMutate ? (
          <Button type="button" kind="quiet" onClick={() => setShowForm((value) => !value)}>
            <Plus size={14} /> {showForm ? 'Close' : 'Add evidence'}
          </Button>
        ) : null}
        {showForm && canMutate ? (
          <EvidenceForm
            actorId={actorId}
            projectId={projectId}
            targetOptions={targetOptions}
            sourceKind={sourceKind}
            sourceId={evidenceSource?.source_id ?? null}
            sourceLabel={evidenceSource?.label ?? null}
            interpretationPlaceholder="What does this publication say about the project?"
            onCreated={finish}
            onCancel={() => {
              setShowForm(false)
              if (onEvidenceSourceConsumed) onEvidenceSourceConsumed()
            }}
          />
        ) : null}
        {loading ? <Loading /> : null}
        <ErrorBox message={error} />
        {data && data.length === 0 ? <Empty label="No evidence in this project yet." /> : null}
        <div className="list">
          {data?.map((item) => (
            <div
              className={`list-row${item.id === focusId ? ' focused' : ''}`}
              key={item.id}
              ref={item.id === focusId ? focusRef : undefined}
            >
              <div className="list-row-head">
                <Boxes size={15} />
                <strong>{item.label ?? item.interpretation ?? 'evidence'}</strong>
                <Badge>{item.kind}</Badge>
                <Badge tone={item.polarity === POLARITY_CONTRADICTS ? 'warn' : 'neutral'}>{item.polarity}</Badge>
                {item.frozen ? <Badge tone="warn">frozen</Badge> : null}
              </div>
              {item.interpretation ? <p>{item.interpretation}</p> : null}
              <small className="mono">
                target {item.target_kind}: {item.target_id.slice(0, 8)}…
                {item.source_id ? ` · source ${item.source_kind}: ${item.source_id.slice(0, 8)}…` : ' · source: none'}
              </small>
            </div>
          ))}
        </div>
        <LoadMore visible={hasMore} onLoad={() => setLimit((value) => value + PAGE_SIZE)} />
      </section>
    </div>
  )
}
