import { useRef, useState } from 'react'
import { Download } from 'lucide-react'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Alert, Badge, type Tone } from '../../components/ui/feedback'
import { TextAreaField, TextField } from '../../components/ui/Field'
import { ApiError } from '../../lib/api'
import { formatDay, humanise } from '../../lib/format'
import { Section } from '../applications/parts'
import { newKey, postApprovalApi } from './postApprovalApi'
import { DueLine, TimingBadge, fileSize } from './parts'
import type { ValidityItemView, ValidityVersionView } from './types'

const STATUS_TONE: Record<ValidityVersionView['status'], Tone> = { UNVERIFIED: 'warning', NEEDS_REVIEW: 'warning', VERIFIED: 'success', REJECTED: 'error', SUPERSEDED: 'neutral' }
const SOURCE_WORDS = { EXTRACTED: 'Read from the submitted document (candidate)', DECLARED: 'Stated by the institution with a renewal (claim)' }

type Act = (fn: () => Promise<ValidityItemView>) => Promise<boolean>

function VersionRow({ v, canReview, act, busy }: { v: ValidityVersionView; canReview: boolean; act: Act; busy: boolean }) {
  const pending = v.status === 'UNVERIFIED' || v.status === 'NEEDS_REVIEW'
  const [date, setDate] = useState(v.value_date ?? '')
  const [reason, setReason] = useState('')
  const [internal, setInternal] = useState('')
  const [mode, setMode] = useState<'verify' | 'reject' | null>(null)
  const key = useRef(newKey('vd'))
  return (
    <li className="notice-card" data-status={pending ? 'OPEN' : 'CLOSED'}>
      <p>
        <strong>Version {v.version_number}</strong> <Badge tone={STATUS_TONE[v.status]}>{humanise(v.status)}</Badge> <span className="muted">{SOURCE_WORDS[v.source]}</span>
      </p>
      <dl className="facts">
        <div>
          <dt>{v.source === 'EXTRACTED' ? 'Date read' : 'Date stated'}</dt>
          <dd className="mono">{v.value_date ? formatDay(v.value_date) : 'none'}</dd>
        </div>
        <div>
          <dt>Date confirmed by a person</dt>
          <dd className="mono">{v.verified_date ? `${formatDay(v.verified_date)}${v.verified_by ? ` · ${v.verified_by}` : ''}` : 'not yet confirmed'}</dd>
        </div>
        {v.provenance && (
          <div>
            <dt>Where it was read</dt>
            <dd>
              Submitted document version {v.provenance.document_version}
              {v.provenance.page ? `, page ${v.provenance.page}` : ''} · {v.provenance.method ? humanise(v.provenance.method) : ''}
              {v.provenance.confidence != null ? ` · confidence ${Math.round(v.provenance.confidence)}%` : ''}
              {v.provenance.evidence && <div className="mono muted">“{v.provenance.evidence}”</div>}
            </dd>
          </div>
        )}
        {v.file && (
          <div>
            <dt>Renewal file</dt>
            <dd>
              {canReview ? (
                <a className="link" href={v.file.download}>
                  <Download size={14} aria-hidden="true" /> {v.file.filename}
                </a>
              ) : (
                v.file.filename
              )}{' '}
              <span className="muted">
                {fileSize(v.file.size_bytes)} · {v.file.scan_status === 'NOT_SCANNED' ? 'not malware-scanned' : (v.file.scan_status ?? '').toLowerCase()}
              </span>
            </dd>
          </div>
        )}
      </dl>
      {v.needs_review_reason && <Alert tone="warning">{v.needs_review_reason}</Alert>}
      {v.conflict && (
        <Alert tone="warning" title="Conflicting dates">
          This date differs from {v.conflict.dates.map(formatDay).join(', ')} (version{v.conflict.with_versions.length === 1 ? '' : 's'} {v.conflict.with_versions.join(', ')}). A person must decide which is right.
        </Alert>
      )}
      {v.review_reason && v.status === 'REJECTED' && <p className="note">Not accepted: {v.review_reason}</p>}
      {v.clock && (
        <p>
          Expiry: <DueLine due={v.clock.effective_due_at ?? v.clock.due_at} timing={v.clock.timing} />
        </p>
      )}
      {canReview && pending && (
        <>
          {mode === null ? (
            <div className="actions">
              <Button size="sm" onClick={() => setMode('verify')}>
                Confirm a date…
              </Button>
              <Button size="sm" variant="secondary" onClick={() => setMode('reject')}>
                Do not accept…
              </Button>
              {v.source_download && (
                <a className="link" href={v.source_download}>
                  <Download size={14} aria-hidden="true" /> Open the source document
                </a>
              )}
            </div>
          ) : (
            <div>
              {mode === 'verify' && (
                <div className="field">
                  <label className="field__label" htmlFor={`vd-${v.id}`}>
                    Expiry date as shown on the document
                  </label>
                  <input id={`vd-${v.id}`} className="field__control" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
                  <p className="field__hint">It may differ from the date read or stated; both are kept. The date counts to the end of that day (UTC).</p>
                </div>
              )}
              <TextAreaField label={mode === 'verify' ? 'What you checked' : 'Why it cannot be accepted (shown to the institution)'} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={4000} hint="At least 10 characters." />
              <TextAreaField label="Internal note" optional value={internal} onChange={(e) => setInternal(e.target.value)} maxLength={4000} />
              <div className="actions">
                <Button variant="quiet" size="sm" onClick={() => setMode(null)}>
                  Cancel
                </Button>
                <ConfirmButton
                  busy={busy}
                  disabled={reason.trim().length < 10 || (mode === 'verify' && !date)}
                  label={mode === 'verify' ? 'Confirm this date' : 'Do not accept'}
                  onConfirm={() =>
                    act(() =>
                      mode === 'verify'
                        ? postApprovalApi.verifyDate(v.id, { verified_date: date, reason, internal_note: internal.trim() || null, idempotency_key: key.current })
                        : postApprovalApi.rejectDate(v.id, reason, internal.trim() || null),
                    ).then((ok) => {
                      if (ok) {
                        setMode(null)
                        setReason('')
                        setInternal('')
                        key.current = newKey('vd')
                      }
                      return ok
                    })
                  }
                  text={mode === 'verify' ? `Only a date a person has confirmed produces an expiry alert. You are confirming ${date || 'a date'} as the end of this document’s validity.` : 'The institution is told why. The earlier confirmed date and its alerts stay in force.'}
                />
              </div>
            </div>
          )}
        </>
      )}
    </li>
  )
}

