import type { ReactNode } from 'react'

export type PillTone = 'verified' | 'active' | 'pending' | 'warning' | 'overdue' | 'proposed' | 'neutral'

export function StatusPill({ tone, children }: { tone: PillTone; children: ReactNode }) {
  return (
    <span className="pill" data-tone={tone}>
      <i aria-hidden="true" />
      {children}
    </span>
  )
}
