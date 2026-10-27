import { useEffect, useState } from 'react'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Alert, Badge } from '../../components/ui/feedback'
import { CheckboxField, SelectField, TextAreaField, TextField } from '../../components/ui/Field'
import { DemoBanner } from '../../components/ui/Banners'
import type { ApiError } from '../../lib/api'
import { formatDate, humanise } from '../../lib/format'
import { Section } from '../applications/parts'
import { Fact, Facts } from '../scrutiny/parts'
import { committeeApi, newKey } from './committeeApi'
import { CommitteeStateBadge, EventList, MODE_WORDS, SCHEDULE_TONE, SCHEDULE_WORDS, localTime, stageWord } from './parts'
import type { CommitteeCaseDetail, Schedule } from './types'

export interface PanelCtx {
  detail: CommitteeCaseDetail
  run: (fn: () => Promise<CommitteeCaseDetail>) => Promise<boolean>
  busy: boolean
  error: ApiError | null
}

export const errorNote = (e: ApiError | null) => (e ? <Alert tone="error">{e.message}</Alert> : null)

// ---- overview -----------------------------------------------------------------------------------------------

export function OverviewPanel({ detail }: { detail: CommitteeCaseDetail }) {
  const a = detail.application
  return (
    <Section title="Case summary" aside={<CommitteeStateBadge state={detail.state} label={detail.state_label} />}>
      {detail.policy?.banner && <DemoBanner text={detail.policy.banner} />}
      <Facts>
        <Fact label="Application">
          <span className="mono">{a.reference}</span>
        </Fact>
        <Fact label="Institution">{a.institution_name}</Fact>
        <Fact label="Category / year">
          {a.category_name} · <span className="mono">{a.academic_year}</span>
        </Fact>
        <Fact label="Stage">{stageWord(detail.stage)}</Fact>
        <Fact label="Activated">{formatDate(detail.activated_at)}</Fact>
        <Fact label="Why this stage applies">
          Route stage “{detail.trigger.route_clause?.label ?? detail.trigger.stage_key}”
          {detail.trigger.route_clause?.is_conditional ? ' (conditional)' : ' (required by the route)'}
          {detail.trigger.determination ? ` — determined: ${detail.trigger.determination.reason}` : ''}
        </Fact>
        <Fact label="Rule version it follows">
          <span className="mono">
            {detail.trigger.rule_version.rule_set} · v{detail.trigger.rule_version.number}
          </span>{' '}
          {detail.trigger.rule_version.classification === 'DEMO' && <span className="demo-tag">DEMO ONLY</span>}
        </Fact>
        <Fact label="Scrutiny outcome">{detail.scrutiny.completion?.outcome ? humanise(detail.scrutiny.completion.outcome) : '—'} (not an approval)</Fact>
        <Fact label="Committee configuration">
          {detail.policy ? (
            <>
              {detail.policy.title} <span className="mono">v{detail.policy.version}</span> · {detail.policy.classification === 'DEMO' ? 'DEMO ONLY' : 'official'}
            </>
          ) : (
            'None applies'
          )}
        </Fact>
      </Facts>
      {detail.blockers.length > 0 && (
        <Alert tone="warning" title="Blocked by missing configuration">
          <ul className="plain-list">
            {detail.blockers.map((b) => (
              <li key={b.code}>{b.message}</li>
            ))}
          </ul>
          <p className="note">This has been recorded for the rule configuration reviewers. Nothing is assumed to keep the case moving.</p>
        </Alert>
      )}
      {detail.outstanding_actions.length > 0 && (
        <ul className="plain-list outstanding">
          {detail.outstanding_actions.map((o) => (
            <li key={o}>{o}</li>
          ))}
        </ul>
      )}
      {detail.policy && (
        <details className="panel panel--collapsible">
          <summary>Sources behind this stage’s configuration ({detail.policy.sources.length})</summary>
          <ul className="plain-list">
            {detail.policy.sources.map((s) => (
              <li key={s.title}>
                {s.title} — <span className="mono">{s.clause_ref}</span> · {humanise(s.verification_status)}
              </li>
            ))}
          </ul>
        </details>
      )}
    </Section>
  )
}

