import { useCallback, useEffect, useState } from 'react'
import { ApiError } from './api'

export type Resource<T> =
  | { status: 'loading' }
  | { status: 'error'; message: string; forbidden: boolean }
  | { status: 'ready'; data: T }

/** Loads data on mount / when `deps` change. `reload` refetches without blanking the screen. */
export function useResource<T>(load: () => Promise<T>, deps: readonly unknown[]): [Resource<T>, () => void] {
  const [state, setState] = useState<Resource<T>>({ status: 'loading' })
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let cancelled = false
    load()
      .then((data) => !cancelled && setState({ status: 'ready', data }))
      .catch((e: unknown) => {
        if (cancelled) return
        const api = e instanceof ApiError ? e : null
        setState({ status: 'error', message: api?.message ?? 'Unexpected error.', forbidden: api?.status === 403 })
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps])

  const reload = useCallback(() => setTick((n) => n + 1), [])
  return [state, reload]
}
