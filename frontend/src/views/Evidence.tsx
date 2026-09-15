import { useEffect, useRef, useState } from 'react'
import { Boxes } from 'lucide-react'

import { useEvidence } from '../api/hooks'
import { Badge, Empty, ErrorBox, LoadMore, Loading } from '../components/ui'
import { POLARITY_CONTRADICTS } from '../contracts/enums'

const PAGE_SIZE = 50

export function EvidenceView({
  actorId,
  projectId,
  focusId = null,
}: {
  actorId: string
  projectId: string
  /** A search-hit target to select/highlight in this existing surface. */
  focusId?: string | null
}) {
  const [limit, setLimit] = useState(PAGE_SIZE)
  const { data, loading, error } = useEvidence(actorId, projectId, { limit })
  const hasMore = (data?.length ?? 0) === limit
  // Scroll the search-selected row into view without creating a parallel detail
  // surface: navigation reuses the canonical Evidence list.
  const focusRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (focusId) focusRef.current?.scrollIntoView({ block: 'center' })
  }, [focusId, data])

  return (
    <div className="view">
      <div className="view-header">
        <h1>Evidence</h1>
        <p>Interpreted claims scoped to this project. Source and target identity are immutable.</p>
      </div>
      <section className="content-section">
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