// ---- selection ----------------------------------------------------------------------------------------------------

export function SelectionPanel({ detail, run, busy, error }: PanelCtx) {
  const sel = detail.selection
  const a = detail.allowed_actions
  const [members, setMembers] = useState<{ id: string; name: string }[]>([])
  const [pick, setPick] = useState<string[]>([])
  const [key, setKey] = useState(newKey)
  const [dialog, setDialog] = useState<null | 'cancel' | 'reselect'>(null)
  const [reason, setReason] = useState('')
  const coordinator = detail.scope.includes('coordinator')
  const canRequest = a.includes('request_selection')

  useEffect(() => {
    if (coordinator && canRequest && sel.availability.demo) void committeeApi.assignable(detail.stage).then((r) => setMembers(r.members)).catch(() => setMembers([]))
  }, [coordinator, canRequest, sel.availability.demo, detail.stage])

  return (
    <Section title="Members and authorised selection">
      <Alert tone={sel.availability.available ? 'warning' : 'info'} title={sel.availability.label}>
        {sel.availability.notice}
      </Alert>
      {sel.assignees.length === 0 ? (
        <p className="note">No members are assigned to this case.</p>
      ) : (
        <ul className="plain-list assignments">
          {sel.assignees.map((m) => (
            <li key={m.user_id}>
              <strong>{m.name}</strong> {m.demo && <Badge tone="warning">Demonstration — fictional</Badge>}
              <div className="mono muted">
                {humanise(m.source)} · since {formatDate(m.since)}
              </div>
            </li>
          ))}
        </ul>
      )}
      {sel.requests.length > 0 && (
        <details className="panel panel--collapsible">
          <summary>Selection requests and results ({sel.requests.length})</summary>
          <ul className="plain-list">
            {sel.requests.map((r) => (
              <li key={r.id}>
                <span className="mono">{r.request_ref}</span> · {humanise(r.provider)} {r.demo && <Badge tone="warning">DEMO</Badge>} · <strong>{humanise(r.status)}</strong> · requested {formatDate(r.requested_at)}
                {r.result_at && <> · result {formatDate(r.result_at)} from {r.result_source}</>}
                {r.members.length > 0 && <div className="muted">Members: {r.members.map((m) => m.name).join(', ')}</div>}
                {r.error_detail && <div className="note">{r.error_detail}</div>}
              </li>
            ))}
          </ul>
        </details>
      )}
      {errorNote(error)}
      {coordinator && (
        <>
          {canRequest && sel.availability.demo && (
            <fieldset className="member-pick">
              <legend>Record fictional members (needs at least {sel.min_members})</legend>
              {members.length === 0 && <p className="note">No fictional members exist in this deployment.</p>}
              {members.map((m) => (
                <CheckboxField key={m.id} label={m.name} checked={pick.includes(m.id)} onChange={(e) => setPick((p) => (e.target.checked ? [...p, m.id] : p.filter((x) => x !== m.id)))} />
              ))}
            </fieldset>
          )}
          <div className="action-bar">
            {canRequest && (
              <Button loading={busy} disabled={sel.availability.demo && pick.length < sel.min_members} onClick={async () => { if (await run(() => committeeApi.requestSelection(detail.stage, detail.id, sel.availability.demo ? pick : [], key))) { setPick([]); setKey(newKey()) } }}>
                {sel.availability.demo ? 'Record demonstration selection' : 'Request selection'}
              </Button>
            )}
            {a.includes('refresh_selection') && (
              <Button variant="secondary" loading={busy} onClick={() => void run(() => committeeApi.refreshSelection(detail.stage, detail.id))}>
                Check for a result
              </Button>
            )}
            {a.includes('cancel_selection') && (
              <Button variant="secondary" onClick={() => { setReason(''); setDialog('cancel') }}>
                Cancel the request…
              </Button>
            )}
            {a.includes('reselect') && (
              <Button variant="secondary" onClick={() => { setReason(''); setDialog('reselect') }}>
                Redo the selection…
              </Button>
            )}
          </div>
          {!canRequest && !sel.availability.available && <p className="note">Selection cannot be requested here until an authorised interface to the official process is connected.</p>}
        </>
      )}
      <ConfirmDialog
        open={dialog !== null}
        title={dialog === 'cancel' ? 'Cancel the selection request' : 'Redo the selection'}
        confirmLabel={dialog === 'cancel' ? 'Cancel the request' : 'Withdraw members and redo'}
        busy={busy}
        confirmDisabled={reason.trim().length < 5}
        onCancel={() => setDialog(null)}
        onConfirm={async () => {
          const ok = await run(() => (dialog === 'cancel' ? committeeApi.cancelSelection(detail.stage, detail.id, reason.trim()) : committeeApi.reselect(detail.id, reason.trim(), detail.row_version)))
          if (ok) setDialog(null)
        }}
      >
        {dialog === 'reselect' && <Alert tone="warning">The assigned members and any live visit schedule are withdrawn. Both stay on record with this reason.</Alert>}
        <TextAreaField label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} rows={3} />
      </ConfirmDialog>
    </Section>
  )
}

