import { useState } from 'react'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Alert, Badge } from '../../components/ui/feedback'
import { TextAreaField } from '../../components/ui/Field'
import type { ApiError } from '../../lib/api'
import { formatDate } from '../../lib/format'
import { Section } from '../applications/parts'
import { FindingStateBadge, SourceRefView, StageTag } from './parts'
import type { ReviewFinding } from './types'

type Conclusion = 'RESOLVED' | 'UNRESOLVED' | 'NOT_SUPPORTED'

const CONCLUSIONS: Record<Conclusion, { label: string; confirm: string; prompt: string }> = {
  RESOLVED: { label: 'Resolved', confirm: 'Record as resolved', prompt: 'Why is this resolved? (what you examined, and what you found)' },
  UNRESOLVED: { label: 'Not resolved by the response', confirm: 'Record as unresolved', prompt: 'Why does the response not resolve it?' },
  NOT_SUPPORTED: { label: 'Not supported', confirm: 'Record as not supported', prompt: 'Why is this not a valid finding?' },
}

function options(f: ReviewFinding): Conclusion[] {
  if (f.state === 'OPEN') return ['RESOLVED', 'NOT_SUPPORTED']
  if (f.state === 'NOTICED') return ['RESOLVED', 'UNRESOLVED', 'NOT_SUPPORTED']
  if (f.state === 'UNRESOLVED') return ['RESOLVED', 'NOT_SUPPORTED']
  return []
}

function FindingCard({
  f, canWork, selected, onSelect, onEdit, onConclude, onOpenEvidence,
}: {
  f: ReviewFinding
  canWork: boolean
  selected: boolean
  onSelect: ((checked: boolean) => void) | null
  onEdit: () => void
  onConclude: (c: Conclusion) => void
  onOpenEvidence: (f: ReviewFinding) => void
}) {
  const [history, setHistory] = useState(false)
  return (
    <li className="finding-card" data-state={f.state}>
      <div className="finding__head">
        <strong>
          Finding {f.sequence} — {f.issue_label}
        </strong>
        <span className="finding__tags">
          <StageTag kind="finding" />
          <FindingStateBadge state={f.state} />
          <Badge tone={f.visibility === 'ELIGIBLE_FOR_NOTICE' ? 'info' : 'neutral'}>{f.visibility === 'ELIGIBLE_FOR_NOTICE' ? 'May go into a notice' : 'Internal only'}</Badge>
        </span>
      </div>
      <p className="checklist__meta mono">
        Round {f.round_number} · recorded by {f.created_by} · {formatDate(f.created_at)}
        {f.notice_reference ? ` · in notice ${f.notice_reference}` : ''}
      </p>
      <div className="finding-card__block">
        <p className="eyebrow">Reviewer’s description — internal</p>
        <p>{f.description}</p>
      </div>
      {f.visibility === 'ELIGIBLE_FOR_NOTICE' && (
        <div className="finding-card__block finding-card__block--external">
          <p className="eyebrow">Wording for the institution</p>
          <p>{f.institution_text}</p>
          <p>
            <strong>Requested:</strong> {f.requested_correction}
          </p>
        </div>
      )}
      <p className="checklist__meta">
        {f.item_label ? (
          <>
            {f.item_label}
            {f.document_version ? (
              <>
                {' '}
                · <span className="mono">version {f.document_version}</span>
                {f.document_superseded && f.document_current_version ? <strong> (since replaced — the current version is {f.document_current_version})</strong> : ''}
              </>
            ) : (
              ' · no file linked'
            )}
            {f.page_number ? <span className="mono"> · page {f.page_number}</span> : ''}
            {f.item_key && (
              <>
                {' · '}
                <button type="button" className="linklike" onClick={() => onOpenEvidence(f)}>
                  Open the evidence
                </button>
              </>
            )}
          </>
        ) : (
          'Not tied to one requirement'
        )}
      </p>
      <p className="checklist__meta">
        <strong>Source:</strong> <SourceRefView source={f.source_ref} status={f.source_ref_status} />
      </p>
      {f.state_reason && (
        <p className="finding-card__conclusion">
          <strong>{f.state === 'RESOLVED' ? 'Resolved' : f.state === 'UNRESOLVED' ? 'Not resolved' : f.state === 'NOT_SUPPORTED' ? 'Not supported' : 'Conclusion'}:</strong> {f.state_reason}
        </p>
      )}
      {f.preliminary_finding_id && <p className="checklist__meta">Started from an automated preliminary issue — recorded by a person, so it is a finding.</p>}
      <div className="inline-actions">
        {canWork && onSelect && (
          <label className="check-inline">
            <input type="checkbox" checked={selected} onChange={(e) => onSelect(e.target.checked)} /> Include in a notice
          </label>
        )}
        {canWork && f.state === 'OPEN' && (
          <Button size="sm" variant="secondary" onClick={onEdit}>
            Edit
          </Button>
        )}
        {canWork && options(f).map((c) => (
          <Button key={c} size="sm" variant="quiet" onClick={() => onConclude(c)}>
            {CONCLUSIONS[c].label}
          </Button>
        ))}
        <button type="button" className="linklike" aria-expanded={history} onClick={() => setHistory((h) => !h)}>
          {history ? 'Hide history' : `History (${f.history.length})`}
        </button>
      </div>
      {history && (
        <ol className="history">
          {f.history.map((h, i) => (
            <li key={i} className="history__item">
              <span className="mono history__at">{formatDate(h.at)}</span>
              <span>
                <strong>{h.action.replace(/_/g, ' ')}</strong> — {h.actor} <span className="muted">(round {h.round_number})</span>
                {h.reason && <em> — {h.reason}</em>}
              </span>
            </li>
          ))}
        </ol>
      )}
    </li>
  )
}

