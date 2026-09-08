import type { ObjectDetailRead } from '../api/types'
import {
  DECISION_STATUS_COMMITTED,
  POLARITY_CONTRADICTS,
  POLARITY_SUPPORTS,
} from '../contracts/enums'
import { Empty } from './ui'

export function ContextInspector({ detail }: { detail: ObjectDetailRead | null }) {
  if (!detail) {
    return (
      <aside className="inspector" aria-label="Context inspector">
        <div className="inspector-header">CONTEXT INSPECTOR</div>
        <Empty label="Select a scientific object to inspect its context." />
      </aside>
    )
  }

  const governing = detail.decisions.find(
    (item) => item.status === DECISION_STATUS_COMMITTED && !item.superseded,
  )
  const supporting = detail.evidence.filter((item) => item.polarity === POLARITY_SUPPORTS)
  const contradicting = detail.evidence.filter((item) => item.polarity === POLARITY_CONTRADICTS)

  return (
    <aside className="inspector" aria-label="Context inspector">
      <div className="inspector-header">CONTEXT INSPECTOR</div>
      <div className="inspector-block">
        <span className="inspector-kicker">CURRENT OBJECT</span>
        <h2>{detail.series.name}</h2>
        <p>{detail.series.description ?? `A ${detail.series.object_type} scientific object in this project.`}</p>
      </div>
      {governing ? (
        <div className="inspector-block">
          <span className="inspector-kicker">GOVERNING DECISION</span>
          <h3>{governing.title}</h3>
          <p>{governing.statement}</p>
          <small className="mono">{governing.status}</small>
        </div>
      ) : (
        <div className="inspector-block">
          <span className="inspector-kicker">GOVERNING DECISION</span>
          <p>None yet.</p>
        </div>
      )}
      <div className="inspector-block">
        <span className="inspector-kicker">EVIDENCE</span>
        <strong>Supporting {supporting.length}</strong>
        <strong className="muted">Contradicting {contradicting.length}</strong>
        {supporting[0] ? <small>{supporting[0].interpretation ?? supporting[0].label}</small> : null}
      </div>
    </aside>
  )
}
