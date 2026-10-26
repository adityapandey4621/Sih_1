import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { Wordmark } from '../../components/ui/Wordmark'
import './auth.css'

interface Props {
  title: string
  intro?: ReactNode
  children: ReactNode
  footer?: ReactNode
}

export function AuthLayout({ title, intro, children, footer }: Props) {
  return (
    <div className="auth">
      <aside className="auth__aside" aria-hidden="false">
        <Wordmark inverse />
        <div className="auth__aside-body">
          <p className="auth__aside-title display">Every application. Every action. Accounted for.</p>
          <p className="auth__aside-note">
            Accounts are issued by an administrator. Institutions request access; official roles are provisioned, never self-selected.
          </p>
        </div>
        <svg className="auth__route" viewBox="0 0 320 24" aria-hidden="true">
          <line x1="8" y1="12" x2="312" y2="12" stroke="currentColor" strokeWidth="1" strokeDasharray="2 5" />
          {[8, 84, 160, 236, 312].map((x) => (
            <circle key={x} cx={x} cy="12" r="4" fill="none" stroke="currentColor" strokeWidth="1.2" />
          ))}
        </svg>
      </aside>

      <main className="auth__main" id="main">
        <div className="auth__panel">
          <Link to="/" className="auth__back">
            <ArrowLeft size={16} aria-hidden="true" /> Back to overview
          </Link>
          <h1 className="auth__title display">{title}</h1>
          {intro && <div className="auth__intro">{intro}</div>}
          {children}
          {footer && <div className="auth__footer">{footer}</div>}
        </div>
      </main>
    </div>
  )
}
