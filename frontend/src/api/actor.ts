import { api } from './client'

const STORAGE_KEY = 'revolab.actor_id'

/**
 * Phase-2 dev identity seam (Phase 1 left real authentication deferred): the
 * workspace ships a durable opaque Actor id over the `X-Actor-Id` header. The
 * backend already treats the Actor as an opaque UUID distinct from any auth
 * provider; OIDC/login remains a Phase 3+ concern and is not implemented here.
 *
 * Creation is single-flight: React StrictMode's double effect invocation (dev)
 * must not mint two actors or store an id different from the one the app is
 * holding, or a later reload would resolve a different Actor and lose the
 * Project it created.
 */
let creation: Promise<string> | null = null

export function resolveActor(): Promise<string> {
  const existing = window.localStorage.getItem(STORAGE_KEY)
  if (existing) return Promise.resolve(existing)

  creation ??= api
    .POST('/api/actors')
    .then(({ data, error }) => {
      if (error || !data) throw new Error('Could not create an actor identity.')
      window.localStorage.setItem(STORAGE_KEY, data.actor_id)
      return data.actor_id
    })
    .finally(() => {
      creation = null
    })

  return creation
}

export function clearActor(): void {
  window.localStorage.removeItem(STORAGE_KEY)
}
