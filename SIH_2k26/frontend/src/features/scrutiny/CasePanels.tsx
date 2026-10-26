import { useEffect, useState } from 'react'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Alert, Badge } from '../../components/ui/feedback'
import { CheckboxField, SelectField, TextAreaField } from '../../components/ui/Field'
import { DemoBanner } from '../../components/ui/Banners'
import type { ApiError } from '../../lib/api'
import { formatDate, humanise } from '../../lib/format'
import { useResource } from '../../lib/useResource'
import { Section } from '../applications/parts'
import { CaseStateBadge, Fact, Facts, RuleTag, StageTag } from './parts'
import { scrutinyApi } from './scrutinyApi'
import type { CaseDetail } from './types'

type Run = (fn: () => Promise<CaseDetail>) => Promise<boolean>

const errorNote = (e: ApiError | null) => (e ? <Alert tone="error">{e.message}</Alert> : null)

// ---- summary ----------------------------------------------------------------------------------------------------

export function SummaryPanel({ detail }: { detail: CaseDetail }) {
  const q = detail.queue
  return (
    <Section title="Application summary" aside={<CaseStateBadge state={q.state} label={q.state_label} />}>
      {q.rule_version.demo_banner && <DemoBanner text={q.rule_version.demo_banner} />}
      <Facts>
        <Fact label="Application">
          <span className="mono">{q.reference}</span>
        </Fact>
        <Fact label="Institution">{q.institution_name}</Fact>
        <Fact label="Category / year">
          {q.category_name} · <span className="mono">{q.academic_year}</span>
        </Fact>
        <Fact label="Submitted">{formatDate(q.submitted_at)}</Fact>
        <Fact label="Rule version it was submitted under">
          <RuleTag rule={q.rule_version} />
        </Fact>
        <Fact label="Current round">{q.round_number ? `Round ${q.round_number} (${q.round_kind === 'RE_SCRUTINY' ? 're-scrutiny' : 'initial scrutiny'})` : '—'}</Fact>
        <Fact label="Assigned">{q.assignees.length ? q.assignees.join(', ') : 'No one yet'}</Fact>
        <Fact label="Response due">{q.due_at ? <><span className="mono">{formatDate(q.due_at)}</span>{q.due_basis ? ` — ${q.due_basis}` : ''}</> : 'No deadline established'}</Fact>
      </Facts>
      <ul className="plain-list outstanding">
        {q.outstanding_actions.map((a) => (
          <li key={a}>{a}</li>
        ))}
      </ul>
      <p className="note">{detail.not_an_approval}</p>
      {detail.checklist.length > 0 && (
        <details className="panel panel--collapsible">
          <summary>Submitted details and requirement checklist ({detail.checklist.length})</summary>
          <div className="table-wrap">
            <table className="table">
              <caption className="sr-only">Requirements</caption>
              <thead>
                <tr>
                  <th scope="col">Requirement</th>
                  <th scope="col">Required</th>
                  <th scope="col">Submitted</th>
                </tr>
              </thead>
              <tbody>
                {detail.checklist.map((c) => (
                  <tr key={c.item_key}>
                    <td>
                      {c.label}
                      {c.clause_ref && <div className="mono muted">{c.clause_ref}</div>}
                    </td>
                    <td>{c.required ? 'Required' : 'Optional'}</td>
                    <td>
                      {c.item_type === 'document_upload' ? (c.document_version ? <span className="mono">{c.document_filename} · version {c.document_version}</span> : <span className="muted">No file submitted</span>) : c.answer === null || c.answer === undefined ? <span className="muted">—</span> : String(c.answer)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </Section>
  )
}

// ---- assignment (coordinator) ---------------------------------------------------------------------------------------

export function AssignmentPanel({ detail, run, busy, error }: { detail: CaseDetail; run: Run; busy: boolean; error: ApiError | null }) {
  const canAssign = detail.allowed_actions.includes('assign') || detail.allowed_actions.includes('reassign')
  const first = detail.allowed_actions.includes('assign')
  const [list] = useResource(() => (canAssign ? scrutinyApi.assignable() : Promise.resolve([])), [canAssign])
  const [pick, setPick] = useState('')
  const [reason, setReason] = useState('')
  const [open, setOpen] = useState(false)
  const sel = detail.selection
  return (
    <Section title="Assignment">
      <Alert tone={sel.available ? 'warning' : 'info'} title={sel.label}>
        {sel.notice}
      </Alert>
      {detail.assignments.length === 0 ? (
        <p className="note">No reviewer has been assigned.</p>
      ) : (
        <ul className="plain-list assignments">
          {detail.assignments.map((a) => (
            <li key={a.id}>
              <strong>{a.user_name}</strong> {a.is_demo && <Badge tone="warning">Demonstration assignment</Badge>} {a.revoked_at && <Badge tone="neutral">Replaced</Badge>}
              <div className="mono muted">
                {humanise(a.source)} · recorded by {a.made_by} · {formatDate(a.created_at)}
                {a.revoked_at ? ` · replaced ${formatDate(a.revoked_at)}` : ''}
              </div>
              {a.reason && <div className="note">Reason: {a.reason}</div>}
              {a.revoked_reason && a.revoked_at && <div className="note">Replaced because: {a.revoked_reason}</div>}
            </li>
          ))}
        </ul>
      )}
      {canAssign && (
        <div className="action-bar">
          <Button variant="secondary" onClick={() => setOpen(true)}>
            {first ? 'Assign a reviewer…' : 'Change the reviewer…'}
          </Button>
        </div>
      )}
      {!canAssign && !sel.available && detail.assignments.length === 0 && <p className="note">Assignments cannot be recorded here until an authorised selection interface exists.</p>}
      <ConfirmDialog
        open={open}
        title={first ? 'Assign a reviewer' : 'Change the reviewer'}
        confirmLabel={first ? 'Record assignment' : 'Replace reviewer'}
        busy={busy}
        confirmDisabled={!pick || (!first && reason.trim().length < 3)}
        onCancel={() => setOpen(false)}
        onConfirm={async () => {
          if (await run(() => scrutinyApi.assign(detail.queue.id, pick, reason.trim() || null, detail.queue.row_version))) setOpen(false)
        }}
      >
        <Alert tone="warning">{sel.notice}</Alert>
        <SelectField
          label="Reviewer"
          value={pick}
          onChange={(e) => setPick(e.target.value)}
          options={(list.status === 'ready' ? list.data : []).filter((u) => !detail.assignments.some((a) => !a.revoked_at && a.user_id === u.id)).map((u) => ({ value: u.id, label: `${u.name} (${u.email}) — ${u.active_cases} active case${u.active_cases === 1 ? '' : 's'}` }))}
        />
        <TextAreaField label={first ? 'Note (optional)' : 'Reason for the change'} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
        {errorNote(error)}
      </ConfirmDialog>
    </Section>
  )
}

// ---- completion (reviewer) ------------------------------------------------------------------------------------------

export function CompletionPanel({ detail, run, busy, error }: { detail: CaseDetail; run: Run; busy: boolean; error: ApiError | null }) {
  const [summary, setSummary] = useState('')
  const [ack, setAck] = useState(false)
  const [open, setOpen] = useState(false)
  if (!detail.allowed_actions.includes('complete')) return null
  const pending = detail.findings.filter((f) => f.state === 'OPEN' || f.state === 'NOTICED').length
  const unresolved = detail.findings.filter((f) => f.state === 'UNRESOLVED').length
  const inRescrutiny = detail.queue.round_kind === 'RE_SCRUTINY'
  return (
    <Section title={inRescrutiny ? 'Complete re-scrutiny' : 'Complete scrutiny'}>
      <p className="note">
        Completing records that this stage is finished. <strong>It is not an approval.</strong> The case then moves to the next stage its own approved route names; if the route cannot be applied unambiguously, it goes to the configuration queue instead.
      </p>
      {pending > 0 && <Alert tone="warning">{pending} finding(s) are still open or awaiting a response. Conclude each one, or prepare a notice, before completing.</Alert>}
      {unresolved > 0 && <Alert tone="warning">{unresolved} finding(s) remain unresolved. They will be recorded as unresolved and carried forward with the case.</Alert>}
      <TextAreaField label="Summary of the outcome (kept with the case)" rows={3} value={summary} onChange={(e) => setSummary(e.target.value)} />
      {unresolved > 0 && <CheckboxField label="I am completing with these findings recorded as unresolved." checked={ack} onChange={(e) => setAck(e.target.checked)} />}
      <div className="action-bar">
        <Button disabled={pending > 0 || summary.trim().length < 5 || (unresolved > 0 && !ack)} onClick={() => setOpen(true)}>
          {inRescrutiny ? 'Complete re-scrutiny…' : 'Complete scrutiny…'}
        </Button>
      </div>
      <ConfirmDialog
        open={open}
        title="Record this stage as complete?"
        confirmLabel="Complete"
        busy={busy}
        onCancel={() => setOpen(false)}
        onConfirm={async () => {
          if (await run(() => scrutinyApi.complete(detail.queue.id, detail.queue.row_version, summary.trim(), ack))) setOpen(false)
        }}
      >
        <p>No further findings or notices can be added to this stage afterwards. The application is not approved or rejected by this step.</p>
        {errorNote(error)}
      </ConfirmDialog>
    </Section>
  )
}

// ---- hand-off -----------------------------------------------------------------------------------------------------

export function HandoffPanel({ detail, run, busy, error }: { detail: CaseDetail; run: Run; busy: boolean; error: ApiError | null }) {
  const h = detail.handoff
  const [stage, setStage] = useState('')
  const [reason, setReason] = useState('')
  const [open, setOpen] = useState(false)
  if (!h) return null
  const canDetermine = detail.allowed_actions.includes('determine_handoff')
  const single = h.options.length === 1
  return (
    <Section title="Next stage">
      {h.status === 'EXCEPTION' ? (
        <Alert tone="error" title="Configuration exception">
          The approved route could not be applied, so the next stage is not guessed. {h.reason} This case needs configuration review.
        </Alert>
      ) : h.status === 'AWAITING_DETERMINATION' ? (
        <Alert tone="warning" title="The next stage needs a determination">
          {h.reason}
        </Alert>
      ) : h.status === 'NO_FURTHER_STAGE' ? (
        <Alert tone="info" title="No further stage is configured">
          {h.determination_reason ? `Determined by ${h.determined_by}: ${h.determination_reason}` : h.reason}
        </Alert>
      ) : (
        <Alert tone="success" title="Ready for next stage">
          <StageTag kind="next-stage" /> <strong>{h.to_stage_label}</strong> — queued. {h.module_available ? '' : 'That stage’s workflow is not available in this release, so nothing has been started.'}
          {h.determination_reason && <> Determined by {h.determined_by}: {h.determination_reason}</>}
        </Alert>
      )}
      {h.options.length > 0 && (
        <ul className="plain-list">
          {h.options.map((o) => (
            <li key={o.stage_key}>
              <strong>{o.label}</strong> {o.is_conditional && <Badge tone="warning">Conditional</Badge>}
              {o.condition_description && <div className="note">{o.condition_description}</div>}
            </li>
          ))}
        </ul>
      )}
      {canDetermine && (
        <div className="action-bar">
          <Button onClick={() => setOpen(true)}>Record the determination…</Button>
        </div>
      )}
      <ConfirmDialog
        open={open}
        title="Determine the next stage"
        confirmLabel="Record determination"
        busy={busy}
        confirmDisabled={reason.trim().length < 5 || (!single && !stage)}
        onCancel={() => setOpen(false)}
        onConfirm={async () => {
          const key = stage === '__none__' ? null : stage || (single ? (h.options[0]?.stage_key ?? null) : null)
          if (await run(() => scrutinyApi.determine(detail.queue.id, key, reason.trim()))) setOpen(false)
        }}
      >
        <p className="note">Choose only among the stages this application’s approved route allows. The decision, your name and your reason are recorded.</p>
        <SelectField
          label="Next stage"
          value={stage}
          onChange={(e) => setStage(e.target.value)}
          options={[...h.options.map((o) => ({ value: o.stage_key, label: o.label })), ...(single && h.options[0]?.is_conditional ?[{ value: '__none__', label: 'None — the condition does not apply' }] : [])]}
          placeholder={single ? 'Apply the stage named above' : 'Select…'}
        />
        <TextAreaField label="Reason" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
        {errorNote(error)}
      </ConfirmDialog>
    </Section>
  )
}

// ---- rounds, notes and history ---------------------------------------------------------------------------------------

function NotesEditor({ detail, run, busy }: { detail: CaseDetail; run: Run; busy: boolean }) {
  const round = detail.rounds.find((r) => r.status === 'OPEN')
  const [text, setText] = useState(round?.review_notes ?? '')
  useEffect(() => setText(round?.review_notes ?? ''), [round?.id, round?.row_version]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!round || !detail.allowed_actions.includes('save_notes')) return null
  return (
    <div className="notes">
      <TextAreaField label="Working notes for this round (internal — saved with the case, never shown to the institution)" rows={4} value={text} onChange={(e) => setText(e.target.value)} />
      <div className="action-bar">
        <Button size="sm" variant="secondary" loading={busy} onClick={() => void run(() => scrutinyApi.saveNotes(detail.queue.id, text, round.row_version))}>
          Save progress
        </Button>
        {round.review_notes_at && <span className="muted">Last saved {formatDate(round.review_notes_at)}</span>}
      </div>
    </div>
  )
}

export function HistoryPanel({ detail, run, busy }: { detail: CaseDetail; run: Run; busy: boolean }) {
  return (
    <>
      <Section title="Rounds">
        <ol className="round-list">
          {detail.rounds.map((r) => (
            <li key={r.id}>
              <strong>Round {r.number}</strong> — {r.kind === 'RE_SCRUTINY' ? 're-scrutiny' : 'initial scrutiny'} <Badge tone={r.status === 'OPEN' ? 'info' : 'neutral'}>{r.status === 'OPEN' ? 'Open' : r.status === 'NOTICE_ISSUED' ? 'Ended with a notice' : 'Completed'}</Badge>
              <div className="mono muted">
                Opened {formatDate(r.opened_at)}
                {r.started_at ? ` · started ${formatDate(r.started_at)}` : ''}
                {r.closed_at ? ` · closed ${formatDate(r.closed_at)}` : ''}
                {r.triggered_by_notice ? ` · responds to ${r.triggered_by_notice}` : ''}
              </div>
              {r.outcome_reason && <div className="note">{r.outcome_reason}</div>}
              {r.review_notes && <div className="note"><strong>Working notes (internal):</strong> {r.review_notes}</div>}
            </li>
          ))}
        </ol>
        <NotesEditor detail={detail} run={run} busy={busy} />
      </Section>
      <Section title="Case history">
        <ol className="history">
          {[...detail.events].reverse().map((e) => (
            <li key={e.id} className="history__item">
              <span className="mono history__at">{formatDate(e.at)}</span>
              <span>
                <strong>{humanise(e.action)}</strong> — {e.actor}
                {e.from_state && e.to_state && e.from_state !== e.to_state ? <span className="muted"> ({humanise(e.from_state.toLowerCase())} → {humanise(e.to_state.toLowerCase())})</span> : null}
                {e.reason && <em> — {e.reason}</em>}
              </span>
            </li>
          ))}
        </ol>
      </Section>
    </>
  )
}
