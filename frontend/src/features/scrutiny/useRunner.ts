import { useCallback, useState } from 'react'
import { ApiError } from '../../lib/api'
import type { CaseDetail } from './types'

/**
 * Runs one workflow action. On success the server's refreshed case replaces the screen's copy; on failure the server's own message is
 * kept (never replaced by a vaguer one). A stale-version answer reloads the case so the person sees what changed.
 */
export function useRunner(onResult: (d: CaseDetail) => void, onStale: () => void) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const run = useCallback(
    async (fn: () => Promise<CaseDetail>): Promise<boolean> => {
      setBusy(true)
      try {
        onResult(await fn())
        setError(null)
        return true
      } catch (e) {
        const err = e instanceof ApiError ? e : new ApiError(0, 'Something unexpected went wrong. Nothing was changed.')
        setError(err)
        if (err.code === 'stale_version' || err.code === 'invalid_transition') onStale()
        return false
      } finally {
        setBusy(false)
      }
    },
    [onResult, onStale],
  )
  return { busy, error, run, clear: useCallback(() => setError(null), []) }
}

/** Field-level messages from a failed save, keyed by the server's path. */
export function issuesByPath(error: ApiError | null): Record<string, string> {
  const out: Record<string, string> = {}
  for (const i of error?.issues ?? []) if (i.path) out[i.path] ??= i.message
  return out
}
