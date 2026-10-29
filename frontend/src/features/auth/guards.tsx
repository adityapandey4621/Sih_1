import type { ReactNode } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { AccessDenied, ErrorState, LoadingState } from '../../components/ui/feedback'
import { useAuth } from './AuthContext'

/** Gate for the authenticated area. The server still enforces access on every API call. */
export function RequireAuth() {
  const { state, retry } = useAuth()
  const location = useLocation()

  if (state.status === 'loading') {
    return (
      <div className="boot">
        <LoadingState label="Checking your session…" />
      </div>
    )
  }
  if (state.status === 'error') {
    return (
      <div className="boot">
        <ErrorState title="We could not check your session" message={state.message} onRetry={retry} />
      </div>
    )
  }
  if (state.status === 'anonymous') {
    return <Navigate to="/sign-in" replace state={{ from: location.pathname }} />
  }
  return <Outlet />
}

export function RequirePermission({ permission, children }: { permission: string; children: ReactNode }) {
  const { can } = useAuth()
  return can(permission) ? <>{children}</> : <AccessDenied />
}

/** Passes when the user holds at least one of the listed permissions (the server still decides what each one may see). */
export function RequireAnyPermission({ permissions, children }: { permissions: string[]; children: ReactNode }) {
  const { can } = useAuth()
  return permissions.some((p) => can(p)) ? <>{children}</> : <AccessDenied />
}
