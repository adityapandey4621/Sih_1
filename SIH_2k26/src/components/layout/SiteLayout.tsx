import { Outlet } from 'react-router-dom'
import { SiteHeader } from '../navigation/SiteHeader'
import { SiteFooter } from './SiteFooter'

export function SiteLayout() {
  return (
    <>
      <a className="skip-link" href="#main">Skip to content</a>
      <SiteHeader />
      <div id="main">
        <Outlet />
      </div>
      <SiteFooter />
    </>
  )
}
