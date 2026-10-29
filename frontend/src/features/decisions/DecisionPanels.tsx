import { useState } from 'react'
import { Download } from 'lucide-react'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Alert, Badge } from '../../components/ui/feedback'
import { CheckboxField, SelectField, TextAreaField, TextField } from '../../components/ui/Field'
import { DemoBanner } from '../../components/ui/Banners'
import type { ApiError } from '../../lib/api'
import { formatDate, humanise } from '../../lib/format'
import { Section } from '../applications/parts'
import { newKey } from '../committee/committeeApi'
import { EventList } from '../committee/parts'
import { Fact, Facts } from '../scrutiny/parts'
import { decisionsApi } from './decisionsApi'
import { DecisionStateBadge } from './DecisionQueuePage'
import type { CouncilOut, DecisionCaseDetail, EcDecisionOut, SyncView } from './types'

export interface Ctx {
  detail: DecisionCaseDetail
  run: (fn: () => Promise<DecisionCaseDetail>) => Promise<boolean>
  busy: boolean
  error: ApiError | null
}

const errorNote = (e: ApiError | null) => (e ? <Alert tone="error">{e.message}</Alert> : null)
const fileSize = (n: number) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`)

/** What the integration with AICTE's portal has actually confirmed. Never claims more than the provider confirmed. */
export function SyncNote({ sync, what }: { sync: SyncView | null; what: string }) {
  if (!sync) return null
  const tone = sync.status === 'SYNCED' ? 'success' : sync.status === 'FAILED' ? 'error' : sync.status === 'SYNC_PENDING' || sync.status === 'RECONCILIATION_REQUIRED' ? 'warning' : 'info'
  const unconfirmed = sync.status === 'SYNC_PENDING' || sync.status === 'FAILED' || sync.status === 'RECONCILIATION_REQUIRED'
  return (
    <Alert tone={tone} title={`AICTE portal — ${what}: ${sync.status === 'SYNCED' ? 'Confirmed' : humanise(sync.status)}`}>
      {sync.message}
      {unconfirmed && sync.last_error && <div className="mono muted">Last problem: {sync.last_error} · attempts {sync.attempts}</div>}
      {sync.status === 'SYNC_PENDING' && sync.next_attempt_at && <div className="mono muted">Next attempt not before {formatDate(sync.next_attempt_at)}</div>}
      {sync.status === 'SYNCED' && sync.external_ref && <div className="mono muted">Portal reference {sync.external_ref}</div>}
    </Alert>
  )
}

function DecisionBlock({ d, live }: { d: EcDecisionOut; live: boolean }) {
  return (
    <li className="notice-card" data-status={live ? 'OPEN' : 'CLOSED'}>
      <p>
        <Badge tone={live ? 'info' : 'neutral'}>{live ? 'Current' : 'Superseded'}</Badge> <strong>{d.outcome_label}</strong> · version {d.version_number} {d.kind === 'CORRECTION' && <Badge tone="warning">Correction</Badge>}
      </p>
      <p className="muted">
        Recorded by {d.decided_by} on {formatDate(d.decided_at)}
        {d.meeting_reference ? ` · ${d.meeting_reference}` : ''}
      </p>
      <p className="eyebrow">Reasons</p>
      <blockquote>{d.reasons}</blockquote>
      {d.speaking_order && (
        <>
          <p className="eyebrow">Speaking order</p>
          <blockquote>{d.speaking_order}</blockquote>
        </>
      )}
      {d.correction_reason && <p className="note">Correction reason: {d.correction_reason}</p>}
      <p className="mono muted">content hash {d.content_hash.slice(0, 16)}…</p>
    </li>
  )
}

// ---- overview --------------------------------------------------------------------------------------------

export function OverviewPanel({ detail }: { detail: DecisionCaseDetail }) {
  const a = detail.application
  const sup = detail.supporting
  const pol = detail.policy
  return (
    <>
      <Section title="Case summary" aside={<DecisionStateBadge state={detail.state} label={detail.state_label} />}>
        {pol?.banner && <DemoBanner text={pol.banner} />}
        <Facts>
          <Fact label="Application">
            <span className="mono">{a.reference}</span>
          </Fact>
          <Fact label="Institution">{a.institution_name}</Fact>
          <Fact label="Category / year">
            {a.category_name} · <span className="mono">{a.academic_year}</span>
          </Fact>
          <Fact label="Entered the decision stage">{formatDate(detail.opened_at)}</Fact>
          <Fact label="Mandatory stages on the route">{detail.route.stages.length ? detail.route.stages.map(humanise).join(' → ') : '—'}</Fact>
          <Fact label="Decision configuration">
            {pol ? (
              <>
                {pol.title} <span className="mono">v{pol.version}</span> · {pol.classification === 'DEMO' ? 'DEMO ONLY' : 'official'}
              </>
            ) : (
              'None applies'
            )}
          </Fact>
        </Facts>
        {detail.blockers.length > 0 && (
          <Alert tone="warning" title="A decision cannot be recorded yet">
            <ul className="plain-list">
              {detail.blockers.map((b) => (
                <li key={b.code}>{b.message}</li>
              ))}
            </ul>
            <p className="note">Nothing is assumed to keep the case moving. The reasons are on record for the rule configuration reviewers.</p>
          </Alert>
        )}
        {pol && (
          <details className="panel panel--collapsible">
            <summary>Sources behind this configuration ({pol.sources.length})</summary>
            <ul className="plain-list">
              {pol.sources.map((s) => (
                <li key={s.title}>
                  {s.title} — <span className="mono">{s.clause_ref}</span> · {humanise(s.verification_status)}
                </li>
              ))}
            </ul>
          </details>
        )}
      </Section>
      <Section title="What the decision-makers are asked to consider">
        <Alert tone="info">{detail.not_a_decision}</Alert>
        <ul className="plain-list">
          <li>
            <strong>Scrutiny.</strong> {sup.scrutiny.completion?.outcome ? humanise(sup.scrutiny.completion.outcome) : 'Not recorded'} · {sup.scrutiny.findings.length} finding{sup.scrutiny.findings.length === 1 ? '' : 's'} (not an approval)
          </li>
          <li>
            <strong>Expert visit (EVC) report.</strong> {sup.evc ? (sup.evc.record ? `Version ${sup.evc.record.version}, hash ${sup.evc.record.content_hash.slice(0, 12)}…` : 'The stage ran; no report is recorded') : 'Not on this route'}
          </li>
          <li>
            <strong>SHC recommendation.</strong> {sup.shc ? (sup.shc.record ? `Version ${sup.shc.record.version}${sup.shc.record.option ? ` — ${humanise(sup.shc.record.option)}` : ''}` : 'The stage ran; no recommendation is recorded') : 'Not on this route'}
          </li>
          <li>
            <strong>Regulation Bureau observations.</strong> {sup.bureau ? `${sup.bureau.observation_ids.length} recorded` : 'None recorded'}
          </li>
          <li>
            <strong>Submitted documents.</strong> {sup.documents.length} current version{sup.documents.length === 1 ? '' : 's'}
          </li>
        </ul>
        <p className="note">A report or recommendation is an input to the decision. It never decides anything on its own.</p>
      </Section>
    </>
  )
}

// ---- Regulation Bureau observations -----------------------------------------------------------------

export function ObservationsPanel({ ctx }: { ctx: Ctx }) {
  const { detail, run, busy, error } = ctx
  const [text, setText] = useState('')
  const [key, setKey] = useState(newKey)
  const can = detail.allowed_actions.includes('observe')
  return (
    <Section title={`Regulation Bureau observations (${detail.observations.length})`}>
      <p className="note">Observations are the Bureau’s views for the Executive Committee to consider. They are recorded as written, in order, and never edited; a later observation adds to them.</p>
      {detail.observations.length === 0 ? (
        <p className="note">None recorded yet.{detail.policy?.decision?.bureau_observation_required ? ' The configuration requires at least one before a decision can be recorded.' : ''}</p>
      ) : (
        <ul className="plain-list">
          {detail.observations.map((o) => (
            <li key={o.id}>
              <strong>Observation {o.sequence}</strong> — {o.by}, {formatDate(o.at)}
              <blockquote>{o.text}</blockquote>
            </li>
          ))}
        </ul>
      )}
      {can && (
        <form
          aria-label="Record an observation"
          onSubmit={(e) => {
            e.preventDefault()
            void run(() => decisionsApi.observe(detail.id, text.trim(), key)).then((ok) => {
              if (ok) {
                setText('')
                setKey(newKey())
              }
            })
          }}
        >
          <TextAreaField label="Observation" value={text} onChange={(e) => setText(e.target.value)} rows={4} />
          {errorNote(error)}
          <Button type="submit" loading={busy} disabled={text.trim().length < 5}>
            Record the observation
          </Button>
        </form>
      )}
    </Section>
  )
}

// ---- Executive Committee decision ----------------------------------------------------------------------

function DecisionForm({ ctx, correcting, onDone }: { ctx: Ctx; correcting: boolean; onDone: () => void }) {
  const { detail, run, busy, error } = ctx
  const outcomes = detail.policy?.decision?.outcomes ?? []
  const [outcome, setOutcome] = useState('')
  const [reasons, setReasons] = useState('')
  const [speaking, setSpeaking] = useState('')
  const [meeting, setMeeting] = useState('')
  const [why, setWhy] = useState('')
  const [considered, setConsidered] = useState<string[]>([])
  const [confirm, setConfirm] = useState(false)
  const [key, setKey] = useState(newKey)
  const chosen = outcomes.find((o) => o.key === outcome)
  const needsSpeaking = !!chosen?.requires_speaking_order
  const labels: Record<string, string> = { scrutiny: 'the scrutiny record', evc: 'the expert visit (EVC) report', shc: 'the SHC recommendation', bureau: 'the Regulation Bureau’s observations' }
  const complete = !!outcome && reasons.trim().length >= 10 && (!needsSpeaking || speaking.trim().length >= 10) && detail.considerable.every((k) => considered.includes(k)) && (!correcting || why.trim().length >= 5)
  const submit = async () => {
    const body = { outcome, reasons: reasons.trim(), speaking_order: speaking.trim() || null, meeting_reference: meeting.trim() || null, considered, idempotency_key: key, row_version: detail.row_version }
    const ok = await run(() => (correcting ? decisionsApi.correct(detail.id, { ...body, correction_reason: why.trim() }) : decisionsApi.decide(detail.id, body)))
    setConfirm(false)
    if (ok) {
      setKey(newKey())
      onDone()
    }
  }
  return (
    <>
    <form
      className="panel"
      aria-label={correcting ? 'Record a correction' : 'Record the decision'}
      onSubmit={(e) => {
        e.preventDefault()
        setConfirm(true)
      }}
    >
      <SelectField label="Outcome" value={outcome} onChange={(e) => setOutcome(e.target.value)} options={outcomes.map((o) => ({ value: o.key, label: o.label }))} placeholder="Choose the outcome the Committee decided" />
      <TextAreaField label="Reasons" value={reasons} onChange={(e) => setReasons(e.target.value)} rows={4} hint="The Committee’s reasons, as decided. This system suggests no outcome and no wording." />
      <TextAreaField label="Speaking order" optional={!needsSpeaking} value={speaking} onChange={(e) => setSpeaking(e.target.value)} rows={4} hint={needsSpeaking ? 'Required for this outcome (APH Ch. I cl. 1.10(b)).' : undefined} />
      <TextField label="Meeting reference" optional value={meeting} onChange={(e) => setMeeting(e.target.value)} />
      <fieldset className="field">
        <legend className="field__label">The Committee confirms it considered</legend>
        {detail.considerable.map((k) => (
          <CheckboxField key={k} label={labels[k] ?? k} checked={considered.includes(k)} onChange={(e) => setConsidered((c) => (e.target.checked ? [...c, k] : c.filter((x) => x !== k)))} />
        ))}
      </fieldset>
      {correcting && <TextAreaField label="Why this correction is needed" value={why} onChange={(e) => setWhy(e.target.value)} rows={2} hint="The original decision is kept unchanged and shown beside this correction." />}
      {errorNote(error)}
      <div className="action-bar">
        <Button type="submit" disabled={!complete} loading={busy}>
          {correcting ? 'Record the correction…' : 'Record the decision…'}
        </Button>
        <Button variant="quiet" onClick={onDone}>
          Cancel
        </Button>
      </div>
      </form>
      <ConfirmDialog open={confirm} title={correcting ? 'Record the correction' : 'Record the Executive Committee’s decision'} confirmLabel="Record" busy={busy} onCancel={() => setConfirm(false)} onConfirm={submit}>
        <p>
          You are recording that the Executive Committee decided <strong>{chosen?.label}</strong>. The record keeps who recorded it, when, the rule version and the records considered. It can be corrected later only by a new, reasoned correction; it cannot be edited or deleted.
        </p>
      </ConfirmDialog>
    </>
  )
}

export function DecisionPanel({ ctx }: { ctx: Ctx }) {
  const { detail } = ctx
  const a = detail.allowed_actions
  const [form, setForm] = useState<null | 'decide' | 'correct'>(null)
  const current = detail.current_decision
  return (
    <Section title="Executive Committee decision">
      <Alert tone="info">The Executive Committee decides. This system records the decision, who recorded it and the records it rests on; it never decides, suggests or defaults an outcome.</Alert>
      {detail.decisions.length === 0 ? (
        <p className="note">No decision has been recorded.</p>
      ) : (
        <ul className="notice-list">
          {[...detail.decisions].reverse().map((d) => (
            <DecisionBlock key={d.id} d={d} live={d.id === current?.id} />
          ))}
        </ul>
      )}
      {form ? (
        <DecisionForm ctx={ctx} correcting={form === 'correct'} onDone={() => setForm(null)} />
      ) : (
        <div className="action-bar">
          {a.includes('record_decision') && <Button onClick={() => setForm('decide')}>Record the decision…</Button>}
          {a.includes('correct_decision') && (
            <Button variant="secondary" onClick={() => setForm('correct')}>
              Record a correction…
            </Button>
          )}
        </div>
      )}
      {detail.state === 'AWAITING_OBSERVATIONS' && <p className="note">The configuration requires the Regulation Bureau’s observations first.</p>}
    </Section>
  )
}

// ---- issuing the outcome ------------------------------------------------------------------------------------

export function IssuePanel({ ctx }: { ctx: Ctx }) {
  const { detail, run, busy, error } = ctx
  const [ref, setRef] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [key, setKey] = useState(newKey)
  const can = detail.allowed_actions.includes('issue_outcome')
  return (
    <Section title="Official outcome issued to the institution">
      <p className="note">Issuing records the official reference (and the document, if there is one) against the recorded decision and shows it to the institution. This system does not draft letter text, signatures or seals.</p>
      {detail.issuances.length === 0 ? (
        <p className="note">Nothing has been issued.</p>
      ) : (
        <ul className="plain-list">
          {detail.issuances.map((i) => (
            <li key={i.id}>
              <Badge tone={i.status === 'ISSUED' ? 'success' : 'neutral'}>{i.status === 'ISSUED' ? 'Current' : 'Replaced'}</Badge> issued {formatDate(i.issued_at)}
              {i.document_reference && <> · reference <span className="mono">{i.document_reference}</span></>}
              {i.file && (
                <>
                  {' '}
                  ·{' '}
                  <a className="link" href={i.file.download}>
                    <Download size={14} aria-hidden="true" /> {i.file.filename}
                  </a>{' '}
                  <span className="muted">({fileSize(i.file.size_bytes)})</span>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      <SyncNote sync={detail.issuance_sync} what="the issued outcome" />
      {can && (
        <form
          aria-label="Issue the outcome"
          onSubmit={(e) => {
            e.preventDefault()
            void run(() => decisionsApi.issue(detail.id, { document_reference: ref.trim(), file, idempotency_key: key, row_version: detail.row_version })).then((ok) => {
              if (ok) {
                setRef('')
                setFile(null)
                setKey(newKey())
              }
            })
          }}
        >
          <TextField label="Official document reference" optional value={ref} onChange={(e) => setRef(e.target.value)} hint="The reference of the official letter. A reference or a file (or both) is needed." />
          <div className="field">
            <label className="field__label" htmlFor="issue-file">
              Official document (PDF)
            </label>
            <input id="issue-file" type="file" accept="application/pdf" className="field__control" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </div>
          {errorNote(error)}
          <Button type="submit" loading={busy} disabled={!ref.trim() && !file}>
            Issue the outcome
          </Button>
        </form>
      )}
    </Section>
  )
}

// ---- the Council ------------------------------------------------------------------------------------------------

function CouncilForm({ ctx, kind, onDone }: { ctx: Ctx; kind: 'ratification' | 'appeal'; onDone: () => void }) {
  const { detail, run, busy, error } = ctx
  const pol = detail.policy
  const options = kind === 'ratification' ? (pol?.ratification?.outcomes ?? []).map((o) => ({ key: o.key, label: o.label, speaking: false })) : (pol?.appeal?.council_outcomes ?? []).map((o) => ({ key: o.key, label: o.label, speaking: !!o.requires_speaking_order }))
  const live = kind === 'ratification' ? detail.live_ratification : detail.appeal?.live_council_decision
  const [outcome, setOutcome] = useState('')
  const [reasons, setReasons] = useState('')
  const [speaking, setSpeaking] = useState('')
  const [reference, setReference] = useState('')
  const [why, setWhy] = useState('')
  const [key, setKey] = useState(newKey)
  const [confirm, setConfirm] = useState(false)
  const chosen = options.find((o) => o.key === outcome)
  const ok = !!outcome && reasons.trim().length >= 5 && (!chosen?.speaking || speaking.trim().length >= 10) && (!live || why.trim().length >= 5)
  const submit = async () => {
    const common = { outcome, reasons: reasons.trim(), reference: reference.trim() || null, correction_reason: live ? why.trim() : null, idempotency_key: key }
    const done = await run(() => (kind === 'ratification' ? decisionsApi.ratify(detail.id, common) : decisionsApi.councilDecision(detail.appeal!.id, { ...common, speaking_order: speaking.trim() || null })))
    setConfirm(false)
    if (done) {
      setKey(newKey())
      onDone()
    }
  }
  return (
    <>
    <form
      className="panel"
      aria-label={kind === 'ratification' ? 'Record the Council’s step' : 'Record the Council’s decision on the appeal'}
      onSubmit={(e) => {
        e.preventDefault()
        setConfirm(true)
      }}
    >
      <SelectField label="Outcome" value={outcome} onChange={(e) => setOutcome(e.target.value)} options={options.map((o) => ({ value: o.key, label: o.label }))} placeholder="Choose the Council’s outcome" />
      <TextAreaField label="Reasons" value={reasons} onChange={(e) => setReasons(e.target.value)} rows={3} />
      {kind === 'appeal' && <TextAreaField label="Speaking order" optional={!chosen?.speaking} value={speaking} onChange={(e) => setSpeaking(e.target.value)} rows={3} />}
      <TextField label="Council minute / reference" optional value={reference} onChange={(e) => setReference(e.target.value)} />
      {live && <TextAreaField label="Why this correction is needed" value={why} onChange={(e) => setWhy(e.target.value)} rows={2} hint="The earlier record is kept unchanged." />}
      {errorNote(error)}
      <div className="action-bar">
        <Button type="submit" loading={busy} disabled={!ok}>
          {live ? 'Record the correction…' : 'Record…'}
        </Button>
        <Button variant="quiet" onClick={onDone}>
          Cancel
        </Button>
      </div>
      </form>
      <ConfirmDialog open={confirm} title="Record the Council’s step" confirmLabel="Record" busy={busy} onCancel={() => setConfirm(false)} onConfirm={submit}>
        <p>You are recording what the Council decided: <strong>{chosen?.label}</strong>. It is kept unchanged; a later correction adds a new record.</p>
      </ConfirmDialog>
    </>
  )
}

function CouncilList({ rows }: { rows: CouncilOut[] }) {
  return (
    <ul className="notice-list">
      {[...rows].reverse().map((c, i) => (
        <li key={c.id} className="notice-card" data-status={i === 0 ? 'OPEN' : 'CLOSED'}>
          <p>
            <Badge tone={i === 0 ? 'info' : 'neutral'}>{i === 0 ? 'Current' : 'Superseded'}</Badge> <strong>{c.outcome_label}</strong> · {c.kind === 'RATIFICATION' ? 'ratification step' : 'decision on the appeal'}
          </p>
          <p className="muted">
            Recorded by {c.recorded_by} on {formatDate(c.recorded_at)}
            {c.reference ? ` · ${c.reference}` : ''}
          </p>
          <blockquote>{c.reasons}</blockquote>
          {c.correction_reason && <p className="note">Correction reason: {c.correction_reason}</p>}
        </li>
      ))}
    </ul>
  )
}

export function CouncilPanel({ ctx }: { ctx: Ctx }) {
  const { detail } = ctx
  const a = detail.allowed_actions
  const [form, setForm] = useState<null | 'ratification' | 'appeal'>(null)
  const rat = detail.policy?.ratification
  return (
    <>
      <Section title="Council ratification">
        <Alert tone="info">The Council’s step on the Executive Committee’s decision is kept separate from the decision itself. {rat?.clause_ref ? `Source: ${rat.clause_ref}.` : ''}</Alert>
        {!rat?.required ? <p className="note">The configuration does not require a Council step on this decision.</p> : detail.ratifications.length === 0 ? <p className="note">The Council’s step has not been recorded.</p> : <CouncilList rows={detail.ratifications} />}
        {form === 'ratification' ? (
          <CouncilForm ctx={ctx} kind="ratification" onDone={() => setForm(null)} />
        ) : (
          a.includes('record_ratification') && <Button onClick={() => setForm('ratification')}>{detail.live_ratification ? 'Record a correction…' : 'Record the Council’s step…'}</Button>
        )}
      </Section>
      {detail.appeal && (
        <Section title="Council decision on the appeal">
          <Alert tone="info">The Council’s decision on an appeal is final under the handbook (Ch. I cl. 1.11(e), Ch. II cl. 2.24(f)). The SAC’s recommendation is an input; it does not decide the appeal.</Alert>
          {detail.appeal.council_decisions.length === 0 ? (
            <p className="note">{detail.appeal.status === 'UNDER_SAC_REVIEW' ? 'The SAC has not finished its review, so the Council cannot yet record a decision.' : 'The Council has not recorded a decision.'}</p>
          ) : (
            <CouncilList rows={detail.appeal.council_decisions} />
          )}
          <SyncNote sync={detail.appeal.sync} what="the Council’s decision" />
          {form === 'appeal' ? (
            <CouncilForm ctx={ctx} kind="appeal" onDone={() => setForm(null)} />
          ) : (
            a.includes('record_appeal_decision') && <Button onClick={() => setForm('appeal')}>{detail.appeal.live_council_decision ? 'Record a correction…' : 'Record the Council’s decision…'}</Button>
          )}
        </Section>
      )}
    </>
  )
}

// ---- the appeal (staff view) and closing ----------------------------------------------------------------

export function AppealStaffPanel({ detail }: { detail: DecisionCaseDetail }) {
  const e = detail.appeal_eligibility
  const ap = detail.appeal
  return (
    <Section title="Appeal">
      <Alert tone="info">An appeal is made once, to the Council, against the Executive Committee’s decision. Eligibility and the period come from the rule version’s recorded provision; nothing is assumed.</Alert>
      <Facts>
        <Fact label="Eligibility">
          <strong>{humanise(e.state)}</strong> — {e.message}
        </Fact>
        {e.clause_ref && <Fact label="Provision">{e.clause_ref}</Fact>}
        {e.deadline_at && <Fact label="Period ends">{formatDate(e.deadline_at)}</Fact>}
      </Facts>
      {e.window?.note && <p className="note">{e.window.note}</p>}
      {ap ? (
        <>
          <p>
            <Badge tone="info">{ap.status_label}</Badge> submitted {formatDate(ap.submitted_at)}
          </p>
          <p className="eyebrow">Grounds</p>
          <blockquote>{ap.grounds}</blockquote>
          {ap.sac_recommendation ? (
            <p>
              SAC recommendation (version {ap.sac_recommendation.version}): <strong>{humanise(ap.sac_recommendation.option ?? 'none')}</strong> — submitted {formatDate(ap.sac_recommendation.submitted_at)}. It is a recommendation, not a decision.
            </p>
          ) : (
            <p className="note">The SAC has not submitted its recommendation.</p>
          )}
          <p className="note">{ap.documents.length} supporting document{ap.documents.length === 1 ? '' : 's'} attached.</p>
        </>
      ) : (
        <p className="note">No appeal has been submitted.</p>
      )}
    </Section>
  )
}

export function ClosePanel({ ctx }: { ctx: Ctx }) {
  const { detail, run, busy, error } = ctx
  const [open, setOpen] = useState(false)
  if (detail.state !== 'ISSUED' && detail.state !== 'CLOSED') return null
  return (
    <Section title="Close the decision stage">
      {detail.state === 'CLOSED' ? (
        <Alert tone="success" title="Closed">
          The decision stage was closed {detail.closed_at ? formatDate(detail.closed_at) : ''}. {detail.post_decision ? 'A hand-off for post-decision processing was recorded; that processing is not part of this system yet.' : ''}
        </Alert>
      ) : (
        <>
          <p className="note">Closing records that the decision and everything that must follow it are finished. It needs the Council’s step (where required) and either an appeal decided or the appeal period run out.</p>
          {detail.closure_blockers.length > 0 && (
            <Alert tone="warning" title="It cannot be closed yet">
              <ul className="plain-list">
                {detail.closure_blockers.map((b) => (
                  <li key={b.code}>{b.message}</li>
                ))}
              </ul>
            </Alert>
          )}
          {errorNote(error)}
          {detail.allowed_actions.includes('close_case') && <Button onClick={() => setOpen(true)}>Close the decision stage…</Button>}
          <ConfirmDialog
            open={open}
            title="Close the decision stage"
            confirmLabel="Close"
            busy={busy}
            onCancel={() => setOpen(false)}
            onConfirm={async () => {
              await run(() => decisionsApi.close(detail.id, detail.row_version))
              setOpen(false)
            }}
          >
            <p>This records the final status and hands the case to post-decision processing. It does not change the decision.</p>
          </ConfirmDialog>
        </>
      )}
    </Section>
  )
}

export const HistoryPanel = ({ detail }: { detail: DecisionCaseDetail }) => (
  <Section title="History">
    <EventList events={detail.events} />
  </Section>
)
