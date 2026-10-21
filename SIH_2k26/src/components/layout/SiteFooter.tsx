import { SITE } from '../../features/home/home.constants'
import './layout.css'

export function SiteFooter() {
  return (
    <footer id="colophon" className="footer">
      <div className="footer__inner">
        <p className="footer__mark">{SITE.brand}</p>
        <p className="footer__note">{SITE.colophon}</p>
        <a className="footer__top" href="#top">Back to top ↑</a>
      </div>
    </footer>
  )
}
