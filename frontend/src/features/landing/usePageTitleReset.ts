import { useEffect } from 'react'

const TITLE = 'PACE — Approval workflow & compliance'

/** Page titles set by other routes are restored when the landing page is shown. */
export function usePageTitleReset() {
  useEffect(() => {
    document.title = TITLE
  }, [])
}
