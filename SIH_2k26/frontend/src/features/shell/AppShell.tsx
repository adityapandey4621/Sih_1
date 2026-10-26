import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { ChevronDown, LogOut, Menu, X } from 'lucide-react'
import { Wordmark } from '../../components/ui/Wordmark'
import { useAuth } from '../auth/AuthContext'
import { NotificationBell } from '../scrutiny/NotificationBell'
import { NAV_GROUPS, visibleNav, type NavItem } from './navigation'
import './shell.css'

function initials(name: string) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase())
    .join('')
}

function ProfileMenu() {
  const { state, signOut } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false)
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  if (state.status !== 'authenticated') return null
  const { user } = state

  async function out() {
    setBusy(true)
    await signOut()
    navigate('/sign-in', { replace: true })
  }

  return (
    <div className="profile" ref={ref}>
      <button type="button" className="profile__button" aria-expanded={open} aria-haspopup="true" onClick={() => setOpen((o) => !o)}>
        <span className="profile__avatar" aria-hidden="true">
          {initials(user.full_name)}
        </span>
        <span className="profile__who">
          <span className="profile__name">{user.full_name}</span>
          <span className="profile__role">{user.role_label}</span>
        </span>
        <ChevronDown size={16} aria-hidden="true" />
      </button>
      {open && (
        <div className="profile__menu" role="group" aria-label="Account">
          <div className="profile__details">
            <p className="profile__name">{user.full_name}</p>
            <p className="mono profile__email">{user.email}</p>
            <p className="profile__role">
              {user.role_label}
              {user.institution ? ` · ${user.institution.name}` : ''}
            </p>
          </div>
          <button type="button" className="profile__item" onClick={out} disabled={busy}>
            <LogOut size={16} aria-hidden="true" /> {busy ? 'Signing out…' : 'Sign out'}
          </button>
        </div>
      )}
    </div>
  )
}

function SideNav({ items, onNavigate }: { items: NavItem[]; onNavigate: () => void }) {
  const groups = (Object.keys(NAV_GROUPS) as NavItem['group'][])
    .map((g) => ({ g, items: items.filter((i) => i.group === g) }))
    .filter((x) => x.items.length)
  return (
    <nav aria-label="Main" className="sidenav">
      {groups.map(({ g, items: list }) => (
        <div key={g} className="sidenav__group">
          <p className="eyebrow sidenav__heading">{NAV_GROUPS[g]}</p>
          <ul>
            {list.map((item) => (
              <li key={item.id}>
                <NavLink to={item.to} className="sidenav__link" onClick={onNavigate}>
                  <item.icon size={18} aria-hidden="true" />
                  <span>{item.label}</span>
                  {item.planned && <span className="sidenav__tag">Planned</span>}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  )
}

export function AppShell() {
  const { state } = useAuth()
  const location = useLocation()
  const [drawer, setDrawer] = useState(false)
  const mainRef = useRef<HTMLElement>(null)

  // Close the drawer and move focus to the page content after navigation.
  useEffect(() => {
    setDrawer(false)
    mainRef.current?.focus({ preventScroll: true })
    window.scrollTo(0, 0)
  }, [location.pathname])

  useEffect(() => {
    if (!drawer) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setDrawer(false)
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [drawer])

  if (state.status !== 'authenticated') return null
  const items = visibleNav(state.user.permissions)

  return (
    <div className="shell" data-drawer={drawer}>
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <aside className="shell__side" id="sidebar">
        <div className="shell__brand">
          <Wordmark to="/app" />
          <button type="button" className="icon-button shell__close" onClick={() => setDrawer(false)} aria-label="Close navigation">
            <X size={20} aria-hidden="true" />
          </button>
        </div>
        <SideNav items={items} onNavigate={() => setDrawer(false)} />
        <p className="shell__build">Phase 5 — scrutiny, notices, expert visits (EVC) and SHC compliance / hearings. Executive Committee decisions and appeals are not built yet.</p>
      </aside>
      {drawer && <div className="shell__scrim" onClick={() => setDrawer(false)} aria-hidden="true" />}

      <div className="shell__body">
        <header className="topbar">
          <button type="button" className="icon-button topbar__menu" onClick={() => setDrawer(true)} aria-label="Open navigation" aria-expanded={drawer} aria-controls="sidebar">
            <Menu size={20} aria-hidden="true" />
          </button>
          <div className="topbar__spacer" />
          {state.user.permissions.includes('notifications:read') && <NotificationBell />}
          <ProfileMenu />
        </header>
        <main id="main" className="shell__main" tabIndex={-1} ref={mainRef}>
          <Outlet />
        </main>
      </div>
    </div>
  )
}