// ---- schedule ---------------------------------------------------------------------------------------------------

function ScheduleRow({ s }: { s: Schedule }) {
  return (
    <li className="schedule-row">
      <Badge tone={SCHEDULE_TONE[s.status]}>{SCHEDULE_WORDS[s.status]}</Badge> <strong>{localTime(s.start_local, s.timezone)}</strong> · {MODE_WORDS[s.mode] ?? s.mode}
      {s.location && <div>Place: {s.location}</div>}
      {s.access_details && <div>How to join: {s.access_details}</div>}
      {s.mode_reason && <div className="note">Reason for this mode: {s.mode_reason}</div>}
      {s.change_reason && <div className="note">Rescheduled because: {s.change_reason}</div>}
      {s.closed_reason && s.status !== 'COMPLETED' && <div className="note">Closed: {s.closed_reason}</div>}
    </li>
  )
}

export function SchedulePanel({ detail, run, busy, error }: PanelCtx) {
  const what = detail.stage === 'EVC' ? 'visit' : 'hearing'
  const a = detail.allowed_actions
  const active = detail.schedule.active
  const modes = detail.stage === 'EVC' ? detail.policy?.modes ?? [] : detail.stage === 'SAC' ? detail.policy?.sac?.hearing_modes ?? [] : detail.policy?.shc?.hearing_modes ?? []
  const [open, setOpen] = useState<null | 'propose' | 'reschedule'>(null)
  const [f, setF] = useState({ start_local: '', timezone: 'Asia/Kolkata', mode: '', mode_reason: '', location: '', access_details: '', reason: '' })
  const [key, setKey] = useState(newKey)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [cancelReason, setCancelReason] = useState('')
  const mode = modes.find((m) => m.key === f.mode)
  const set = (patch: Partial<typeof f>) => setF((cur) => ({ ...cur, ...patch }))
  const history = detail.schedule.history.filter((s) => s.id !== active?.id)
  const can = a.includes('propose_schedule') || a.includes('reschedule')
  const coordinator = detail.scope.includes('coordinator')

  async function submit() {
    const body = { start_local: f.start_local, timezone: f.timezone.trim(), mode: f.mode, mode_reason: f.mode_reason.trim() || null, location: f.location.trim() || null, access_details: f.access_details.trim() || null, idempotency_key: key }
    const ok = await run(() => (open === 'reschedule' ? committeeApi.reschedule(detail.stage, detail.id, { ...body, reason: f.reason.trim() }) : committeeApi.propose(detail.stage, detail.id, body)))
    if (ok) {
      setOpen(null)
      setKey(newKey())
    }
  }

  return (
    <Section title={detail.stage === 'EVC' ? 'Visit schedule' : 'Hearing schedule'}>
      <Alert tone="info">
        The system records the schedule it is given. It holds no availability data, so it never says that anyone is free; the only clash it can see is the same member starting another visit or hearing at the exact same time.
      </Alert>
      {active ? (
        <ul className="plain-list">
          <ScheduleRow s={active} />
        </ul>
      ) : (
        <p className="note">No {what} is currently scheduled.</p>
      )}
      {history.length > 0 && (
        <details className="panel panel--collapsible">
          <summary>Earlier schedules ({history.length})</summary>
          <ul className="plain-list">
            {history.map((s) => (
              <ScheduleRow key={s.id} s={s} />
            ))}
          </ul>
        </details>
      )}
      {errorNote(error)}
      {coordinator && (
        <div className="action-bar">
          {a.includes('propose_schedule') && <Button onClick={() => { setOpen('propose'); setF((c) => ({ ...c, reason: '' })) }}>Propose a {what} schedule…</Button>}
          {a.includes('confirm_schedule') && active && (
            <Button loading={busy} onClick={() => void run(() => committeeApi.confirm(detail.stage, detail.id, active.id, active.row_version))}>
              Confirm this schedule
            </Button>
          )}
          {a.includes('reschedule') && <Button variant="secondary" onClick={() => setOpen('reschedule')}>Reschedule…</Button>}
          {a.includes('cancel_schedule') && active && <Button variant="secondary" onClick={() => { setCancelReason(''); setCancelOpen(true) }}>Cancel this schedule…</Button>}
        </div>
      )}
      {coordinator && !can && !active && detail.blockers.length > 0 && <p className="note">Scheduling is blocked until the missing configuration is supplied.</p>}
      {open && (
        <form className="panel schedule-form" onSubmit={(e) => { e.preventDefault(); void submit() }} aria-label={open === 'reschedule' ? 'Reschedule' : 'Propose a schedule'}>
          <h3 className="panel__sub">{open === 'reschedule' ? 'Reschedule (the current schedule stays on record)' : `Propose a ${what} schedule`}</h3>
          <TextField label="Date and time (local)" type="datetime-local" value={f.start_local} onChange={(e) => set({ start_local: e.target.value })} required />
          <TextField label="Time zone" value={f.timezone} onChange={(e) => set({ timezone: e.target.value })} hint="An IANA name, for example Asia/Kolkata. The time above is read in this zone." required />
          <SelectField label="Mode" value={f.mode} onChange={(e) => set({ mode: e.target.value })} options={modes.map((m) => ({ value: m.key, label: m.label }))} required />
          {detail.stage === 'EVC' && (detail.policy?.unverified_modes ?? []).map((m) => (
            <p key={m.key} className="note">
              {m.label} is not offered: {m.reason}
            </p>
          ))}
          {mode?.requires_reason && <TextAreaField label="Circumstance that justifies this mode" value={f.mode_reason} onChange={(e) => set({ mode_reason: e.target.value })} rows={2} hint={mode.clause_ref} />}
          {mode?.requires.includes('location') && <TextAreaField label="Place" value={f.location} onChange={(e) => set({ location: e.target.value })} rows={2} />}
          {mode?.requires.includes('access_details') && <TextAreaField label="How to join (no passwords or secrets)" value={f.access_details} onChange={(e) => set({ access_details: e.target.value })} rows={2} />}
          {open === 'reschedule' && <TextAreaField label="Reason for rescheduling" value={f.reason} onChange={(e) => set({ reason: e.target.value })} rows={2} required />}
          <div className="action-bar">
            <Button type="submit" loading={busy} disabled={!f.start_local || !f.mode}>
              {open === 'reschedule' ? 'Reschedule' : 'Propose'}
            </Button>
            <Button variant="quiet" onClick={() => setOpen(null)}>
              Close
            </Button>
          </div>
        </form>
      )}
      <ConfirmDialog
        open={cancelOpen}
        title={`Cancel the ${what} schedule`}
        confirmLabel="Cancel the schedule"
        busy={busy}
        confirmDisabled={cancelReason.trim().length < 5}
        onCancel={() => setCancelOpen(false)}
        onConfirm={async () => {
          if (active && (await run(() => committeeApi.cancelSchedule(detail.stage, detail.id, active.id, cancelReason.trim())))) setCancelOpen(false)
        }}
      >
        <TextAreaField label="Reason" value={cancelReason} onChange={(e) => setCancelReason(e.target.value)} rows={3} />
      </ConfirmDialog>
    </Section>
  )
}

