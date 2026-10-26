import { useState } from 'react'
import { useResource } from '../../lib/useResource'
import { Button } from '../../components/ui/Button'
import { EmptyState } from '../../components/ui/feedback'
import { PageHeader } from '../shell/PageHeader'
import { adminApi } from './adminApi'
import { formatDate, ResourceView } from './shared'
import './admin.css'

const PAGE_SIZE = 25

export default function AuditLogPage() {
  const [offset, setOffset] = useState(0)
  const [page, reload] = useResource(() => adminApi.audit(PAGE_SIZE, offset), [offset])

  return (
    <>
      <PageHeader
        title="Audit log"
        description="Authentication and administration events, newest first. This view is read-only."
        crumbs={[{ label: 'Oversight', to: '/app' }, { label: 'Audit log' }]}
        actions={
          <Button variant="secondary" size="sm" onClick={reload}>
            Refresh
          </Button>
        }
      />
      <ResourceView resource={page} reload={reload}>
        {({ items, total }) =>
          items.length === 0 ? (
            <EmptyState title="No events recorded yet" />
          ) : (
            <>
              <div className="table-wrap">
                <table className="table">
                  <caption className="sr-only">Audit events</caption>
                  <thead>
                    <tr>
                      <th scope="col">Time</th>
                      <th scope="col">Action</th>
                      <th scope="col">Actor</th>
                      <th scope="col">Target</th>
                      <th scope="col">Address</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((e) => (
                      <tr key={e.id}>
                        <td className="mono nowrap">{formatDate(e.occurred_at)}</td>
                        <td className="mono">{e.action}</td>
                        <td className="mono">{e.actor_label ?? '—'}</td>
                        <td className="mono">{e.target_type ? `${e.target_type}${e.target_id ? ` ${e.target_id.slice(0, 8)}` : ''}` : '—'}</td>
                        <td className="mono">{e.ip_address ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="pager">
                <span className="muted">
                  {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
                </span>
                <div className="inline-actions">
                  <Button size="sm" variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                    Newer
                  </Button>
                  <Button size="sm" variant="secondary" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>
                    Older
                  </Button>
                </div>
              </div>
            </>
          )
        }
      </ResourceView>
    </>
  )
}
