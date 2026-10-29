import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ApiError } from '../../lib/api'
import { useResource } from '../../lib/useResource'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Alert } from '../../components/ui/feedback'
import { CheckboxField, TextAreaField } from '../../components/ui/Field'
import { ResourceView } from '../admin/shared'
import { AccessDenied } from '../../components/ui/feedback'
import { useAuth } from '../auth/AuthContext'
import { Section } from '../applications/parts'
import { PageHeader } from '../shell/PageHeader'
import '../applications/applications.css'
import { ClassificationBadge, VersionBody, VersionStatusBadge } from './parts'
import { rulesApi } from './rulesApi'
import type { VersionDetail } from './types'

interface Diff {
  added: string[]
  removed: string[]
  changed: string[]
}

function diffAgainst(prev: VersionDetail, next: VersionDetail): { checklist: Diff; route: Diff; years: boolean } {
  const cmp = <T extends { label: string }>(a: T[], b: T[], key: (x: T) => string, same: (x: T, y: T) => boolean): Diff => {
    const am = new Map(a.map((x) => [key(x), x]))
    const bm = new Map(b.map((x) => [key(x), x]))
    return {
      added: [...bm.keys()].filter((k) => !am.has(k)),
      removed: [...am.keys()].filter((k) => !bm.has(k)),
      changed: [...bm.keys()].filter((k) => am.has(k) && !same(am.get(k)!, bm.get(k)!)),
    }
  }
  return {
    checklist: cmp(prev.checklist, next.checklist, (i) => i.item_key, (x, y) => JSON.stringify({ ...x, display_order: 0 }) === JSON.stringify({ ...y, display_order: 0 })),
    route: cmp(prev.route, next.route, (s) => s.stage_key, (x, y) => JSON.stringify({ ...x, display_order: 0 }) === JSON.stringify({ ...y, display_order: 0 })),
    years: JSON.stringify(prev.academic_years) !== JSON.stringify(next.academic_years),
  }
}

function DiffBlock({ title, d }: { title: string; d: Diff }) {
  const none = !d.added.length && !d.removed.length && !d.changed.length
  return (
    <div className="diff">
      <strong>{title}</strong>
      {none && <span className="muted">No changes</span>}
      {d.added.map((k) => <span key={`a${k}`} className="diff__added">+ added: <span className="mono">{k}</span></span>)}
      {d.removed.map((k) => <span key={`r${k}`} className="diff__removed">− removed: <span className="mono">{k}</span></span>)}
      {d.changed.map((k) => <span key={`c${k}`} className="diff__changed">~ changed: <span className="mono">{k}</span></span>)}
    </div>
  )
}

