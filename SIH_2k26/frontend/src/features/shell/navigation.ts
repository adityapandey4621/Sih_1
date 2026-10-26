import { FilePlus2, FileWarning, Inbox, Library, ScrollText, UserCog, type LucideIcon } from 'lucide-react'
import { DASHBOARDS, dashboardPath } from '../dashboard/dashboardSpecs'

export interface NavItem {
  id: string
  label: string
  to: string
  icon: LucideIcon
  permission: string
  group: 'workspace' | 'administration' | 'oversight'
  /** Shell only: the workflow behind this area is not built yet. */
  planned?: boolean
}

export const NAV_GROUPS: Record<NavItem['group'], string> = {
  workspace: 'Workspace',
  administration: 'Administration',
  oversight: 'Oversight',
}

const dashboardItems: NavItem[] = DASHBOARDS.map((d) => ({
  id: d.key,
  label: d.navLabel,
  to: dashboardPath(d.key),
  icon: d.icon,
  permission: d.permission,
  group: 'workspace',
  planned: d.planned,
}))

export const NAV_ITEMS: NavItem[] = [
  ...dashboardItems,
  { id: 'applications', label: 'Applications', to: '/app/applications', icon: FilePlus2, permission: 'application:read_own', group: 'workspace' },
  { id: 'notices', label: 'Notices', to: '/app/notices', icon: FileWarning, permission: 'notice:read_own', group: 'workspace' },
  { id: 'rule-catalogue', label: 'Rule catalogue', to: '/app/rules', icon: Library, permission: 'rules:read', group: 'workspace' },
  { id: 'access-requests', label: 'Access requests', to: '/app/admin/access-requests', icon: Inbox, permission: 'admin:access-requests', group: 'administration' },
  { id: 'users', label: 'Users and invitations', to: '/app/admin/users', icon: UserCog, permission: 'admin:users', group: 'administration' },
  { id: 'audit-log', label: 'Audit log', to: '/app/audit-log', icon: ScrollText, permission: 'audit:read', group: 'oversight' },
]

export const visibleNav = (permissions: readonly string[]) => NAV_ITEMS.filter((i) => permissions.includes(i.permission))

/** Landing page after sign-in: the user's own dashboard. */
export function homePathFor(permissions: readonly string[]): string | null {
  const dash = DASHBOARDS.find((d) => permissions.includes(d.permission))
  return dash ? dashboardPath(dash.key) : null
}
