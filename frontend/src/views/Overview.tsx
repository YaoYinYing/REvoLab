import { Boxes, FileText, FlaskConical, ShieldCheck } from 'lucide-react'

import { useDecisions, useEvidence, useObjects } from '../api/hooks'
import { Badge, Empty, ErrorBox, Loading } from '../components/ui'
import {
  DECISION_STATUS_COMMITTED,
  DECISION_STATUS_DRAFT,
  POLARITY_CONTRADICTS,
} from '../contracts/enums'

export function OverviewView({ actorId, projectId }: { actorId: string; projectId: string }) {
  const objects = useObjects(actorId, projectId)
  const evidence = useEvidence(actorId, projectId)
  const decisions = useDecisions(actorId, projectId)

  const loading = objects.loading || evidence.loading || decisions.loading
  const error = objects.error ?? evidence.error ?? decisions.error
  const drafts = (decisions.data ?? []).filter((item) => item.status === DECISION_STATUS_DRAFT)
  const committed = (decisions.data ?? []).filter((item) => item.status === DECISION_STATUS_COMMITTED)
  const recentEvidence = (evidence.data ?? []).slice(0, 5)

  return (
    <div className="view">
      <div className="view-header">
        <h1>Overview</h1>
        <p>Recent scientific state of this project and the next thing to do. Use the collection pages for complete lists.</p>
      </div>

      {loading ? <Loading /> : null}
      <ErrorBox message={error} />

      <section className="metric-grid">
        <div className="metric">
          <FlaskConical size={16} />
          <strong>{objects.data?.length ?? 0}</strong>
          <span>objects</span>
        </div>
        <div className="metric">
          <Boxes size={16} />
          <strong>{evidence.data?.length ?? 0}</strong>
          <span>evidence</span>
        </div>
        <div className="metric">
          <FileText size={16} />
          <strong>{drafts.length}</strong>
          <span>open decisions</span>
        </div>
        <div className="metric">
          <ShieldCheck size={16} />
          <strong>{committed.length}</strong>
          <span>committed</span>
        </div>
      </section>

      <section className="content-section">
        <div className="section-heading">
          <h2>Open decisions</h2>
        </div>
        {drafts.length === 0 ? <Empty label="No open decision drafts." /> : (
          drafts.map((item) => (
            <div className="list-row" key={item.id}>
              <div className="list-row-head">
                <strong>{item.title}</strong>
                <Badge tone="warn">draft</Badge>
              </div>
              <p>{item.statement}</p>
            </div>
          ))
        )}
      </section>

      <section className="content-section">
        <div className="section-heading">
          <h2>Recent evidence</h2>
        </div>
        {recentEvidence.length === 0 ? <Empty label="No evidence yet." /> : (
          recentEvidence.map((item) => (
            <div className="list-row" key={item.id}>
              <div className="list-row-head">
                <strong>{item.label ?? item.interpretation ?? 'evidence'}</strong>
                <Badge>{item.kind}</Badge>
                <Badge tone={item.polarity === POLARITY_CONTRADICTS ? 'warn' : 'neutral'}>{item.polarity}</Badge>
              </div>
            </div>
          ))
        )}
      </section>

      <section className="content-section">
        <div className="section-heading">
          <h2>Committed truth</h2>
        </div>
        {committed.length === 0 ? <Empty label="No committed decisions yet." /> : (
          committed.map((item) => (
            <div className="list-row" key={item.id}>
              <div className="list-row-head">
                <strong>{item.title}</strong>
                <Badge tone="good">committed</Badge>
                {item.superseded ? <Badge tone="warn">superseded</Badge> : null}
              </div>
              <p>{item.statement}</p>
            </div>
          ))
        )}
      </section>
    </div>
  )
}
