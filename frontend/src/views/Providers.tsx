import { Zap } from 'lucide-react'

import { Badge } from '../components/ui'

// Phase-2 presentation-only capability surface. These are the Core-owned
// capability kinds named in the accepted architecture; credential binding,
// availability derivation and real drivers are Phase 3+ and intentionally
// absent from this workspace.
const CAPABILITIES = [
  { kind: 'COMPUTE', label: 'Compute', description: 'Submit and track external executions.' },
  { kind: 'SEARCH', label: 'Search', description: 'Look up literature and biological databases.' },
  { kind: 'ARTIFACT_RESOLUTION', label: 'Artifact resolution', description: 'Resolve a durable reference to bytes on demand.' },
  { kind: 'DESIGN', label: 'Design', description: 'Export interactive design results into objects.' },
  { kind: 'INTERACTIVE_HANDOFF', label: 'Interactive handoff', description: 'Deep-link an object into an external interactive tool.' },
]

export function ProvidersView() {
  return (
    <div className="view">
      <div className="view-header">
        <h1>Providers</h1>
        <p>
          Static capability surface. Credentials, secret material and derived availability belong to Phase 3 and are
          not implemented here.
        </p>
      </div>
      <section className="content-section">
        <div className="card-grid">
          {CAPABILITIES.map((capability) => (
            <div className="card" key={capability.kind}>
              <div className="card-head">
                <Zap size={15} />
                <strong>{capability.label}</strong>
              </div>
              <p>{capability.description}</p>
              <div>
                <Badge tone="warn">Phase 3</Badge>
                <span className="mono"> {capability.kind}</span>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
