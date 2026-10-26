import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bell } from 'lucide-react'
import { usePolling } from '../../lib/usePolling'
import { notificationsApi } from './scrutinyApi'
import './scrutiny.css'

/** Unread count in the top bar. A failed refresh is silent: the count simply stays as it was. */
export function NotificationBell() {
  const [unread, setUnread] = useState(0)
  const load = useCallback(async () => {
    try {
      setUnread((await notificationsApi.list(1, true)).unread)
    } catch {
      /* retried on the next tick */
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])
  usePolling(load, true, 30000)
  return (
    <Link to="/app/notifications" className="icon-button bell" aria-label={unread ? `Notifications, ${unread} unread` : 'Notifications'}>
      <Bell size={20} aria-hidden="true" />
      {unread > 0 && (
        <span className="bell__count mono" aria-hidden="true">
          {unread > 99 ? '99+' : unread}
        </span>
      )}
    </Link>
  )
}
