import { useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { AccessDenied, Alert, ErrorState, LoadingState } from '../../components/ui/feedback'
import { Button } from '../../components/ui/Button'
import type { Resource } from '../../lib/useResource'

const dateFmt = new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeStyle: 'short' })
export const formatDate = (iso: string | null) => (iso ? dateFmt.format(new Date(iso)) : '—')

/** Renders the loading / error / forbidden states of a resource, or its content. */
export function ResourceView<T>({ resource, reload, children }: { resource: Resource<T>; reload: () => void; children: (data: T) => React.ReactNode }) {
  if (resource.status === 'loading') return <LoadingState />
  if (resource.status === 'error') return resource.forbidden ? <AccessDenied /> : <ErrorState message={resource.message} onRetry={reload} />
  return <>{children(resource.data)}</>
}

/** One-time display of an invitation link. No email is sent in Phase 1, and the page says so. */
export function InvitationNotice({ path, email, onDismiss }: { path: string; email: string; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false)
  const link = `${window.location.origin}${path}`
  async function copy() {
    try {
      await navigator.clipboard.writeText(link)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }
  return (
    <Alert tone="warning" title={`Invitation created for ${email}`}>
      <p>
        <strong>No email has been sent</strong> — email delivery is not configured. Pass this single-use link to the recipient through a secure channel. It is shown only now and cannot be retrieved again.
      </p>
      <p className="mono invite-link" tabIndex={0}>
        {link}
      </p>
      <div className="inline-actions">
        <Button size="sm" variant="secondary" onClick={copy}>
          {copied ? <Check size={16} aria-hidden="true" /> : <Copy size={16} aria-hidden="true" />}
          {copied ? 'Copied' : 'Copy link'}
        </Button>
        <Button size="sm" variant="quiet" onClick={onDismiss}>
          Dismiss
        </Button>
      </div>
    </Alert>
  )
}
