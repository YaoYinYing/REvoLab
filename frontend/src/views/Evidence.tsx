import { Boxes } from 'lucide-react'

import { useEvidence } from '../api/hooks'
import { Badge, Empty, ErrorBox, Loading } from '../components/ui'
import { POLARITY_CONTRADICTS } from '../contracts/enums'

export function EvidenceView({ actorId, projectId }: { actorId: string; projectId: string }) {
  const { data, loading, error } = useEvidence(actorId, projectId)

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
            <div className="list-row" key={item.id}>
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
      </section>
    </div>
  )
}
