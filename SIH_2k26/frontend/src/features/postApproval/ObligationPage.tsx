import { useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Download } from 'lucide-react'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Alert, Badge, type Tone } from '../../components/ui/feedback'
import { SelectField, TextAreaField, TextField } from '../../components/ui/Field'
import { ApiError } from '../../lib/api'
import { formatDate, humanise } from '../../lib/format'
import { useResource } from '../../lib/useResource'
import { useAuth } from '../auth/AuthContext'
import { ResourceView } from '../admin/shared'
import { Section } from '../applications/parts'
import { Fact, Facts } from '../scrutiny/parts'
import { PageHeader } from '../shell/PageHeader'
import '../applications/applications.css'
import '../scrutiny/scrutiny.css'
import '../committee/committee.css'
import './postApproval.css'
import { newKey, postApprovalApi } from './postApprovalApi'
import { DueLine, fileSize } from './parts'
import type { ObligationDetail, ObligationStatus } from './types'

const TONE: Record<ObligationStatus, Tone> = { AWAITING_EVIDENCE: 'warning', EVIDENCE_SUBMITTED: 'info', CORRECTION_REQUESTED: 'warning', VERIFIED: 'success', NOT_MET: 'error', WITHDRAWN: 'neutral' }
export const StatusBadge = ({ status, label }: { status: ObligationStatus; label: string }) => <Badge tone={TONE[status] ?? 'neutral'}>{label}</Badge>

type Run = (fn: () => Promise<ObligationDetail>) => Promise<boolean>

function useRunner(set: (d: ObligationDetail) => void) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const run: Run = async (fn) => {
    setBusy(true)
    setError(null)
    try {
      set(await fn())
      return true
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, 'Unexpected error.'))
      return false
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, run }
}

const ErrorNote = ({ e }: { e: ApiError | null }) => (e ? <Alert tone="error">{e.message}</Alert> : null)

// ---- the institution submits evidence --------------------------------------------------------------------------

function SubmitEvidence({ o, run, busy, error }: { o: ObligationDetail; run: Run; busy: boolean; error: ApiError | null }) {
  const [files, setFiles] = useState<{ label: string; file: File }[]>([])
  const [note, setNote] = useState('')
  const [confirm, setConfirm] = useState(false)
  const key = useRef(newKey('evid'))
  const input = useRef<HTMLInputElement>(null)
  return (
    <Section title={o.status === 'CORRECTION_REQUESTED' ? 'Send the corrected evidence' : 'Submit your evidence'}>
      <Alert tone="info">{o.notes.upload}</Alert>
      <p>
        <strong>What is required:</strong> {o.required_evidence}
      </p>
      <div className="field">
        <label className="field__label" htmlFor="evidence-files">
          Files (PDF, PNG or JPEG, up to 5)
        </label>
        <input
          id="evidence-files"
          ref={input}
          className="field__control"
          type="file"
          multiple
          accept="application/pdf,image/png,image/jpeg"
          onChange={(e) => setFiles(Array.from(e.target.files ?? []).slice(0, 5).map((f) => ({ label: f.name.replace(/\.[^.]+$/, ''), file: f })))}
        />
      </div>
      {files.length > 0 && (
        <ul className="plain-list">
          {files.map((f, i) => (
            <li key={f.file.name + i}>
              <TextField
                label={`Label for ${f.file.name} (${fileSize(f.file.size)})`}
                value={f.label}
                maxLength={120}
                onChange={(e) => setFiles(files.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))}
                hint="A resubmission with the same label becomes the next version of that document; the earlier one is kept."
              />
            </li>
          ))}
        </ul>
      )}
      <TextAreaField label="Note for the verifier" optional value={note} onChange={(e) => setNote(e.target.value)} maxLength={8000} />
      <ErrorNote e={error} />
      <Button onClick={() => setConfirm(true)} disabled={files.length === 0 || files.some((f) => !f.label.trim())}>
        Submit evidence
      </Button>
      <ConfirmDialog
        open={confirm}
        title="Submit this evidence?"
        confirmLabel="Submit evidence"
        busy={busy}
        onCancel={() => setConfirm(false)}
        onConfirm={async () => {
          const ok = await run(() => postApprovalApi.submitEvidence(o.id, { files, note, row_version: o.row_version, idempotency_key: key.current }))
          if (ok) {
            setConfirm(false)
            setFiles([])
            setNote('')
            key.current = newKey('evid')
            if (input.current) input.current.value = ''
          }
        }}
      >
        <p>
          {files.length} file{files.length === 1 ? '' : 's'} will be sent to the assigned verifier. Submitting is <strong>not</strong> verification: the obligation stays open until the verifier records an outcome.
        </p>
      </ConfirmDialog>
    </Section>
  )
}

// ---- the verifier records an outcome ----------------------------------------------------------------------------

