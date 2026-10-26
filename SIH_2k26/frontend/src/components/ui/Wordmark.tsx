import { Link } from 'react-router-dom'

/** Typographic identity. Deliberately not a seal or emblem: this product is not an official AICTE service. */
export function Wordmark({ to = '/', inverse = false }: { to?: string; inverse?: boolean }) {
  return (
    <Link to={to} className={`wordmark${inverse ? ' wordmark--inverse' : ''}`} aria-label="Dossier — home">
      <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" className="wordmark__glyph">
        <path d="M5 2.5h9l5 5V21.5H5z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
        <path d="M14 2.5v5h5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
        <path d="M8.5 14.5l2.3 2.3 4.2-4.6" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span className="wordmark__text">Dossier</span>
    </Link>
  )
}
