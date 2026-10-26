import { useEffect, useRef } from 'react'

/** Calls `tick` every `ms` while `active`, pausing when the tab is hidden. Stops by itself when `active` turns false. */
export function usePolling(tick: () => void | Promise<void>, active: boolean, ms = 3000) {
  const latest = useRef(tick)
  latest.current = tick
  useEffect(() => {
    if (!active) return
    const id = window.setInterval(() => {
      if (!document.hidden) void latest.current()
    }, ms)
    return () => window.clearInterval(id)
  }, [active, ms])
}
