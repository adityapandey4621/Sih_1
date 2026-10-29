import { useAuth } from '../auth/AuthContext'
import DashboardPage from '../dashboard/DashboardPage'
import ScrutinyQueuePage from './QueuePage'

/** The scrutiny landing page is the real queue for anyone who may see it; otherwise the role's dashboard shell. */
export default function ScrutinyHome() {
  const { can } = useAuth()
  return can('scrutiny:queue') ? <ScrutinyQueuePage /> : <DashboardPage specKey="scrutiny" />
}
