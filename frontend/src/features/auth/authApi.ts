import { request } from '../../lib/api'
import type { AccessRequestInput, InvitationPreview, SessionUser } from './types'

export const authApi = {
  /** Resolves to null when nobody is signed in (a normal state for public pages, not an error). */
  session: () => request<SessionUser | null>('/api/auth/session'),
  login: (email: string, password: string) =>
    request<SessionUser>('/api/auth/login', { method: 'POST', body: { email, password }, expectUnauthorized: true }),
  logout: () => request<void>('/api/auth/logout', { method: 'POST', expectUnauthorized: true }),
  submitAccessRequest: (input: AccessRequestInput) =>
    request<{ status: string; message: string }>('/api/access-requests', { method: 'POST', body: input }),
  previewInvitation: (token: string) =>
    request<InvitationPreview>('/api/auth/invitations/preview', { method: 'POST', body: { token } }),
  acceptInvitation: (token: string, password: string) =>
    request<{ status: string }>('/api/auth/invitations/accept', { method: 'POST', body: { token, password } }),
}
