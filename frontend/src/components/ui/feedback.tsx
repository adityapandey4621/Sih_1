import type { ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, Clock3, Info, Loader2, Lock, XCircle, type LucideIcon } from 'lucide-react'
import { ButtonLink, Button } from './Button'

export type Tone = 'info' | 'success' | 'warning' | 'error' | 'neutral'

const ICONS: Record<Tone, LucideIcon> = {
  info: Info,
  success: CheckCircle2,
  warning: Clock3,
  error: XCircle,
  neutral: Info,
}

export function Alert({ tone = 'info', title, children }: { tone?: Tone; title?: string; children: ReactNode }) {
  const Icon = ICONS[tone]
  return (
    <div className={`alert alert--${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <Icon size={18} aria-hidden="true" className="alert__icon" />
      <div>
        {title && <p className="alert__title">{title}</p>}
        <div className="alert__body">{children}</div>
      </div>
    </div>
  )
}

/** Status marker: colour is always accompanied by an icon and a text label. */
export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  const Icon = ICONS[tone]
  return (
    <span className={`badge badge--${tone}`}>
      <Icon size={12} aria-hidden="true" />
      {children}
    </span>
  )
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="state state--empty">
      <p className="state__title">{title}</p>
      {children && <div className="state__body">{children}</div>}
      {action && <div className="state__action">{action}</div>}
    </div>
  )
}

export function ErrorState({ title = 'Something went wrong', message, onRetry }: { title?: string; message: string; onRetry?: () => void }) {
  return (
    <div className="state state--error" role="alert">
      <AlertTriangle size={20} aria-hidden="true" />
      <p className="state__title">{title}</p>
      <p className="state__body">{message}</p>
      {onRetry && (
        <div className="state__action">
          <Button variant="secondary" size="sm" onClick={onRetry}>
            Try again
          </Button>
        </div>
      )}
    </div>
  )
}

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="state state--loading" role="status" aria-live="polite">
      <Loader2 size={20} className="spin" aria-hidden="true" />
      <p>{label}</p>
    </div>
  )
}

export function AccessDenied({ message }: { message?: string }) {
  return (
    <div className="state state--denied" role="alert">
      <Lock size={20} aria-hidden="true" />
      <p className="state__title">You do not have access to this page</p>
      <p className="state__body">{message ?? 'Your account is not permitted to view this area. If you believe this is a mistake, contact your platform administrator.'}</p>
      <div className="state__action">
        <ButtonLink to="/app" variant="secondary" size="sm">
          Back to my dashboard
        </ButtonLink>
      </div>
    </div>
  )
}
