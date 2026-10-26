import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Menu, X } from 'lucide-react'
import { Wordmark } from '../../components/ui/Wordmark'
import { ButtonLink } from '../../components/ui/Button'

const LINKS = [
  { href: '/#overview', label: 'Overview' },
  { href: '/#workflow', label: 'Workflow' },
  { href: '/#security', label: 'Security and accountability' },
]

export function SiteHeader() {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <header className="site-header">
      <div className="container site-header__bar">
        <Wordmark />
        <button type="button" className="icon-button site-header__toggle" aria-expanded={open} aria-controls="site-nav" aria-label={open ? 'Close menu' : 'Open menu'} onClick={() => setOpen((o) => !o)}>
          {open ? <X size={20} aria-hidden="true" /> : <Menu size={20} aria-hidden="true" />}
        </button>
        <nav id="site-nav" aria-label="Primary" className="site-header__nav" data-open={open}>
          <ul>
            {LINKS.map((l) => (
              <li key={l.href}>
                <a href={l.href} onClick={() => setOpen(false)}>
                  {l.label}
                </a>
              </li>
            ))}
          </ul>
          <div className="site-header__cta">
            <Link to="/request-access" className="site-header__text-link" onClick={() => setOpen(false)}>
              Request access
            </Link>
            <ButtonLink to="/sign-in" size="sm" onClick={() => setOpen(false)}>
              Sign in
            </ButtonLink>
          </div>
        </nav>
      </div>
    </header>
  )
}
