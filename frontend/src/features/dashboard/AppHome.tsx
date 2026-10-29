import { Navigate } from 'react-router-dom'
import { AccessDenied } from '../../components/ui/feedback'
import { useAuth } from '../auth/AuthContext'
import { homePathFor } from '../shell/navigation'

/** /app → the signed-in user's own dashboard. */
export function AppHome() {
  const { state } = useAuth()
  if (state.status !== 'authenticated') return null
  const home = homePathFor(state.user.permissions)
  if (!home) return <AccessDenied message="Your account has no workspace assigned. Please contact your platform administrator." />
  return <Navigate to={home} replace />
}
