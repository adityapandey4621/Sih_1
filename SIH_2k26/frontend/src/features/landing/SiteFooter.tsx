import { Link } from 'react-router-dom'
import { Wordmark } from '../../components/ui/Wordmark'

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="container site-footer__inner">
        <div>
          <Wordmark inverse />
          <p className="site-footer__note">
            PACE is an independent project supporting an approval workflow. It is not an official AICTE website, service or seal, and it is not connected to AICTE systems.
          </p>
        </div>
        <nav aria-label="Footer">
          <ul>
            <li><a href="/#overview">Product overview</a></li>
            <li><a href="/#workflow">How the workflow runs</a></li>
            <li><Link to="/sign-in">Sign in</Link></li>
            <li><Link to="/request-access">Request access</Link></li>
          </ul>
        </nav>
      </div>
    </footer>
  )
}
