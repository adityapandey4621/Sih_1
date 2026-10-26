import { Link } from 'react-router-dom'
import { Alert } from '../../components/ui/feedback'
import { AuthLayout } from './AuthLayout'

/**
 * Self-service reset needs an email provider (see backend/app/email_service.py), which is not configured.
 * This page says so instead of offering a form that would silently do nothing.
 */
export default function ForgotPasswordPage() {
  return (
    <AuthLayout title="Password recovery" footer={<p><Link to="/sign-in">Back to sign in</Link></p>}>
      <Alert tone="warning" title="Email-based recovery is not available">
        This deployment has no email service configured, so recovery links cannot be sent. Please contact your platform administrator to have your password reset.
      </Alert>
    </AuthLayout>
  )
}
