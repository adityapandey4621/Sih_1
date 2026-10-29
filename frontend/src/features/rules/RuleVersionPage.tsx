import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ApiError } from '../../lib/api'
import { useResource } from '../../lib/useResource'
import { Button, ButtonLink } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Alert } from '../../components/ui/feedback'
import { TextAreaField } from '../../components/ui/Field'
import { ResourceView } from '../admin/shared'
import { useAuth } from '../auth/AuthContext'
import { PageHeader } from '../shell/PageHeader'
import '../applications/applications.css'
import { ClassificationBadge, VersionBody, VersionStatusBadge } from './parts'
import { rulesApi } from './rulesApi'
import type { VersionDetail } from './types'

function Actions({ v, onChanged }: { v: VersionDetail; onChanged: (v: VersionDetail) => void }) {
  const { can } = useAuth()
  const navigate = useNavigate()
  const [dialog, setDialog] = useState<'submit' | 'retire' | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function run(fn: () => Promise<VersionDetail>) {
    setBusy(true)
    setError(null)
    try {
      onChanged(await fn())
      setDialog(null)
      setReason('')
    } catch (e) {
      setDialog(null)
      setError(e instanceof ApiError ? e.message : 'The action failed.')
    } finally {
      setBusy(false)
    }
  }

  const editor = can('rules:edit')
  const approver = can('rules:approve')
  const none = !((editor && (v.status === 'draft' || v.status === 'published' || v.status === 'retired')) || (approver && (v.status === 'in_review' || v.status === 'published')))

  return (
    <>
      {error && <Alert tone="error">{error}</Alert>}
      <div className="action-bar" role="group" aria-label="Version actions">
        <VersionStatusBadge status={v.status} />
        <ClassificationBadge classification={v.rule_set.classification} />
        <span className="action-bar__spacer" />
        {editor && v.status === 'draft' && (
          <>
            <ButtonLink to={`/app/rules/versions/${v.id}/edit`} variant="secondary" size="sm">
              Edit draft
            </ButtonLink>
            <Button size="sm" onClick={() => setDialog('submit')}>
              Submit for review
            </Button>
          </>
        )}
        {approver && v.status === 'in_review' && (
          <ButtonLink to={`/app/rules/versions/${v.id}/review`} size="sm">
            Open review
          </ButtonLink>
        )}
        {approver && v.status === 'published' && (
          <Button size="sm" variant="danger" onClick={() => setDialog('retire')}>
            Retire version
          </Button>
        )}
        {editor && (v.status === 'published' || v.status === 'retired') && (
          <Button
            size="sm"
            variant="secondary"
            onClick={() =>
              void run(async () => {
                const next = await rulesApi.newVersion(v.rule_set.id)
                navigate(`/app/rules/versions/${next.id}/edit`)
                return next
              })
            }
          >
            Create new version
          </Button>
        )}
        {none && <span className="muted">You can view this version; your role cannot change it.</span>}
      </div>

      <ConfirmDialog open={dialog === 'submit'} title="Submit this draft for review?" confirmLabel="Submit for review" busy={busy} onCancel={() => setDialog(null)} onConfirm={() => void run(() => rulesApi.submitReview(v.id, v.row_version))}>
        <p>The draft becomes read-only while it is reviewed. A different person must approve it; you cannot publish your own work.</p>
      </ConfirmDialog>
      <ConfirmDialog open={dialog === 'retire'} danger title="Retire this version?" confirmLabel="Retire version" busy={busy} confirmDisabled={reason.trim().length < 5} onCancel={() => setDialog(null)} onConfirm={() => void run(() => rulesApi.retire(v.id, reason.trim()))}>
        <p>New applications will no longer use this version. Applications already submitted keep exactly the configuration they were submitted under, and this version's history is preserved.</p>
        <TextAreaField label="Reason for retiring" value={reason} onChange={(e) => setReason(e.target.value)} hint="At least 5 characters. Recorded in the history and audit log." />
      </ConfirmDialog>
    </>
  )
}

export default function RuleVersionPage() {
  const { id = '' } = useParams()
  const [res, reload] = useResource(() => rulesApi.version(id), [id])
  const [override, setOverride] = useState<VersionDetail | null>(null)
  return (
    <ResourceView resource={res} reload={reload}>
      {(loaded) => {
        const v = override?.id === loaded.id ? override : loaded
        return (
          <>
            <PageHeader
              title={`${v.rule_set.title} — version ${v.version_number}`}
              description={<span className="mono">{v.rule_set.key}</span>}
              crumbs={[{ label: 'Rule catalogue', to: '/app/rules' }, { label: `v${v.version_number}` }]}
            />
            <Actions v={v} onChanged={setOverride} />
            <VersionBody v={v} />
            <p className="note">
              <Link to="/app/rules" className="link">
                Back to the rule catalogue
              </Link>
            </p>
          </>
        )
      }}
    </ResourceView>
  )
}
