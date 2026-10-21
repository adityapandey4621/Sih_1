import { useState } from 'react'
import { useMotionValueEvent, useScroll } from 'motion/react'
import { Button } from '../ui/Button'
import { NAV_ITEMS, SITE } from '../../features/home/home.constants'
import './SiteHeader.css'

export function SiteHeader() {
  const [open, setOpen] = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const { scrollY } = useScroll()

  // Only re-render when the boolean flips, never per scroll frame.
  useMotionValueEvent(scrollY, 'change', (y) => {
    const next = y > 24
    setScrolled((prev) => (prev === next ? prev : next))
  })

  const close = () => setOpen(false)

  return (
    <header
      className="header"
      data-scrolled={scrolled}
      data-open={open}
      onKeyDown={(e) => e.key === 'Escape' && close()}
    >
      <div className="header__bar">
        <a className="header__brand" href="#top" onClick={close} aria-label={`${SITE.brand} — top of page`}>
          <span className="header__glyph" aria-hidden="true" />
          {SITE.brand}
          <span className="header__edition">{SITE.edition}</span>
        </a>

        <button
          type="button"
          className="header__toggle"
          aria-expanded={open}
          aria-controls="site-nav"
          onClick={() => setOpen((v) => !v)}
        >
          <span className="sr-only">{open ? 'Close menu' : 'Open menu'}</span>
          <span className="header__bars" aria-hidden="true" />
        </button>

        <nav id="site-nav" className="header__nav" aria-label="Primary">
          <ul>
            {NAV_ITEMS.map((item) => (
              <li key={item.href}>
                <a className="header__link" href={item.href} onClick={close}>
                  {item.label}
                </a>
              </li>
            ))}
          </ul>
          <Button href="#chapters" size="sm" onClick={close}>
            {SITE.cta}
          </Button>
        </nav>
      </div>
    </header>
  )
}
