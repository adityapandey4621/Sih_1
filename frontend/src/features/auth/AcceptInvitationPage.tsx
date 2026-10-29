import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ApiError } from '../../lib/api'
import { Button } from '../../components/ui/Button'
import { PasswordField } from '../../components/ui/Field'
import { Alert, LoadingState } from '../../components/ui/feedback'
import { AuthLayout } from './AuthLayout'
import { authApi } from './authApi'
import type { InvitationPreview } from './types'
import { PASSWORD_MIN_LENGTH } from './validation'

/** The token travels in the URL fragment so it is never sent to a server, proxy log or Referer header. */
const readToken = () => new URLSearchParams(window.location.hash.replace(/^#/, '')).get('token')

type Load = { s: 'loading' } | { s: 'invalid'; message: string } | { s: 'ready'; invite: InvitationPreview }

export default function AcceptInvitationPage() {
  const navigate = useNavigate()
  const [token] = useState(readToken)
  const [load, setLoad] = useState<Load>(token ? { s: 'loading' } : { s: 'invalid', message: 'This invitation link is incomplete.' })
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [errors, setErrors] = useState<{ password?: string; confirm?: string }>({})
  const [failure, setFailure] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!token) return
    let cancelled = false
    authApi
      .previewInvitation(token)
      .then((invite) => !cancelled && setLoad({ s: 'ready', invite }))
      .catch((e: unknown) => !cancelled && setLoad({ s: 'invalid', message: e instanceof ApiError ? e.message : 'The invitation could not be checked.' }))
    return () => {
      cancelled = true
    }
  }, [token])

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (busy || !token) return
    const next: typeof errors = {}
    if (password.length < PASSWORD_MIN_LENGTH) next.password = `Use at least ${PASSWORD_MIN_LENGTH} characters. A short passphrase of several words works well.`
    if (confirm !== password) next.confirm = 'The two passwords do not match.'
    setErrors(next)
    setFailure(null)
    if (Object.keys(next).length) return
    setBusy(true)
    try {
      await authApi.acceptInvitation(token, password)
      history.replaceState(null, '', window.location.pathname) // drop the token from the address bar
      navigate('/sign-in', { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.fieldErrors.password) setErrors({ password: err.fieldErrors.password })
      else setFailure(err instanceof ApiError ? err.message : 'Your account could not be created. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  if (load.s === 'loading') {
    return (
      <AuthLayout title="Accept invitation">
        <LoadingState label="Checking your invitation…" />
      </AuthLayout>
    )
  }
  if (load.s === 'invalid') {
    return (
      <AuthLayout title="Invitation unavailable" footer={<p>Ask your platform administrator for a new invitation, or <Link to="/sign-in">sign in</Link> if you already have an account.</p>}>
        <Alert tone="error">{load.message}</Alert>
      </AuthLayout>
    )
  }

  const { invite } = load
  return (
    <AuthLayout
      title="Set your password"
      intro={
        <>
          You have been invited as <strong>{invite.role_label}</strong>
          {invite.institution_name ? <> for {invite.institution_name}</> : null}. Account: <span className="mono">{invite.email}</span>
        </>
      }
    >
      {failure && <Alert tone="error">{failure}</Alert>}
      <form className="auth__form" onSubmit={onSubmit} noValidate aria-busy={busy}>
        <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: 0, display: 'grid', gap: 'var(--s-4)' }}>
          <PasswordField label="New password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} error={errors.password} hint={`At least ${PASSWORD_MIN_LENGTH} characters.`} />
          <PasswordField label="Confirm password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} error={errors.confirm} />
          <div className="auth__actions">
            <Button type="submit" loading={busy}>
              {busy ? 'Creating account…' : 'Create account'}
            </Button>
          </div>
        </fieldset>
      </form>
    </AuthLayout>
  )
}
