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
const ApplicationsListPage = lazy(() => import('../features/applications/ApplicationsListPage'))
const NewApplicationPage = lazy(() => import('../features/applications/NewApplicationPage'))
const ApplicationDetailPage = lazy(() => import('../features/applications/ApplicationDetailPage'))
const ScrutinyHome = lazy(() => import('../features/scrutiny/ScrutinyHome'))
const ScrutinyCasePage = lazy(() => import('../features/scrutiny/CasePage'))
const NoticePage = lazy(() => import('../features/scrutiny/NoticePage'))
const NoticesListPage = lazy(() => import('../features/scrutiny/InstitutionPanels').then((m) => ({ default: m.NoticesListPage })))
const NotificationsPage = lazy(() => import('../features/scrutiny/NotificationsPage'))
const EvcQueuePage = lazy(() => import('../features/committee/QueuePage').then((m) => ({ default: m.EvcQueuePage })))
const ShcQueuePage = lazy(() => import('../features/committee/QueuePage').then((m) => ({ default: m.ShcQueuePage })))
const EvcCasePage = lazy(() => import('../features/committee/CommitteeCasePage').then((m) => ({ default: m.EvcCasePage })))
const ShcCasePage = lazy(() => import('../features/committee/CommitteeCasePage').then((m) => ({ default: m.ShcCasePage })))
const ComplianceListPage = lazy(() => import('../features/committee/InstitutionPanels').then((m) => ({ default: m.ComplianceListPage })))
const CompliancePage = lazy(() => import('../features/committee/InstitutionPanels'))
const RulesCatalogPage = lazy(() => import('../features/rules/RulesCatalogPage'))
const RuleVersionPage = lazy(() => import('../features/rules/RuleVersionPage'))
const RuleEditorPage = lazy(() => import('../features/rules/RuleEditorPage'))
const RuleReviewPage = lazy(() => import('../features/rules/RuleReviewPage'))

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

const guarded = (permission: string, node: ReactNode) => lazyPage(<RequirePermission permission={permission}>{node}</RequirePermission>)

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
              { path: 'admin/access-requests', element: guarded('admin:access-requests', <AccessRequestsPage />) },
              { path: 'admin/users', element: guarded('admin:users', <UsersPage />) },
              { path: 'audit-log', element: guarded('audit:read', <AuditLogPage />) },
              // Institution applications (backend re-checks permission and institution scope on every call)
              { path: 'applications', element: guarded('application:read_own', <ApplicationsListPage />) },
              { path: 'applications/new', element: guarded('application:create', <NewApplicationPage />) },
              { path: 'applications/:id', element: guarded('application:read_own', <ApplicationDetailPage />) },
              // Scrutiny (queue, case workspace) - every call is scope-checked again by the backend
              { path: 'scrutiny', element: guarded('dashboard:scrutiny', <ScrutinyHome />) },
              { path: 'scrutiny/:id', element: guarded('scrutiny:queue', <ScrutinyCasePage />) },
              // Institution: formal notices and responses
              { path: 'notices', element: guarded('notice:read_own', <NoticesListPage />) },
              { path: 'notices/:id', element: guarded('notice:read_own', <NoticePage />) },
              { path: 'notifications', element: guarded('notifications:read', <NotificationsPage />) },
              // EVC and SHC (Phase 5): every call is checked again by the backend for role and for this exact case
              { path: 'evc-coordination', element: guarded('evc:queue', <EvcQueuePage />) },
              { path: 'evc-member', element: guarded('evc:queue', <EvcQueuePage />) },
              { path: 'evc/:id', element: guarded('evc:queue', <EvcCasePage />) },
              { path: 'shc', element: guarded('shc:queue', <ShcQueuePage />) },
              { path: 'shc/:id', element: guarded('shc:queue', <ShcCasePage />) },
              // Institution: SHC compliance
              { path: 'compliance', element: guarded('committee:status_own', <ComplianceListPage />) },
              { path: 'compliance/:id', element: guarded('shc:respond', <CompliancePage />) },
              // Rule catalogue
              { path: 'rules', element: guarded('rules:read', <RulesCatalogPage />) },
              { path: 'rules/versions/:id', element: guarded('rules:read', <RuleVersionPage />) },
              { path: 'rules/versions/:id/edit', element: guarded('rules:edit', <RuleEditorPage />) },
              { path: 'rules/versions/:id/review', element: guarded('rules:approve', <RuleReviewPage />) },
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
