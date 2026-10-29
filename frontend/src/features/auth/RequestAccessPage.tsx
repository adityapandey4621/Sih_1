import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from '../../lib/api'
import { Button, ButtonLink } from '../../components/ui/Button'
import { TextAreaField, TextField } from '../../components/ui/Field'
import { Alert } from '../../components/ui/feedback'
import { AuthLayout } from './AuthLayout'
import { authApi } from './authApi'
import { isEmail, type Errors } from './validation'

type Fields = 'institution_name' | 'institution_identifier' | 'applicant_name' | 'applicant_email' | 'reason'

const EMPTY = { institution_name: '', institution_identifier: '', applicant_name: '', applicant_email: '', reason: '' }
const MIN_REASON = 20

export default function RequestAccessPage() {
  const [form, setForm] = useState(EMPTY)
  const [errors, setErrors] = useState<Errors<Fields>>({})
  const [failure, setFailure] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [receivedMessage, setReceivedMessage] = useState<string | null>(null)

  const set = (k: Fields) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }))

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (busy) return
    const next: Errors<Fields> = {}
    if (form.institution_name.trim().length < 3) next.institution_name = 'Enter the full name of the institution.'
    if (form.applicant_name.trim().length < 2) next.applicant_name = 'Enter your full name.'
    if (!isEmail(form.applicant_email)) next.applicant_email = 'Enter your official email address.'
    if (form.reason.trim().length < MIN_REASON) next.reason = `Please describe why you need access (at least ${MIN_REASON} characters).`
    setErrors(next)
    setFailure(null)
    if (Object.keys(next).length) return

    setBusy(true)
    try {
      const res = await authApi.submitAccessRequest(form)
      setReceivedMessage(res.message)
    } catch (err) {
      if (err instanceof ApiError && Object.keys(err.fieldErrors).length) {
        setErrors(err.fieldErrors as Errors<Fields>)
        setFailure('Some details were not accepted. Please review the highlighted fields.')
      } else {
        setFailure(err instanceof ApiError ? err.message : 'Your request could not be submitted. Please try again.')
      }
    } finally {
      setBusy(false)
    }
  }

  if (receivedMessage) {
    return (
      <AuthLayout title="Request received">
        <Alert tone="success" title="We have received your request">
          {receivedMessage}
        </Alert>
        <p className="auth__intro">
          An administrator will review the details you provided. If approved, you will be given an invitation to set your password. Approval is not automatic and may require verification.
        </p>
        <div className="auth__actions">
          <ButtonLink to="/" variant="secondary">
            Return to overview
          </ButtonLink>
        </div>
      </AuthLayout>
    )
  }

  return (
    <AuthLayout
      title="Request institution access"
      intro="Tell us who you are and why you need access. This creates a request for review — it does not create an account."
      footer={
        <p>
          Already have an account? <Link to="/sign-in">Sign in</Link>
        </p>
      }
    >
      {failure && <Alert tone="error">{failure}</Alert>}
      <form className="auth__form" onSubmit={onSubmit} noValidate aria-busy={busy}>
        <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: 0, display: 'grid', gap: 'var(--s-4)' }}>
          <TextField label="Institution name" name="institution_name" autoComplete="organization" value={form.institution_name} onChange={set('institution_name')} error={errors.institution_name} />
          <TextField
            label="Institution identifier"
            name="institution_identifier"
            optional
            hint="Any identifier you hold for the institution. It is recorded as supplied and is not verified automatically."
            value={form.institution_identifier}
            onChange={set('institution_identifier')}
            error={errors.institution_identifier}
          />
          <TextField label="Your full name" name="applicant_name" autoComplete="name" value={form.applicant_name} onChange={set('applicant_name')} error={errors.applicant_name} />
          <TextField label="Official email address" type="email" name="applicant_email" autoComplete="email" inputMode="email" value={form.applicant_email} onChange={set('applicant_email')} error={errors.applicant_email} />
          <TextAreaField label="Reason for requesting access" name="reason" value={form.reason} onChange={set('reason')} error={errors.reason} maxLength={2000} />
          <div className="auth__actions">
            <Button type="submit" loading={busy}>
              {busy ? 'Submitting…' : 'Submit request'}
            </Button>
          </div>
        </fieldset>
      </form>
    </AuthLayout>
  )
}