function ReviewForm({ o, run, busy, error }: { o: ObligationDetail; run: Run; busy: boolean; error: ApiError | null }) {
  const [outcome, setOutcome] = useState('')
  const [reason, setReason] = useState('')
  const [internal, setInternal] = useState('')
  const [confirm, setConfirm] = useState(false)
  const key = useRef(newKey('rev'))
  const latest = o.submissions_detail[o.submissions_detail.length - 1]
  const label = outcome === 'VERIFIED' ? 'Record as verified' : outcome === 'CORRECTION_REQUESTED' ? 'Request a correction' : 'Record as not met'
  return (
    <Section title="Record your verification outcome">
      <Alert tone="info">
        You are the assigned verifier. Open each file of submission {latest?.sequence} before you decide. Your outcome is recorded with your name and cannot be edited afterwards; it revokes nothing and imposes nothing.
      </Alert>
      <SelectField
        label="Outcome"
        value={outcome}
        onChange={(e) => setOutcome(e.target.value)}
        options={[
          { value: 'VERIFIED', label: 'The evidence satisfies the condition' },
          { value: 'CORRECTION_REQUESTED', label: 'Ask for a correction (a permitted resubmission)' },
          { value: 'NOT_MET', label: 'The evidence does not satisfy the condition' },
        ]}
      />
      <TextAreaField label="Reason (shown to the institution)" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={4000} hint="What you checked and what you found. At least 10 characters." />
      <TextAreaField label="Internal note" optional value={internal} onChange={(e) => setInternal(e.target.value)} maxLength={4000} hint="Never shown to the institution." />
      <ErrorNote e={error} />
      <Button onClick={() => setConfirm(true)} disabled={!outcome || reason.trim().length < 10}>
        {outcome ? label : 'Choose an outcome'}
      </Button>
      <ConfirmDialog
        open={confirm}
        title={label}
        confirmLabel={label}
        danger={outcome === 'NOT_MET'}
        busy={busy}
        onCancel={() => setConfirm(false)}
        onConfirm={async () => {
          if (!latest) return
          const ok = await run(() => postApprovalApi.review(o.id, { outcome, submission_id: latest.id, reason, internal_note: internal.trim() || null, row_version: o.row_version, idempotency_key: key.current }))
          if (ok) {
            setConfirm(false)
            setOutcome('')
            setReason('')
            setInternal('')
            key.current = newKey('rev')
          }
        }}
      >
        <p>This is recorded against {o.reference}, submission {latest?.sequence}, with your name and the time. It cannot be edited afterwards.</p>
      </ConfirmDialog>
    </Section>
  )
}

// ---- the Bureau withdraws or reassigns ------------------------------------------------------------------------------

function BureauActions({ o, run, busy, error }: { o: ObligationDetail; run: Run; busy: boolean; error: ApiError | null }) {
  const [verifiers] = useResource(() => postApprovalApi.verifiers(), [])
  const [who, setWho] = useState('')
  const [why, setWhy] = useState('')
  const [confirm, setConfirm] = useState<'withdraw' | 'reassign' | null>(null)
  return (
    <Section title="Bureau actions">
      <ErrorNote e={error} />
      <TextAreaField label="Reason" value={why} onChange={(e) => setWhy(e.target.value)} maxLength={2000} hint="Required for either action." />
      {verifiers.status === 'ready' && (
        <SelectField label="Assign to another verifier" value={who} onChange={(e) => setWho(e.target.value)} options={verifiers.data.items.filter((v) => v.id !== o.verifier_id).map((v) => ({ value: v.id, label: v.name }))} />
      )}
      <div className="actions">
        <Button variant="secondary" disabled={!who || why.trim().length < 5} onClick={() => setConfirm('reassign')}>
          Reassign verifier
        </Button>
        <Button variant="danger" disabled={why.trim().length < 5} onClick={() => setConfirm('withdraw')}>
          Withdraw (recorded in error)
        </Button>
      </div>
      <ConfirmDialog
        open={confirm !== null}
        title={confirm === 'withdraw' ? 'Withdraw this obligation?' : 'Reassign the verifier?'}
        confirmLabel={confirm === 'withdraw' ? 'Withdraw' : 'Reassign'}
        danger={confirm === 'withdraw'}
        busy={busy}
        onCancel={() => setConfirm(null)}
        onConfirm={async () => {
          const ok = await run(() => (confirm === 'withdraw' ? postApprovalApi.withdraw(o.id, why, o.row_version) : postApprovalApi.reassign(o.id, who, why)))
          if (ok) {
            setConfirm(null)
            setWhy('')
            setWho('')
          }
        }}
      >
        <p>{confirm === 'withdraw' ? 'The institution is told nothing further is required for it. The record and its history are kept.' : 'The new verifier sees this obligation; the previous one no longer does.'}</p>
      </ConfirmDialog>
    </Section>
  )
}

// ---- the page --------------------------------------------------------------------------------------------------------