// ---- route: what follows this stage ----------------------------------------------------------------------------------

export function RoutePanel({ detail, run, busy, error }: PanelCtx) {
  const next = detail.next
  const [choice, setChoice] = useState('')
  const [reason, setReason] = useState('')
  if (detail.state !== 'COMPLETED' || !next) return null
  const single = next.options.length === 1 && next.options[0]?.is_conditional
  return (
    <Section title="What follows this stage">
      <p className="note">The next stage is read from the application’s own approved route. It is never assumed.</p>
      {next.status === 'QUEUED' && (
        <Alert tone="success" title="Next stage activated">
          {next.to_stage_label} was activated from the approved route.
        </Alert>
      )}
      {next.status === 'NO_FURTHER_STAGE' && (
        <Alert tone="success" title={detail.decision_handoff ? 'Ready for the decision stage' : 'No further stage in the route'}>
          {detail.decision_handoff ? detail.decision_handoff.basis.note : next.reason}
        </Alert>
      )}
      {next.status === 'EXCEPTION' && (
        <Alert tone="error" title="The route cannot be applied">
          {next.reason} The case waits for configuration review; nothing was guessed.
        </Alert>
      )}
      {next.status === 'AWAITING_DETERMINATION' && (
        <>
          <Alert tone="warning" title="An authorised person must determine the next stage">
            {next.reason}
          </Alert>
          {next.determination_reason && <p className="note">{next.determination_reason}</p>}
          {detail.allowed_actions.includes('determine_next') && (
            <form className="panel" onSubmit={(e) => { e.preventDefault(); void run(() => committeeApi.determine(detail.stage, detail.id, choice === '__none__' ? null : choice, reason.trim())) }}>
              <SelectField
                label="Determination"
                value={choice}
                onChange={(e) => setChoice(e.target.value)}
                options={[...next.options.map((o) => ({ value: o.stage_key, label: `${o.label} applies${o.condition_description ? ` — ${o.condition_description}` : ''}` })), ...(single ? [{ value: '__none__', label: 'It does not apply' }] : [])]}
              />
              <TextAreaField label="Reason (kept on record)" value={reason} onChange={(e) => setReason(e.target.value)} rows={2} />
              {errorNote(error)}
              <Button type="submit" loading={busy} disabled={!choice || reason.trim().length < 5}>
                Record determination
              </Button>
            </form>
          )}
        </>
      )}
    </Section>
  )
}

