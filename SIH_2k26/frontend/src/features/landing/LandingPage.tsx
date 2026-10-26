import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { ButtonAnchor, ButtonLink } from '../../components/ui/Button'
import { Badge } from '../../components/ui/feedback'
import { usePageTitleReset } from './usePageTitleReset'
import { SiteFooter } from './SiteFooter'
import { SiteHeader } from './SiteHeader'
import { WorkflowInstrument } from './WorkflowInstrument'
import { ASSURANCES, OVERVIEW } from './landing.content'
import './landing.css'

export default function LandingPage() {
  const { hash } = useLocation()
  usePageTitleReset()

  // Router navigation to /#section does not scroll by itself.
  useEffect(() => {
    if (!hash) return
    document.getElementById(hash.slice(1))?.scrollIntoView()
  }, [hash])

  return (
    <>
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <SiteHeader />
      <main id="main">
        <section className="hero container" aria-labelledby="hero-title">
          <p className="eyebrow hero__eyebrow">Approval workflow · compliance monitoring · alert management</p>
          <h1 id="hero-title" className="hero__title display">
            Every application. Every action. Accounted for.
          </h1>
          <div className="hero__lower">
            <p className="hero__lede">
              An integrated workflow for application processing, evidence review, deadline monitoring and compliance tracking — with authorised human decisions and complete traceability.
            </p>
            <div className="hero__actions">
              <ButtonLink to="/sign-in">Sign in</ButtonLink>
              <ButtonAnchor href="#workflow" variant="secondary">
                Explore the workflow
              </ButtonAnchor>
            </div>
          </div>
        </section>

        <section id="workflow" className="container block" aria-labelledby="workflow-title">
          <div className="block__head">
            <h2 id="workflow-title" className="block__title display">
              Follow one application through the process
            </h2>
            <p className="block__intro">A walkthrough of the stages an application passes through. Select a stage to read what happens there and who acts.</p>
          </div>
          <WorkflowInstrument />
        </section>

        <section id="overview" className="container block" aria-labelledby="overview-title">
          <div className="block__head">
            <h2 id="overview-title" className="block__title display">
              What the system brings together
            </h2>
            <p className="block__intro">Five capabilities, in the order an application meets them. The status beside each is where it stands today.</p>
          </div>
          <ol className="sequence">
            {OVERVIEW.map((item, i) => (
              <li key={item.title} className="sequence__item">
                <span className="sequence__num mono" aria-hidden="true">
                  {String(i + 1).padStart(2, '0')}
                </span>
                <h3 className="sequence__title">{item.title}</h3>
                <p className="sequence__body">{item.body}</p>
                <p className="sequence__status">
                  <Badge tone={item.status.startsWith('Audit') ? 'success' : 'neutral'}>{item.status}</Badge>
                </p>
              </li>
            ))}
          </ol>
        </section>

        <section id="security" className="security" aria-labelledby="security-title">
          <div className="container security__inner">
            <div>
              <h2 id="security-title" className="block__title display">
                Built to support authorised people, not to replace them
              </h2>
              <p className="security__lede">
                The system routes work, checks evidence, keeps the clock and keeps the record. Scrutiny, committee judgement and the final decision stay with the officials the process names.
              </p>
              <p className="security__disclaimer">
                Dossier is not an official AICTE service and makes no claim of endorsement or connection. Integration with AICTE systems would be added only where authorised.
              </p>
            </div>
            <dl className="assurances">
              {ASSURANCES.map((a) => (
                <div key={a.term} className="assurances__row">
                  <dt>{a.term}</dt>
                  <dd>{a.detail}</dd>
                </div>
              ))}
            </dl>
          </div>
        </section>

        <section className="container closing" aria-labelledby="closing-title">
          <h2 id="closing-title" className="closing__title display">
            Ready to begin?
          </h2>
          <div className="hero__actions">
            <ButtonLink to="/sign-in">Sign in</ButtonLink>
            <ButtonLink to="/request-access" variant="secondary">
              Request institution access
            </ButtonLink>
          </div>
        </section>
      </main>
      <SiteFooter />
    </>
  )
}
