import { Zap } from 'lucide-react'

import { useProviders } from '../api/hooks'
import type { CapabilityAvailability, CapabilityKind, ProviderRuntimeHealth } from '../api/types'
import { Badge, Empty, ErrorBox, Loading } from '../components/ui'
import { CAPABILITY_AVAILABILITIES, CAPABILITY_KINDS, PROVIDER_RUNTIME_HEALTHS } from '../contracts/enums.generated'

type Tone = 'neutral' | 'good' | 'warn'

// Build maps keyed by the GENERATED closed-vocabulary arrays, so the frontend
// never re-declares the value lists; only presentation rules are expressed here
// (single source of truth for the values stays in OpenAPI).
function complete<T extends string, V>(values: readonly T[], valueOf: (value: T) => V): Record<T, V> {
  const map = {} as Record<T, V>
  for (const value of values) map[value] = valueOf(value)
  return map
}

function humanize(value: string): string {
  return value
    .split('_')
    .map((word) => (word ? word[0]!.toUpperCase() + word.slice(1) : word))
    .join(' ')
}

const CAPABILITY_LABEL = complete<CapabilityKind, string>(CAPABILITY_KINDS, humanize)
const HEALTH_TONE = complete<ProviderRuntimeHealth, Tone>(PROVIDER_RUNTIME_HEALTHS, (value) =>
  value === 'ready' ? 'good' : 'warn',
)
const AVAILABILITY_TONE = complete<CapabilityAvailability, Tone>(CAPABILITY_AVAILABILITIES, (value) =>
  value === 'available' ? 'good' : value === 'not_authorized' ? 'neutral' : 'warn',
)

export function ProvidersView({ actorId, projectId }: { actorId: string; projectId: string }) {
  const providers = useProviders(actorId, projectId)
  const loaded = !providers.loading && !providers.error

  return (
    <div className="view">
      <div className="view-header">
        <h1>Providers</h1>
        <p>
          External capability providers visible to this project, projected through your identity.
          Availability is derived per query and per capability from provider runtime state, your
          credentials, and project access — it is never stored.
        </p>
      </div>

      {providers.loading ? <Loading label="Loading providers…" /> : null}
      {providers.error ? <ErrorBox message={providers.error} /> : null}
      {loaded && providers.data?.length === 0 ? (
        <Empty label="No providers configured for this project." />
      ) : null}

      {loaded && providers.data?.length ? (
        <section className="provider-register" aria-label="Provider catalog">
          {providers.data.map((provider) => (
            <article className="provider-row" key={provider.key}>
              <div className="provider-row-head">
                <div className="provider-identity">
                  <Zap size={15} />
                  <strong>{provider.name}</strong>
                  <span className="mono">{provider.key}</span>
                </div>
                <Badge tone={HEALTH_TONE[provider.health]}>{provider.health}</Badge>
              </div>

              <p className="provider-description">{provider.description}</p>

              <div className="provider-facts">
                <div className="provider-fact">
                  <span className="eyebrow">Capabilities · your availability</span>
                  <div className="capability-list">
                    {(provider.capabilities ?? []).map((capability) => (
                      <div className="capability-entry" key={capability.kind}>
                        <span>{CAPABILITY_LABEL[capability.kind]}</span>
                        <Badge tone={AVAILABILITY_TONE[capability.availability]}>
                          {capability.availability}
                        </Badge>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="provider-fact">
                  <span className="eyebrow">Required credentials · yours</span>
                  <div className="chip-row">
                    {(provider.required_credential_kinds ?? []).map((kind) => {
                      const status = (provider.credential_presence ?? []).find(
                        (entry) => entry.kind === kind,
                      )
                      return (
                        <Badge key={kind} tone={status?.present ? 'good' : 'warn'}>
                          {kind}: {status?.present ? 'present' : 'missing'}
                        </Badge>
                      )
                    })}
                  </div>
                </div>
              </div>
            </article>
          ))}
        </section>
      ) : null}
    </div>
  )
}
