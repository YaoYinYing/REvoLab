import { useCallback, useEffect, useRef, useState } from 'react'

export type AsyncState<T> = {
  data: T | null
  loading: boolean
  error: string | null
  reload: () => void
}

/**
 * Minimal data-loading hook. One concrete need only: fetch + reload + error
 * state for the workspace views. No global state container is introduced.
 */
export function useAsync<T>(fn: () => Promise<T>, deps: readonly unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)

  const reload = useCallback(() => setTick((value) => value + 1), [])
  const fnRef = useRef(fn)
  fnRef.current = fn

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fnRef.current()
      .then((value) => {
        if (cancelled) return
        setData(value)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Request failed.')
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // The caller's dependency array is spread intentionally, with the reload
    // tick appended as the refresh trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])

  return { data, loading, error, reload }
}