function ConfirmButton({ label, text, disabled, busy, onConfirm }: { label: string; text: string; disabled: boolean; busy: boolean; onConfirm: () => Promise<boolean> }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button size="sm" disabled={disabled} onClick={() => setOpen(true)}>
        {label}
      </Button>
      <ConfirmDialog
        open={open}
        title={label}
        confirmLabel={label}
        busy={busy}
        onCancel={() => setOpen(false)}
        onConfirm={async () => {
          if (await onConfirm()) setOpen(false)
        }}
      >
        <p>{text}</p>
      </ConfirmDialog>
    </>
  )
}

function RenewForm({ item, act, busy }: { item: ValidityItemView; act: Act; busy: boolean }) {
  const [file, setFile] = useState<File | null>(null)
  const [date, setDate] = useState('')
  const key = useRef(newKey('rn'))
  const input = useRef<HTMLInputElement>(null)
  return (
    <div className="panel panel--collapsible">
      <p className="eyebrow">Upload a renewed document</p>
      <p className="note">A renewal is stored as a new version. Your earlier date and its reminders stay in force until a verifier confirms the renewal.</p>
      <div className="field">
        <label className="field__label" htmlFor={`rn-${item.id}`}>
          Renewed document (PDF, PNG or JPEG)
        </label>
        <input id={`rn-${item.id}`} ref={input} className="field__control" type="file" accept="application/pdf,image/png,image/jpeg" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </div>
      <TextField label="New expiry date shown on it" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
      <ConfirmButton
        busy={busy}
        disabled={!file || !date}
        label="Send the renewal"
        text="The renewal and the date you state are sent to a verifier. Your earlier date stays in force until it is verified."
        onConfirm={() =>
          act(() => postApprovalApi.renew(item.id, { file: file as File, declared_date: date, idempotency_key: key.current })).then((ok) => {
            if (ok) {
              setFile(null)
              setDate('')
              key.current = newKey('rn')
              if (input.current) input.current.value = ''
            }
            return ok
          })
        }
      />
    </div>
  )
}

export function ValidityCard({ item, onChanged }: { item: ValidityItemView; onChanged: () => void }) {
  const [shown, setShown] = useState<ValidityItemView | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [resolving, setResolving] = useState(false)
  const [why, setWhy] = useState('')
  const cur = shown && shown.id === item.id ? shown : item
  const act: Act = async (fn) => {
    setBusy(true)
    setError(null)
    try {
      setShown(await fn())
      onChanged()
      return true
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, 'Unexpected error.'))
      return false
    } finally {
      setBusy(false)
    }
  }
  return (
    <Section title={cur.label} aside={cur.status === 'RESOLVED' ? <Badge>No longer tracked</Badge> : <TimingBadge timing={cur.expiry_timing} />}>
      <p>
        <span className="mono">{cur.application.reference}</span> · {cur.application.institution_name}
      </p>
      <p>
        Confirmed expiry date: <strong className="mono">{cur.current_verified_date ? formatDay(cur.current_verified_date) : 'none confirmed yet'}</strong>
        {cur.awaiting_review > 0 && <span className="muted"> · {cur.awaiting_review} date{cur.awaiting_review === 1 ? '' : 's'} awaiting a person’s review</span>}
      </p>
      <p className="note">{cur.note}</p>
      {error && <Alert tone="error">{error.message}</Alert>}
      <ol className="plain-list">
        {cur.versions.map((v) => (
          <VersionRow key={v.id} v={v} canReview={cur.can.review} act={act} busy={busy} />
        ))}
      </ol>
      {cur.status === 'RESOLVED' && cur.resolution_reason && <Alert tone="info">No longer tracked: {cur.resolution_reason}</Alert>}
      {cur.can.renew && <RenewForm item={cur} act={act} busy={busy} />}
      {cur.can.review && cur.status === 'ACTIVE' && (
        <details className="panel panel--collapsible">
          <summary>This document no longer needs tracking</summary>
          <TextAreaField label="Reason" value={why} onChange={(e) => setWhy(e.target.value)} maxLength={4000} hint="For example a permitted change of policy. Its expiry reminders end; the history is kept. At least 10 characters." />
          <Button variant="secondary" size="sm" disabled={why.trim().length < 10} onClick={() => setResolving(true)}>
            Stop tracking
          </Button>
          <ConfirmDialog
            open={resolving}
            title="Stop tracking this document?"
            confirmLabel="Stop tracking"
            busy={busy}
            onCancel={() => setResolving(false)}
            onConfirm={async () => {
              if (await act(() => postApprovalApi.resolveItem(cur.id, why))) setResolving(false)
            }}
          >
            <p>Its expiry reminders end. The dates, reviews and history stay on record.</p>
          </ConfirmDialog>
        </details>
      )}
    </Section>
  )
}
