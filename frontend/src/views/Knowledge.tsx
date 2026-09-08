import { BookOpen } from 'lucide-react'

import { useDecisions } from '../api/hooks'
import { Badge, Empty, ErrorBox, Loading } from '../components/ui'
import { DECISION_STATUS_COMMITTED } from '../contracts/enums'

export function KnowledgeView({ actorId, projectId }: { actorId: string; projectId: string }) {
  const { data, loading, error } = useDecisions(actorId, projectId)
  const committed = (data ?? []).filter((decision) => decision.status === DECISION_STATUS_COMMITTED)

  return (
    <div className="view">
      <div className="view-header">
        <h1>Knowledge</h1>
        <p>Promoted project truth: only committed decisions surface here. The promotion gate is the explicit commit.</p>
      </div>
      <section className="content-section">
        {loading ? <Loading /> : null}
        <ErrorBox message={error} />
        {committed.length === 0 ? <Empty label="No committed decisions yet." /> : null}
        <div className="list">
          {committed.map((item) => (
            <div className="list-row" key={item.id}>
              <div className="list-row-head">
                <BookOpen size={15} />
                <strong>{item.title}</strong>
                <Badge tone="good">committed</Badge>
                {item.superseded ? <Badge tone="warn">superseded</Badge> : null}
              </div>
              <p>{item.statement}</p>
              {item.next_actions.length > 0 ? (
                <div className="next-actions">
                  {item.next_actions.map((action) => (
                    <span key={action}>↳ {action}</span>
                  ))}
                </div>
              ) : null}
              <small className="mono">
                {item.cites.length} cited evidence · {item.selects.length} selected targets
                {item.committed_at ? ` · committed ${item.committed_at.slice(0, 10)}` : ''}
              </small>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
