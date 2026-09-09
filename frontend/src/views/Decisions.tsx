import { useState } from 'react'
import { FileText } from 'lucide-react'

import { projectApi } from '../api/backend'
import { useDecisions } from '../api/hooks'
import type { DecisionRead } from '../api/types'
import { Button } from '../components/buttons'
import { Badge, Empty, ErrorBox, LoadMore, Loading } from '../components/ui'
import { DECISION_STATUS_COMMITTED, DECISION_STATUS_DRAFT } from '../contracts/enums'

const PAGE_SIZE = 50

export function DecisionsView({ actorId, projectId }: { actorId: string; projectId: string }) {
  const [limit, setLimit] = useState(PAGE_SIZE)
  const { data, loading, error, reload } = useDecisions(actorId, projectId, { limit })
  const hasMore = (data?.length ?? 0) === limit
  const [busyId, setBusyId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  async function commit(decision: DecisionRead) {
    setBusyId(decision.id)
    setActionError(null)
    const res = await projectApi(actorId).commitDecision(projectId, decision.id)
    setBusyId(null)
    if (res.error || !res.data) {
      setActionError('Commit failed.')
      return
    }
    reload()
  }

  return (
    <div className="view">
      <div className="view-header">
        <h1>Decisions</h1>
        <p>The project decision log. Drafts must be explicitly committed; committed decisions are immutable.</p>
      </div>
      <section className="content-section">
        {loading ? <Loading /> : null}
        <ErrorBox message={error ?? actionError} />
        {data && data.length === 0 ? <Empty label="No decisions in this project yet." /> : null}
        <div className="list">
          {data?.map((item) => (
            <div className="list-row" key={item.id}>
              <div className="list-row-head">
                <FileText size={15} />
                <strong>{item.title}</strong>
                <Badge tone={item.status === DECISION_STATUS_COMMITTED ? 'good' : 'warn'}>
                  {item.status}
                  {item.superseded ? ' · superseded' : ''}
                </Badge>
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
                cites {item.cites.length} · selects {item.selects.length}
                {item.superseded_by ? ` · superseded by ${item.superseded_by.slice(0, 8)}…` : ''}
              </small>
              {item.status === DECISION_STATUS_DRAFT ? (
                <div className="form-actions">
                  <Button onClick={() => commit(item)} disabled={busyId === item.id}>
                    {busyId === item.id ? 'Committing…' : 'Commit'}
                  </Button>
                </div>
              ) : null}
            </div>
          ))}
        </div>
        <LoadMore visible={hasMore} onLoad={() => setLimit((value) => value + PAGE_SIZE)} />
      </section>
    </div>
  )
}
