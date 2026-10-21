import type { ReactNode } from 'react'

interface DemoControlsProps {
  label: string
  children: ReactNode
  /** When set, shows a control to hand control back to scrolling. */
  following?: boolean
  onFollow?: () => void
  /** Screen-reader announcement of the current demo state. */
  status?: string
}

export function DemoControls({ label, children, following, onFollow, status }: DemoControlsProps) {
  return (
    <div className="controls" role="group" aria-label={label}>
      <span className="controls__tag">Demo control</span>
      {children}
      {following === false && onFollow ? (
        <button type="button" className="ctl ctl--quiet" onClick={onFollow}>
          Follow scroll
        </button>
      ) : null}
      {following ? <span className="controls__hint">Following your scroll</span> : null}
      <span className="sr-only" role="status" aria-live="polite">{status}</span>
    </div>
  )
}

interface CtlProps {
  onClick: () => void
  children: ReactNode
  disabled?: boolean
  primary?: boolean
  pressed?: boolean
}

export function Ctl({ onClick, children, disabled, primary, pressed }: CtlProps) {
  return (
    <button
      type="button"
      className="ctl"
      data-primary={primary || undefined}
      aria-pressed={pressed}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  )
}