export default function ObligationPage() {
  const { id = '' } = useParams()
  const { can } = useAuth()
  const [res, reload] = useResource(() => postApprovalApi.obligation(id), [id])
  const [override, setOverride] = useState<ObligationDetail | null>(null)
  const { busy, error, run } = useRunner(setOverride)
  const official = can('compliance:queue')
  return (
    <ResourceView resource={res} reload={reload}>
      {(loaded) => {
        const o = override && override.id === loaded.id ? override : loaded
        return (
          <>
            <PageHeader
              title={o.title}
              description={`${o.reference} · ${o.application.reference} · ${o.application.institution_name}`}
              crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Obligations and deadlines', to: '/app/post-approval' }, { label: o.reference }]}
            />
            <Alert tone="info">{o.notes.penalty}</Alert>
            <Section title="The obligation" aside={<StatusBadge status={o.status} label={o.status_label} />}>
              <Facts>
                <Fact label="Condition">{o.condition_text}</Fact>
                <Fact label="Where it appears">{o.condition_ref ?? '—'}</Fact>
                <Fact label="Evidence required">{o.required_evidence}</Fact>
                <Fact label="From the decision">
                  {o.decision.outcome_label}
                  {o.decision.version_number ? ` · version ${o.decision.version_number}` : ''}
                </Fact>
                <Fact label="Due date">
                  {o.due.basis === 'NONE' ? (
                    'No due date was set; none is assumed.'
                  ) : (
                    <>
                      <DueLine due={o.effective_due_at ?? o.due_at} timing={o.timing} />
                      <div className="note">
                        {o.due.basis === 'POLICY' ? `Computed from the configured policy${o.due.policy && 'duration_value' in o.due.policy ? ` (${String(o.due.policy.duration_value)} ${String(o.due.policy.duration_unit)})` : ''}.` : `Specified in the decision: ${o.due.authority}`}
                      </div>
                    </>
                  )}
                </Fact>
                {official && <Fact label="Assigned verifier">{o.verifier}</Fact>}
                {official && o.defined_by && <Fact label="Recorded by">{o.defined_by}</Fact>}
              </Facts>
              {o.withdrawn_reason && <Alert tone="warning">Withdrawn: {o.withdrawn_reason}</Alert>}
            </Section>

            {o.can.submit && <SubmitEvidence o={o} run={run} busy={busy} error={error} />}
            {o.can.review && <ReviewForm o={o} run={run} busy={busy} error={error} />}

            <Section title="Submissions and verification">
              {o.submissions_detail.length === 0 ? (
                <p className="note">No evidence has been submitted yet.</p>
              ) : (
                <ol className="plain-list">
                  {o.submissions_detail.map((s) => (
                    <li key={s.id} className="notice-card" data-status={s.review ? 'CLOSED' : 'OPEN'}>
                      <p>
                        <strong>Submission {s.sequence}</strong> · {s.submitted_by} · {formatDate(s.submitted_at)}{' '}
                        {s.review ? <Badge tone={s.review.outcome === 'VERIFIED' ? 'success' : s.review.outcome === 'NOT_MET' ? 'error' : 'warning'}>{humanise(s.review.outcome)}</Badge> : <Badge tone="info">Awaiting verification</Badge>}
                      </p>
                      {s.note && <blockquote>{s.note}</blockquote>}
                      <ul className="plain-list">
                        {s.documents.map((d) => (
                          <li key={d.id}>
                            <a className="link" href={d.download}>
                              <Download size={14} aria-hidden="true" /> {d.label} · v{d.version_number}
                            </a>{' '}
                            <span className="muted">
                              {d.filename} · {fileSize(d.size_bytes)} · {d.scan_status === 'NOT_SCANNED' ? 'not malware-scanned' : d.scan_status.toLowerCase()}
                            </span>
                            <div className="mono muted">sha256 {d.sha256.slice(0, 16)}…</div>
                          </li>
                        ))}
                      </ul>
                      {s.review && (
                        <>
                          <p className="eyebrow">
                            {s.review.reviewed_by} · {formatDate(s.review.reviewed_at)}
                          </p>
                          <blockquote>{s.review.reason}</blockquote>
                          {official && s.review.internal_note && <p className="note">Internal note: {s.review.internal_note}</p>}
                        </>
                      )}
                    </li>
                  ))}
                </ol>
              )}
            </Section>

            {o.can.withdraw && <BureauActions o={o} run={run} busy={busy} error={error} />}

            <Section title="History">
              <ol className="history">
                {o.events.map((e, i) => (
                  <li key={`${e.at}-${i}`} className="history__item">
                    <span className="mono history__at">{formatDate(e.at)}</span>
                    <span>
                      <strong>{humanise(e.action)}</strong>
                      {e.actor ? ` — ${e.actor}` : ''}
                      {e.from_status && e.to_status && e.from_status !== e.to_status ? <span className="muted"> ({humanise(e.from_status)} → {humanise(e.to_status)})</span> : null}
                    </span>
                  </li>
                ))}
              </ol>
            </Section>
          </>
        )
      }}
    </ResourceView>
  )
}
