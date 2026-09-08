import { useMemo, useState } from 'react'
import { Database } from 'lucide-react'

import { useResources } from '../api/hooks'
import type { ResourceKind } from '../api/types'
import { Badge, Empty, ErrorBox, LoadMore, Loading } from '../components/ui'

const PAGE_SIZE = 50

// Presentation-only labels for backend-owned resource_kind values. The kind
// VALUES themselves come from the live reference data, never from this map.
const KIND_LABELS: Record<string, string> = {
  run_reference: 'Runs',
  artifact_reference: 'Artifacts',
  session_reference: 'Sessions',
  literature_reference: 'Literature',
  external_reference: 'External',
}

export function RunsAndArtifactsView({ actorId, projectId }: { actorId: string; projectId: string }) {
  const [kind, setKind] = useState<ResourceKind | ''>('')
  const [limit, setLimit] = useState(PAGE_SIZE)
  const { data, loading, error } = useResources(actorId, projectId, kind || null, { limit })
  const hasMore = (data?.length ?? 0) === limit

  const kinds = useMemo(
    () => Array.from(new Set((data ?? []).map((item) => item.resource_kind))).sort() as ResourceKind[],
    [data],
  )
  const visible = kind ? (data ?? []).filter((item) => item.resource_kind === kind) : (data ?? [])

  return (
    <div className="view">
      <div className="view-header">
        <h1>Runs &amp; Artifacts</h1>
        <p>Durable reference identity cards visible through this project. Execution truth stays with the owning provider.</p>
      </div>
      <section className="content-section">
        <div className="section-heading">
          <h2>References</h2>
          <div className="segmented">
            <button className={kind === '' ? 'segmented-active' : ''} onClick={() => setKind('')}>
              All references
            </button>
            {kinds.map((value) => (
              <button
                key={value}
                className={kind === value ? 'segmented-active' : ''}
                onClick={() => setKind(value)}
              >
                {KIND_LABELS[value] ?? value}
              </button>
            ))}
          </div>
        </div>
        {loading ? <Loading /> : null}
        <ErrorBox message={error} />
        {visible.length === 0 ? <Empty label="No references of this kind visible through this project." /> : null}
        <div className="list">
          {visible.map((item) => (
            <div className="list-row" key={item.resource_id}>
              <div className="list-row-head">
                <Database size={15} />
                <strong>{item.native_id}</strong>
                <Badge>{item.resource_kind}</Badge>
                {item.revoked_at ? <Badge tone="warn">revoked</Badge> : null}
              </div>
              <small className="mono">
                authority {item.authority}
                {item.checksum ? ` · checksum ${item.checksum.slice(0, 12)}…` : ''}
                {item.version_id ? ` · version ${item.version_id}` : ''}
                {item.task_type ? ` · task ${item.task_type}` : ''}
                {item.title ? ` · ${item.title}` : ''}
              </small>
            </div>
          ))}
        </div>
        <LoadMore visible={hasMore} onLoad={() => setLimit((value) => value + PAGE_SIZE)} />
      </section>
    </div>
  )
}
