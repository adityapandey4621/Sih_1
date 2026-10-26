import { Navigate, useParams } from 'react-router-dom'
import { ButtonLink } from '../../components/ui/Button'
import { AccessDenied, Alert, EmptyState } from '../../components/ui/feedback'
import { useAuth } from '../auth/AuthContext'
import { PageHeader } from '../shell/PageHeader'
import { findDashboard } from './dashboardSpecs'

/** One component renders every role's dashboard shell from its spec; access is decided by permission. */
export default function DashboardPage({ specKey }: { specKey?: string } = {}) {
  const { key } = useParams()
  const { state, can } = useAuth()
  const spec = findDashboard(specKey ?? key)

  if (state.status !== 'authenticated') return null
  if (!spec) return <Navigate to="/app" replace />
  if (!can(spec.permission)) return <AccessDenied />

  return (
    <div className="dash">
      <PageHeader title={spec.title} description={spec.description} crumbs={[{ label: 'Workspace', to: '/app' }, { label: spec.navLabel }]} />
      <Alert tone="info" title="No application data yet">
        This is the structure of your workspace. No applications or case records exist in this system yet, and nothing below is sample or live data.
      </Alert>
      {spec.planned && (
        <Alert tone="warning" title="Planned — not available in this phase">
          The workflow for this role is a later phase. This page shows only the intended structure; no actions are possible here yet.
        </Alert>
      )}
      {spec.note && <Alert tone="neutral">{spec.note}</Alert>}
      <div className="dash__sections">
        {spec.sections.map((s, i) => (
          <section key={s.title} className="dash-section" aria-labelledby={`sec-${i}`}>
            <div>
              <h2 id={`sec-${i}`} className="dash-section__title">
                {s.title}
              </h2>
              <p className="dash-section__desc">{s.description}</p>
            </div>
            <EmptyState
              title={s.emptyTitle}
              action={
                s.link && can(s.link.permission) ? (
                  <ButtonLink to={s.link.to} variant="secondary" size="sm">
                    {s.link.label}
                  </ButtonLink>
                ) : undefined
              }
            >
              {s.emptyBody}
            </EmptyState>
          </section>
        ))}
      </div>
    </div>
  )
}