function Review({ v, setV }: { v: VersionDetail; setV: (v: VersionDetail) => void }) {
  const navigate = useNavigate()
  const [prev, setPrev] = useState<VersionDetail | null>(null)
  const [dialog, setDialog] = useState<'publish' | 'changes' | null>(null)
  const [note, setNote] = useState('')
  const [supersede, setSupersede] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const needsSupersede = v.publication_issues.some((i) => i.code === 'overlap_same_rule_set')
  const blocking = v.publication_issues.filter((i) => i.code !== 'overlap_same_rule_set')

  useEffect(() => {
    if (!v.supersedes_version_id) return
    rulesApi.version(v.supersedes_version_id).then(setPrev).catch(() => setPrev(null))
  }, [v.supersedes_version_id])
  const diff = useMemo(() => (prev ? diffAgainst(prev, v) : null), [prev, v])

  async function act(fn: () => Promise<VersionDetail>) {
    setBusy(true)
    setError(null)
    try {
      const next = await fn()
      setV(next)
      setDialog(null)
      navigate(`/app/rules/versions/${next.id}`, { replace: true })
    } catch (e) {
      setDialog(null)
      setError(e instanceof ApiError ? e.message + (e.issues.length ? ' ' + e.issues.map((i) => i.message).join(' ') : '') : 'The action failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="action-bar" role="group" aria-label="Review decision">
        <VersionStatusBadge status={v.status} />
        <ClassificationBadge classification={v.rule_set.classification} />
        <span className="action-bar__spacer" />
        <Button size="sm" variant="secondary" onClick={() => setDialog('changes')}>
          Request changes
        </Button>
        <Button size="sm" onClick={() => setDialog('publish')} disabled={blocking.length > 0}>
          Publish version
        </Button>
      </div>
      {blocking.length > 0 && <Alert tone="warning">Publishing is blocked until the items below are resolved. A reviewer can request changes to send the draft back to its author.</Alert>}
      {error && <Alert tone="error">{error}</Alert>}

      <Section title="What changed">
        {!v.supersedes_version_id ? (
          <p className="note">This is the first version of the rule set; there is nothing to compare it with.</p>
        ) : !diff ? (
          <p className="note">Loading the previous version…</p>
        ) : (
          <div className="form-grid">
            <DiffBlock title="Checklist items" d={diff.checklist} />
            <DiffBlock title="Route stages" d={diff.route} />
            <div className="diff">
              <strong>Academic years</strong>
              {diff.years ? <span className="diff__changed">~ changed: {prev?.academic_years.join(', ')} → {v.academic_years.join(', ')}</span> : <span className="muted">No changes</span>}
            </div>
          </div>
        )}
      </Section>

      <VersionBody v={v} />

      <ConfirmDialog open={dialog === 'publish'} title="Publish this version?" confirmLabel="Publish" busy={busy} onCancel={() => setDialog(null)} onConfirm={() => void act(() => rulesApi.publish(v.id, supersede, note))}>
        <p>Publishing makes this version selectable for new applications and freezes its content. It can never be edited afterwards; changes need a new version.</p>
        {v.rule_set.classification === 'DEMO' && <p><strong>This is DEMO ONLY configuration.</strong> It is not an official rule.</p>}
        {needsSupersede && (
          <CheckboxField label="Retire the currently published version of this rule set" checked={supersede} onChange={(e) => setSupersede(e.target.checked)} hint="Required: the two versions overlap, and only one may apply. Applications already submitted keep the version they used." />
        )}
        <TextAreaField label="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} optional />
      </ConfirmDialog>
      <ConfirmDialog open={dialog === 'changes'} title="Request changes" confirmLabel="Send back to author" busy={busy} confirmDisabled={note.trim().length < 5} onCancel={() => setDialog(null)} onConfirm={() => void act(() => rulesApi.requestChanges(v.id, note.trim()))}>
        <TextAreaField label="What needs to change?" value={note} onChange={(e) => setNote(e.target.value)} hint="At least 5 characters. The author sees this on the draft." />
      </ConfirmDialog>
    </>
  )
}

export default function RuleReviewPage() {
  const { id = '' } = useParams()
  const { can } = useAuth()
  const [res, reload] = useResource(() => rulesApi.version(id), [id])
  const [override, setOverride] = useState<VersionDetail | null>(null)
  if (!can('rules:approve')) return <AccessDenied message="Only a rules approver can review and publish a rule version." />
  return (
    <ResourceView resource={res} reload={reload}>
      {(loaded) => {
        const v = override?.id === loaded.id ? override : loaded
        return (
          <>
            <PageHeader
              title={`Review: ${v.rule_set.title} v${v.version_number}`}
              description="Check the content, its sources and what changed, then publish or send it back."
              crumbs={[{ label: 'Rule catalogue', to: '/app/rules' }, { label: `v${v.version_number}`, to: `/app/rules/versions/${v.id}` }, { label: 'Review' }]}
            />
            {v.status !== 'in_review' ? <Alert tone="info">This version is {v.status.replace('_', ' ')}, not awaiting review.</Alert> : <Review v={v} setV={setOverride} />}
            {v.status !== 'in_review' && <VersionBody v={v} />}
          </>
        )
      }}
    </ResourceView>
  )
}