// ---- completing the stage -------------------------------------------------------------------------------------------

export function CompletePanel({ detail, run, busy, error, label }: PanelCtx & { label: string }) {
  const [open, setOpen] = useState(false)
  const [note, setNote] = useState('')
  if (!detail.allowed_actions.includes('complete')) return null
  return (
    <Section title="Complete this stage">
      <p className="note">{label}</p>
      {errorNote(error)}
      <Button onClick={() => { setNote(''); setOpen(true) }}>Complete the {detail.stage} stage…</Button>
      <ConfirmDialog
        open={open}
        title={`Complete the ${detail.stage} stage`}
        confirmLabel="Complete the stage"
        busy={busy}
        confirmDisabled={note.trim().length < 5}
        onCancel={() => setOpen(false)}
        onConfirm={async () => {
          const ok = await run(() => (detail.stage === 'EVC' ? committeeApi.completeEvc(detail.id, note.trim(), detail.row_version) : detail.stage === 'SAC' ? committeeApi.completeSac(detail.id, note.trim(), detail.row_version) : committeeApi.completeShc(detail.id, note.trim(), detail.row_version)))
          if (ok) setOpen(false)
        }}
      >
        <Alert tone="warning">Completing the stage is not an approval or a decision. The route then records what follows.</Alert>
        <TextAreaField label="Note for the record" value={note} onChange={(e) => setNote(e.target.value)} rows={3} />
      </ConfirmDialog>
    </Section>
  )
}

export const HistoryPanel = ({ detail }: { detail: CommitteeCaseDetail }) => (
  <Section title="History">
    <EventList events={detail.events} />
  </Section>
)
