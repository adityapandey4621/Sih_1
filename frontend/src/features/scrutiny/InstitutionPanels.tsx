import { Link } from 'react-router-dom'
import { Alert, Badge, EmptyState } from '../../components/ui/feedback'
import { formatDate } from '../../lib/format'
import { useResource } from '../../lib/useResource'
import { ResourceView } from '../admin/shared'
import { Section } from '../applications/parts'
import { PageHeader } from '../shell/PageHeader'
import '../applications/applications.css'
import { noticesApi } from './scrutinyApi'
import type { InstNoticeSummary } from './types'
import './scrutiny.css'

function NoticeTable({ items }: { items: InstNoticeSummary[] }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="sr-only">Deficiency notices</caption>
        <thead>
          <tr>
            <th scope="col">Notice</th>
            <th scope="col">Application</th>
            <th scope="col">Issued</th>
            <th scope="col">Response due</th>
            <th scope="col">Status</th>
            <th scope="col">Items answered</th>
          </tr>
        </thead>
        <tbody>
          {items.map((n) => (
            <tr key={n.id}>
              <td>
                <Link className="mono link" to={`/app/notices/${n.id}`}>
                  {n.reference}
                </Link>
              </td>
              <td className="mono">{n.application_reference}</td>
              <td className="mono nowrap">{formatDate(n.issued_at)}</td>
              <td className="nowrap">{n.response_due_at ? <span className="mono">{formatDate(n.response_due_at)}</span> : <span className="muted">No deadline established</span>}</td>
              <td>
                <Badge tone={n.status === 'ISSUED' ? 'warning' : 'info'}>{n.status_label}</Badge>
              </td>
              <td className="mono">
                {n.items_answered} of {n.items}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function NoticesListPage() {
  const [res, reload] = useResource(() => noticesApi.list(), [])
  return (
    <>
      <PageHeader title="Deficiency notices" description="Formal notices issued about your applications, and your responses." crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Notices' }]} />
      <ResourceView resource={res} reload={reload}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title="No notices have been issued">If scrutiny needs something from your institution, a formal notice appears here. Nothing is needed from you now.</EmptyState>
          ) : (
            <>
              {items.some((n) => n.status === 'ISSUED') && <Alert tone="warning" title="A response is needed">One or more notices are waiting for your response.</Alert>}
              <NoticeTable items={items} />
            </>
          )
        }
      </ResourceView>
    </>
  )
}

/** Where a submitted application stands in scrutiny, in the institution's terms: no internal names, notes or states. */
export function ScrutinyStatusPanel({ applicationId, refreshKey = 0 }: { applicationId: string; refreshKey?: number }) {
  const [res, reload] = useResource(() => noticesApi.applicationScrutiny(applicationId), [applicationId, refreshKey])
  return (
    <Section title="Scrutiny status">
      <ResourceView resource={res} reload={reload}>
        {(s) => (
          <>
            <p>
              <strong>{s.state_label}</strong>
            </p>
            <p className="note">{s.explanation}</p>
            {s.notices.some((n) => n.status === 'ISSUED') && (
              <Alert tone="warning" title="A response is needed">
                {s.notices.filter((n) => n.status === 'ISSUED').map((n) => (
                  <p key={n.id}>
                    <Link className="link" to={`/app/notices/${n.id}`}>
                      Open notice {n.reference}
                    </Link>
                    {n.response_due_at ? ` — respond by ${formatDate(n.response_due_at)}` : ' — no response deadline has been established'}
                  </p>
                ))}
              </Alert>
            )}
            {s.notices.length > 0 && <NoticeTable items={s.notices} />}
            {s.events.length > 0 && (
              <ol className="history">
                {s.events.map((e, i) => (
                  <li key={i} className="history__item">
                    <span className="mono history__at">{formatDate(e.at)}</span>
                    <span>{e.label}</span>
                  </li>
                ))}
              </ol>
            )}
            <p className="note">{s.not_an_approval}</p>
          </>
        )}
      </ResourceView>
    </Section>
  )
}
