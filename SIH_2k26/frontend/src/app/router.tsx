import { lazy, Suspense, type ReactNode } from 'react'
import { createBrowserRouter, Outlet } from 'react-router-dom'
import { LoadingState } from '../components/ui/feedback'
import { AuthProvider } from '../features/auth/AuthContext'
import { RequireAuth, RequirePermission } from '../features/auth/guards'
import { AppShell } from '../features/shell/AppShell'
import { AppHome } from '../features/dashboard/AppHome'
import { NotFoundPage, NotFoundInShell } from './NotFound'

const LandingPage = lazy(() => import('../features/landing/LandingPage'))
const SignInPage = lazy(() => import('../features/auth/SignInPage'))
const RequestAccessPage = lazy(() => import('../features/auth/RequestAccessPage'))
const AcceptInvitationPage = lazy(() => import('../features/auth/AcceptInvitationPage'))
const ForgotPasswordPage = lazy(() => import('../features/auth/ForgotPasswordPage'))
const DashboardPage = lazy(() => import('../features/dashboard/DashboardPage'))
const AccessRequestsPage = lazy(() => import('../features/admin/AccessRequestsPage'))
const UsersPage = lazy(() => import('../features/admin/UsersPage'))
const AuditLogPage = lazy(() => import('../features/admin/AuditLogPage'))

const lazyPage = (node: ReactNode) => (
  <Suspense
    fallback={
      <div className="boot">
        <LoadingState />
      </div>
    }
  >
    {node}
  </Suspense>
)

function Root() {
  return (
    <AuthProvider>
      <Outlet />
    </AuthProvider>
  )
}

export const routes = [
  {
    element: <Root />,
    children: [
      { index: true, element: lazyPage(<LandingPage />) },
      { path: 'sign-in', element: lazyPage(<SignInPage />) },
      { path: 'request-access', element: lazyPage(<RequestAccessPage />) },
      { path: 'accept-invitation', element: lazyPage(<AcceptInvitationPage />) },
      { path: 'forgot-password', element: lazyPage(<ForgotPasswordPage />) },
      {
        path: 'app',
        element: <RequireAuth />,
        children: [
          {
            element: <AppShell />,
            children: [
              { index: true, element: <AppHome /> },
              { path: 'admin/access-requests', element: lazyPage(<RequirePermission permission="admin:access-requests"><AccessRequestsPage /></RequirePermission>) },
              { path: 'admin/users', element: lazyPage(<RequirePermission permission="admin:users"><UsersPage /></RequirePermission>) },
              { path: 'audit-log', element: lazyPage(<RequirePermission permission="audit:read"><AuditLogPage /></RequirePermission>) },
              { path: ':key', element: lazyPage(<DashboardPage />) },
              { path: '*', element: <NotFoundInShell /> },
            ],
          },
        ],
      },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]

export const router = createBrowserRouter(routes)
