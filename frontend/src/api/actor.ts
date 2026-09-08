import { api } from './client'

const STORAGE_KEY = 'revolab.actor_id'

/**
 * Phase-2 dev identity seam (Phase 1 left real authentication deferred): the
 * workspace ships a durable opaque Actor id over the `X-Actor-Id` header. The
 * backend already treats the Actor as an opaque UUID distinct from any auth
 * provider; OIDC/login remains a Phase 3+ concern and is not implemented here.
 */
export async function resolveActor(): Promise<string> {
  const existing = window.localStorage.getItem(STORAGE_KEY)
  if (existing) return existing

  const { data, error } = await api.POST('/api/actors')
  if (error || !data) {
    throw new Error('Could not create an actor identity.')
  }
  window.localStorage.setItem(STORAGE_KEY, data.actor_id)
  return data.actor_id
}

export function clearActor(): void {
  window.localStorage.removeItem(STORAGE_KEY)
}
