import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Button } from '../../components/ui/Button'
import { Alert, EmptyState, ErrorState, LoadingState } from '../../components/ui/feedback'
import { ApiError } from '../../lib/api'
import { formatDate } from '../../lib/format'
import { PageHeader } from '../shell/PageHeader'
import { notificationsApi } from './scrutinyApi'
import type { NotificationPage } from './types'
import './scrutiny.css'

export default function NotificationsPage() {
  const [page, setPage] = useState(1)
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [data, setData] = useState<NotificationPage | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setData(await notificationsApi.list(page, unreadOnly))
      setError(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Notifications could not be loaded.')
    }
  }, [page, unreadOnly])
  useEffect(() => {
    void load()
  }, [load])

  async function mark(id: string) {
    await notificationsApi.markRead(id).catch(() => undefined)
    void load()
  }

  return (
    <>
      <PageHeader title="Notifications" description="Events that concern you, stored in this application." crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Notifications' }]} />
      <Alert tone="info">
        These notifications are stored and shown here only. No email or text message is sent, because no delivery service is configured. A notice is also always visible on its own page.
      </Alert>
      <div className="action-bar">
        <label className="check-inline">
          <input type="checkbox" checked={unreadOnly} onChange={(e) => { setUnreadOnly(e.target.checked); setPage(1) }} /> Unread only
        </label>
        <Button variant="secondary" size="sm" disabled={!data?.unread} onClick={() => void notificationsApi.markAll().then(load)}>
          Mark all as read
        </Button>
      </div>
      {error && !data ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : !data ? (
        <LoadingState />
      ) : data.items.length === 0 ? (
        <EmptyState title={unreadOnly ? 'Nothing unread' : 'No notifications yet'}>Events such as a new case, an assignment, an issued notice or a received response appear here.</EmptyState>
      ) : (
        <>
          <ul className="notification-list">
            {data.items.map((n) => (
              <li key={n.id} className="notification" data-unread={!n.read || undefined}>
                <div>
                  <p className="notification__title">
                    {!n.read && <span className="notification__dot" aria-label="Unread" />}
                    {n.link ? (
                      <Link className="link" to={n.link} onClick={() => void mark(n.id)}>
                        {n.title}
                      </Link>
                    ) : (
                      n.title
                    )}
                  </p>
                  <p>{n.body}</p>
                  <p className="checklist__meta mono">
                    {formatDate(n.created_at)}
                    {n.application_reference ? ` · ${n.application_reference}` : ''} · {n.delivery_state === 'STORED_IN_APP' ? 'stored in the app' : n.delivery_state} · external delivery: {n.external_delivery === 'NOT_CONFIGURED' ? 'not configured' : n.external_delivery}
                  </p>
                </div>
                {!n.read && (
                  <Button size="sm" variant="quiet" onClick={() => void mark(n.id)}>
                    Mark as read
                  </Button>
                )}
              </li>
            ))}
          </ul>
          <nav className="pager" aria-label="Pages">
            <Button variant="quiet" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              Previous
            </Button>
            <span className="mono">
              Page {data.page} of {Math.max(1, Math.ceil(data.total / data.page_size))}
            </span>
            <Button variant="quiet" size="sm" disabled={page * data.page_size >= data.total} onClick={() => setPage((p) => p + 1)}>
              Next
            </Button>
          </nav>
        </>
      )}
    </>
  )
}
