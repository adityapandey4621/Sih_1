import { request } from '../../lib/api'

export interface AccessRequest {
  id: string
  institution_name: string
  institution_identifier: string | null
  applicant_name: string
  applicant_email: string
  reason: string
  status: 'pending' | 'approved' | 'rejected'
  created_at: string
  reviewed_at: string | null
  review_note: string | null
}

export interface ManagedUser {
  id: string
  email: string
  full_name: string
  role: string
  role_label: string
  institution_name: string | null
  is_active: boolean
  last_login_at: string | null
}

export interface Invitation {
  id: string
  email: string
  full_name: string
  role: string
  role_label: string
  institution_name: string | null
  created_at: string
  expires_at: string
  status: 'pending' | 'accepted' | 'revoked' | 'expired'
}

export interface IssuedInvitation {
  invitation: Invitation
  invitation_path: string
  email_delivery: string
}

export interface AuditEvent {
  id: string
  occurred_at: string
  action: string
  actor_label: string | null
  target_type: string | null
  target_id: string | null
  ip_address: string | null
}

export interface RoleOption {
  value: string
  label: string
}

export const adminApi = {
  accessRequests: (status?: string) => request<AccessRequest[]>(`/api/admin/access-requests${status ? `?status=${status}` : ''}`),
  approve: (id: string) => request<IssuedInvitation>(`/api/admin/access-requests/${id}/approve`, { method: 'POST' }),
  reject: (id: string, note: string) => request<AccessRequest>(`/api/admin/access-requests/${id}/reject`, { method: 'POST', body: { note: note || null } }),
  roles: () => request<RoleOption[]>('/api/admin/roles'),
  users: () => request<ManagedUser[]>('/api/admin/users'),
  deactivate: (id: string) => request<void>(`/api/admin/users/${id}/deactivate`, { method: 'POST' }),
  activate: (id: string) => request<void>(`/api/admin/users/${id}/activate`, { method: 'POST' }),
  invitations: () => request<Invitation[]>('/api/admin/invitations'),
  invite: (body: { email: string; full_name: string; role: string; institution_name?: string }) =>
    request<IssuedInvitation>('/api/admin/invitations', { method: 'POST', body }),
  revokeInvitation: (id: string) => request<void>(`/api/admin/invitations/${id}/revoke`, { method: 'POST' }),
  audit: (limit: number, offset: number) => request<{ items: AuditEvent[]; total: number }>(`/api/audit/events?limit=${limit}&offset=${offset}`),
}