export function FindingsPanel({
  findings, allowed, busy, error, onRecord, onEdit, onConclude, onPrepareNotice, onOpenEvidence,
}: {
  findings: ReviewFinding[]
  allowed: string[]
  busy: boolean
  error: ApiError | null
  onRecord: () => void
  onEdit: (f: ReviewFinding) => void
  onConclude: (f: ReviewFinding, state: Conclusion, reason: string) => Promise<boolean>
  onPrepareNotice: (ids: string[]) => void
  onOpenEvidence: (f: ReviewFinding) => void
}) {
  const [picked, setPicked] = useState<string[]>([])
  const [dialog, setDialog] = useState<{ f: ReviewFinding; c: Conclusion } | null>(null)
  const [reason, setReason] = useState('')
  const canWork = allowed.includes('conclude_finding')
  const canPrepare = allowed.includes('prepare_notice')
  const pending = findings.filter((f) => f.state === 'OPEN' || f.state === 'NOTICED' || f.state === 'UNRESOLVED')
  const concluded = findings.filter((f) => f.state === 'RESOLVED' || f.state === 'NOT_SUPPORTED')
  const eligible = (f: ReviewFinding) => (f.state === 'OPEN' || f.state === 'UNRESOLVED') && f.visibility === 'ELIGIBLE_FOR_NOTICE'

  const card = (f: ReviewFinding) => (
    <FindingCard
      key={f.id}
      f={f}
      canWork={canWork}
      selected={picked.includes(f.id)}
      onSelect={canPrepare && eligible(f) ? (c) => setPicked((p) => (c ? [...p, f.id] : p.filter((x) => x !== f.id))) : null}
      onEdit={() => onEdit(f)}
      onConclude={(c) => { setReason(''); setDialog({ f, c }) }}
      onOpenEvidence={onOpenEvidence}
    />
  )

  return (
    <Section
      title={`Findings (${findings.length})`}
      aside={
        allowed.includes('record_finding') ? (
          <Button size="sm" onClick={onRecord}>
            Record a finding
          </Button>
        ) : undefined
      }
    >
      <Alert tone="info">
        These are conclusions recorded by a person. Automated checks only point at possible problems; nothing here is a formal notice until an authorised issuer issues one.
      </Alert>
      {canWork && !allowed.includes('record_finding') && (
        <Alert tone="info" title="Re-scrutiny checks only the deficiencies already raised">
          The Approval Process Handbook (Ch. I cl. 1.8(g)) limits re-scrutiny to the deficiencies the scrutiny pointed out, so no new finding can be recorded in this round. Conclude each existing finding with the evidence the institution supplied.
        </Alert>
      )}
      {findings.length === 0 && <p className="note">No findings have been recorded. If your examination finds nothing to raise, you can complete the round without findings.</p>}
      {pending.length > 0 && (
        <>
          <h3 className="panel__sub">To conclude or put in a notice ({pending.length})</h3>
          <ul className="finding-list">{pending.map(card)}</ul>
        </>
      )}
      {concluded.length > 0 && (
        <>
          <h3 className="panel__sub">Concluded ({concluded.length})</h3>
          <ul className="finding-list">{concluded.map(card)}</ul>
        </>
      )}
      {canPrepare && (
        <div className="action-bar">
          <Button disabled={picked.length === 0} onClick={() => onPrepareNotice(picked)}>
            Prepare a notice from {picked.length || 'the selected'} finding{picked.length === 1 ? '' : 's'}…
          </Button>
          <span className="muted">Only findings marked “May go into a notice” can be selected. Preparing a notice does not issue it.</span>
        </div>
      )}

      <ConfirmDialog
        open={!!dialog}
        title={dialog ? `${CONCLUSIONS[dialog.c].label}: finding ${dialog.f.sequence}` : ''}
        confirmLabel={dialog ? CONCLUSIONS[dialog.c].confirm : ''}
        busy={busy}
        confirmDisabled={reason.trim().length < 5}
        onCancel={() => setDialog(null)}
        onConfirm={async () => {
          if (dialog && (await onConclude(dialog.f, dialog.c, reason.trim()))) setDialog(null)
        }}
      >
        {dialog && (
          <>
            <p className="note">A concluded finding is final and is kept with its reason in the case history.</p>
            <TextAreaField label={CONCLUSIONS[dialog.c].prompt} rows={4} value={reason} onChange={(e) => setReason(e.target.value)} />
            {error && <Alert tone="error">{error.message}</Alert>}
          </>
        )}
      </ConfirmDialog>
    </Section>
  )
}
