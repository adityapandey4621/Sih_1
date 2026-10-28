import { useEffect, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'

export interface Crumb {
  label: string
  to?: string
}

export function usePageTitle(title: string) {
  useEffect(() => {
    const previous = document.title
    document.title = `${title} — PACE`
    return () => {
      document.title = previous
    }
  }, [title])
}

interface Props {
  title: string
  description?: ReactNode
  crumbs?: Crumb[]
  actions?: ReactNode
}

export function PageHeader({ title, description, crumbs, actions }: Props) {
  usePageTitle(title)
  return (
    <header className="page-header">
      {crumbs && crumbs.length > 0 && (
        <nav aria-label="Breadcrumb" className="crumbs">
          <ol>
            {crumbs.map((c, i) => (
              <li key={c.label}>
                {c.to && i < crumbs.length - 1 ? <Link to={c.to}>{c.label}</Link> : <span aria-current={i === crumbs.length - 1 ? 'page' : undefined}>{c.label}</span>}
                {i < crumbs.length - 1 && <ChevronRight size={14} aria-hidden="true" />}
              </li>
            ))}
          </ol>
        </nav>
      )}
      <div className="page-header__row">
        <div>
          <h1 className="page-header__title display">{title}</h1>
          {description && <p className="page-header__desc">{description}</p>}
        </div>
        {actions && <div className="page-header__actions">{actions}</div>}
      </div>
    </header>
  )
}
