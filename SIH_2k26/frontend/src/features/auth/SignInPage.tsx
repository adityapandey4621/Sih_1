import { useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { ApiError } from '../../lib/api'
import { Button } from '../../components/ui/Button'
import { TextField, PasswordField } from '../../components/ui/Field'
import { Alert } from '../../components/ui/feedback'
import { AuthLayout } from './AuthLayout'
import { useAuth } from './AuthContext'
import { isEmail, type Errors } from './validation'

type Fields = 'email' | 'password'

export default function SignInPage() {
  const { state, signIn } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [errors, setErrors] = useState<Errors<Fields>>({})
  const [failure, setFailure] = useState<{ tone: 'error' | 'warning'; message: string } | null>(null)
  const [busy, setBusy] = useState(false)

  if (state.status === 'authenticated') return <Navigate to={from ?? '/app'} replace />

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (busy) return
    const next: Errors<Fields> = {}
    if (!email.trim()) next.email = 'Enter your email address.'
    else if (!isEmail(email)) next.email = 'Enter a valid email address.'
    if (!password) next.password = 'Enter your password.'
    setErrors(next)
    setFailure(null)
    if (Object.keys(next).length) return

    setBusy(true)
    try {
      await signIn(email.trim(), password)
      navigate(from ?? '/app', { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setFailure({ tone: 'warning', message: err.message })
      } else if (err instanceof ApiError && (err.isNetwork || err.status >= 500)) {
        setFailure({ tone: 'error', message: err.message })
      } else if (err instanceof ApiError && err.status === 401) {
        setFailure({ tone: 'error', message: 'Incorrect email or password.' })
      } else {
        setFailure({ tone: 'error', message: 'Sign-in failed. Please try again.' })
      }
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  const expired = state.status === 'anonymous' && state.sessionExpired

  return (
    <AuthLayout
      title="Sign in"
      intro="Use the account issued to you. Access is limited to authorised institutional and official users."
      footer={
        <>
          <p>
            Institution without an account? <Link to="/request-access">Request access</Link>
          </p>
          <p>
            <Link to="/forgot-password">Forgot your password?</Link>
          </p>
        </>
      }
    >
      {expired && (
        <Alert tone="warning" title="Your session has ended">
          For your security you were signed out. Please sign in again to continue.
        </Alert>
      )}
      {failure && <Alert tone={failure.tone}>{failure.message}</Alert>}
      <form className="auth__form" onSubmit={onSubmit} noValidate aria-busy={busy}>
        <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: 0, display: 'grid', gap: 'var(--s-4)' }}>
          <TextField
            label="Email address"
            type="email"
            name="email"
            autoComplete="username"
            inputMode="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            error={errors.email}
            autoFocus
          />
          <PasswordField
            label="Password"
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            error={errors.password}
          />
          <div className="auth__actions">
            <Button type="submit" loading={busy}>
              {busy ? 'Signing in…' : 'Sign in'}
            </Button>
          </div>
        </fieldset>
      </form>
    </AuthLayout>
  )
}
