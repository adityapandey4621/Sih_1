import { useState, type FormEvent } from 'react'
import { ApiError } from '../../lib/api'
import { useResource } from '../../lib/useResource'
import { Button } from '../../components/ui/Button'
import { TextField } from '../../components/ui/Field'
import { Alert, Badge, EmptyState, type Tone } from '../../components/ui/feedback'
import { isEmail } from '../auth/validation'
import { PageHeader } from '../shell/PageHeader'
import { adminApi, type Invitation, type IssuedInvitation, type ManagedUser } from './adminApi'
import { formatDate, InvitationNotice, ResourceView } from './shared'
import './admin.css'

const INVITE_TONE: Record<Invitation['status'], Tone> = { pending: 'warning', accepted: 'success', revoked: 'neutral', expired: 'neutral' }
const INSTITUTE_ROLE = 'institute_user'

function InviteForm({ onIssued }: { onIssued: (i: IssuedInvitation) => void }) {
  const [roles] = useResource(adminApi.roles, [])
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [role, setRole] = useState('')
  const [institution, setInstitution] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [failure, setFailure] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    const next: Record<string, string> = {}
    if (!isEmail(email)) next.email = 'Enter a valid email address.'
    if (name.trim().length < 2) next.full_name = 'Enter the full name.'
    if (!role) next.role = 'Choose a role.'
    if (role === INSTITUTE_ROLE && institution.trim().length < 3) next.institution_name = 'Enter the institution name.'
    setErrors(next)
    setFailure(null)
    if (Object.keys(next).length) return
    setBusy(true)
    try {
      const issued = await adminApi.invite({ email: email.trim(), full_name: name.trim(), role, institution_name: role === INSTITUTE_ROLE ? institution.trim() : undefined })
      onIssued(issued)
      setEmail('')
      setName('')
      setRole('')
      setInstitution('')
    } catch (err) {
      if (err instanceof ApiError && Object.keys(err.fieldErrors).length) setErrors(err.fieldErrors)
      setFailure(err instanceof ApiError ? err.message : 'The invitation could not be created.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="invite-form" onSubmit={submit} noValidate aria-busy={busy}>
      <h2 className="section-title">Invite a user</h2>
      {failure && <Alert tone="error">{failure}</Alert>}
      <div className="invite-form__grid">
        <TextField label="Full name" value={name} onChange={(e) => setName(e.target.value)} error={errors.full_name} />
        <TextField label="Email address" type="email" value={email} onChange={(e) => setEmail(e.target.value)} error={errors.email} />
        <div className="field" data-invalid={errors.role ? 'true' : undefined}>
          <label className="field__label" htmlFor="invite-role">
            Role
          </label>
          <select id="invite-role" className="field__control" value={role} onChange={(e) => setRole(e.target.value)} aria-invalid={errors.role ? true : undefined}>
            <option value="">Select a role…</option>
            {roles.status === 'ready' && roles.data.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
          {errors.role && <p className="field__error">{errors.role}</p>}
        </div>
        {role === INSTITUTE_ROLE && (
          <TextField label="Institution name" value={institution} onChange={(e) => setInstitution(e.target.value)} error={errors.institution_name} />
        )}
      </div>
      <div className="inline-actions">
        <Button type="submit" loading={busy}>
          Create invitation
        </Button>
      </div>
    </form>
  )
}

function UserRow({ user, onChanged }: { user: ManagedUser; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function toggle() {
    setBusy(true)
    setError(null)
    try {
      await (user.is_active ? adminApi.deactivate(user.id) : adminApi.activate(user.id))
      onChanged()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Action failed.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <tr>
      <td>
        {user.full_name}
        <br />
        <span className="mono muted">{user.email}</span>
      </td>
      <td>
        {user.role_label}
        {user.institution_name && <span className="muted"> · {user.institution_name}</span>}
      </td>
      <td>
        <Badge tone={user.is_active ? 'success' : 'neutral'}>{user.is_active ? 'Active' : 'Deactivated'}</Badge>
      </td>
      <td className="mono">{formatDate(user.last_login_at)}</td>
      <td>
        <Button size="sm" variant={user.is_active ? 'danger' : 'secondary'} loading={busy} onClick={toggle}>
          {user.is_active ? 'Deactivate' : 'Reactivate'}
        </Button>
        {error && <p className="field__error">{error}</p>}
      </td>
    </tr>
  )
}

export default function UsersPage() {
  const [users, reloadUsers] = useResource(adminApi.users, [])
  const [invitations, reloadInvitations] = useResource(adminApi.invitations, [])
  const [issued, setIssued] = useState<IssuedInvitation | null>(null)

  async function revoke(id: string) {
    await adminApi.revokeInvitation(id)
    reloadInvitations()
  }

  return (
    <>
      <PageHeader
        title="Users and invitations"
        description="Official roles are assigned here and nowhere else. Each invitation is single-use and expires."
        crumbs={[{ label: 'Administration', to: '/app' }, { label: 'Users and invitations' }]}
      />
      {issued && <InvitationNotice path={issued.invitation_path} email={issued.invitation.email} onDismiss={() => setIssued(null)} />}
      <InviteForm
        onIssued={(i) => {
          setIssued(i)
          reloadInvitations()
        }}
      />

      <h2 className="section-title">Invitations</h2>
      <ResourceView resource={invitations} reload={reloadInvitations}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title="No invitations yet" />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Invitee</th>
                    <th scope="col">Role</th>
                    <th scope="col">Status</th>
                    <th scope="col">Expires</th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((i) => (
                    <tr key={i.id}>
                      <td>
                        {i.full_name}
                        <br />
                        <span className="mono muted">{i.email}</span>
                      </td>
                      <td>{i.role_label}</td>
                      <td>
                        <Badge tone={INVITE_TONE[i.status]}>{i.status[0]?.toUpperCase() + i.status.slice(1)}</Badge>
                      </td>
                      <td className="mono">{formatDate(i.expires_at)}</td>
                      <td>
                        {i.status === 'pending' && (
                          <Button size="sm" variant="secondary" onClick={() => revoke(i.id)}>
                            Revoke
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </ResourceView>

      <h2 className="section-title">Accounts</h2>
      <ResourceView resource={users} reload={reloadUsers}>
        {(items) => (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">User</th>
                  <th scope="col">Role</th>
                  <th scope="col">Status</th>
                  <th scope="col">Last sign-in</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((u) => (
                  <UserRow key={u.id} user={u} onChanged={reloadUsers} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </ResourceView>
    </>
  )
}
