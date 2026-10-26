/** Mirrors backend/app/schemas.py. Permission strings come from the backend; see backend/app/rbac.py. */

export type Permission = string

export interface SessionUser {
  id: string
  email: string
  full_name: string
  role: string
  role_label: string
  institution: { id: string; name: string } | null
  permissions: Permission[]
  session_expires_at: string
}

export interface AccessRequestInput {
  institution_name: string
  institution_identifier: string
  applicant_name: string
  applicant_email: string
  reason: string
}

export interface InvitationPreview {
  email: string
  full_name: string
  role_label: string
  institution_name: string | null
}
