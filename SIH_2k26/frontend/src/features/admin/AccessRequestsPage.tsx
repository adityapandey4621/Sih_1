import { useState } from 'react'
import { ApiError } from '../../lib/api'
import { useResource } from '../../lib/useResource'
import { Button } from '../../components/ui/Button'
import { TextField } from '../../components/ui/Field'
import { Alert, Badge, EmptyState, type Tone } from '../../components/ui/feedback'
import { PageHeader } from '../shell/PageHeader'
import { adminApi, type AccessRequest, type IssuedInvitation } from './adminApi'
import { formatDate, InvitationNotice, ResourceView } from './shared'
import './admin.css'

const FILTERS = [
  { value: 'pending', label: 'Pending' },
  { value: 'approved', label: 'Approved' },
  { value: 'rejected', label: 'Rejected' },
  { value: '', label: 'All' },
]
const TONE: Record<AccessRequest['status'], Tone> = { pending: 'warning', approved: 'success', rejected: 'error' }

function RequestRow({ req, onDone, onIssued }: { req: AccessRequest; onDone: () => void; onIssued: (i: IssuedInvitation) => void }) {
  const [rejecting, setRejecting] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function run(fn: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await fn()
      onDone()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'The action failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="request">
      <div className="request__head">
        <div>
          <p className="request__inst">{req.institution_name}</p>
          <p className="request__meta">
            {req.applicant_name} · <span className="mono">{req.applicant_email}</span>
            {req.institution_identifier && <> · ID <span className="mono">{req.institution_identifier}</span></>}
          </p>
        </div>
        <div className="request__side">
          <Badge tone={TONE[req.status]}>{req.status[0]?.toUpperCase() + req.status.slice(1)}</Badge>
          <span className="mono request__date">{formatDate(req.created_at)}</span>
        </div>
      </div>
      <p className="request__reason">{req.reason}</p>
      {req.review_note && <p className="request__meta">Review note: {req.review_note}</p>}
      {error && <Alert tone="error">{error}</Alert>}
      {req.status === 'pending' && !rejecting && (
        <div className="inline-actions">
          <Button
            size="sm"
            loading={busy}
            onClick={() =>
              run(async () => {
                onIssued(await adminApi.approve(req.id))
              })
            }
          >
            Approve and create invitation
          </Button>
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => setRejecting(true)}>
            Reject…
          </Button>
        </div>
      )}
      {req.status === 'pending' && rejecting && (
        <form
          className="reject-form"
          onSubmit={(e) => {
            e.preventDefault()
            void run(() => adminApi.reject(req.id, note.trim()))
          }}
        >
          <TextField label="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} />
          <div className="inline-actions">
            <Button type="submit" size="sm" variant="danger" loading={busy}>
              Confirm rejection
            </Button>
            <Button size="sm" variant="quiet" disabled={busy} onClick={() => setRejecting(false)}>
              Cancel
            </Button>
          </div>
        </form>
      )}
    </li>
  )
}

export default function AccessRequestsPage() {
  const [filter, setFilter] = useState('pending')
  const [list, reload] = useResource(() => adminApi.accessRequests(filter || undefined), [filter])
  const [issued, setIssued] = useState<IssuedInvitation | null>(null)

  return (
    <>
      <PageHeader
        title="Access requests"
        description="Institutions asking for access. Approval creates a single-use invitation for the applicant; it never creates an account directly."
        crumbs={[{ label: 'Administration', to: '/app' }, { label: 'Access requests' }]}
      />
      {issued && <InvitationNotice path={issued.invitation_path} email={issued.invitation.email} onDismiss={() => setIssued(null)} />}
      <div className="tabs" role="group" aria-label="Filter by status">
        {FILTERS.map((f) => (
          <button key={f.label} type="button" className="tab" aria-pressed={filter === f.value} onClick={() => setFilter(f.value)}>
            {f.label}
          </button>
        ))}
      </div>
      <ResourceView resource={list} reload={reload}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title="No requests to show">{filter === 'pending' ? 'There are no pending access requests.' : 'No requests match this filter.'}</EmptyState>
          ) : (
            <ul className="requests">
              {items.map((r) => (
                <RequestRow key={r.id} req={r} onDone={reload} onIssued={setIssued} />
              ))}
            </ul>
          )
        }
      </ResourceView>
    </>
  )
}
