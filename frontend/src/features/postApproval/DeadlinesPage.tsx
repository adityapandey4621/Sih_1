import { useMemo, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Alert, Badge, EmptyState } from '../../components/ui/feedback'
import { SelectField, TextAreaField, TextField } from '../../components/ui/Field'
import { ApiError } from '../../lib/api'
import { formatDate, humanise } from '../../lib/format'
import { useResource } from '../../lib/useResource'
import { ResourceView } from '../admin/shared'
import { useAuth } from '../auth/AuthContext'
import { Section } from '../applications/parts'
import { Fact, Facts } from '../scrutiny/parts'
import { PageHeader } from '../shell/PageHeader'
import '../applications/applications.css'
import '../scrutiny/scrutiny.css'
import '../committee/committee.css'
import './postApproval.css'
import { ClockTable, DueLine, KIND_LABEL, TYPE_LABEL } from './parts'
import { clocksApi } from './postApprovalApi'
import type { ClockView, SchedulerHealth } from './types'

const KINDS = ['OFFICER_STAGE', 'INSTITUTION_RESPONSE', 'EVC', 'APPEAL', 'COMPLIANCE', 'DOCUMENT_VALIDITY']

function Health({ h }: { h: SchedulerHealth }) {
  const stuck = h.due_and_waiting > 0
  return (
    <Section title="Alert scheduler">
      <Alert tone={stuck ? 'warning' : 'success'} title={stuck ? `${h.due_and_waiting} alert${h.due_and_waiting === 1 ? ' is' : 's are'} due and waiting` : 'No alert is overdue for sending'}>
        {stuck ? `The oldest has waited ${Math.round(h.oldest_waiting_lag_seconds / 60)} minutes. ` : ''}
        {h.note}
      </Alert>
      <Facts>
        <Fact label="Live clocks">{h.live_clocks}</Fact>
        {Object.entries(h.work).map(([k, v]) => (
          <Fact key={k} label={humanise(k)}>
            {v}
          </Fact>
        ))}
      </Facts>
      {h.failed.length > 0 && (
        <Alert tone="error" title="Alerts that exhausted their retries">
          <ul className="plain-list">
            {h.failed.map((f) => (
              <li key={f.id}>
                {humanise(f.alert_kind)} · {f.attempts} attempts · {f.last_error ?? 'unknown error'} · {formatDate(f.updated_at)}
              </li>
            ))}
          </ul>
          <p className="note">An operator can give them another bounded set of attempts with `python -m app.scheduler --requeue-failed`.</p>
        </Alert>
      )}
    </Section>
  )
}

/** The list of deadlines, with filters. Used inside the obligations page (for institutions) and on the officials' Deadlines page. */
export function DeadlinesPanel({ official }: { official: boolean }) {
  const [params, setParams] = useSearchParams()
  const kind = params.get('kind') ?? ''
  const due = params.get('due') ?? ''
  const q = params.get('q') ?? ''
  const page = Number(params.get('page') ?? '1') || 1
  const [res, reload] = useResource(() => clocksApi.list({ kind, due, q, page }), [kind, due, q, page])
  const set = useMemo(
    () => (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(params)
      for (const [k, v] of Object.entries(patch)) (v ? next.set(k, v) : next.delete(k))
      if (!('page' in patch)) next.delete('page')
      setParams(next, { replace: true })
    },
    [params, setParams],
  )
  return (
    <ResourceView resource={res} reload={reload}>
      {(data) => (
        <>
          <Alert tone="info">
            A deadline exists only where a verified rule configuration states it, or where an authorised person cited it. Where none can be established the row says so instead of showing a date. A date passing raises reminders; it does not change any decision.
          </Alert>
          <div className="chips" role="group" aria-label="Timing">
            <button type="button" className="chip-btn" aria-pressed={!due} onClick={() => set({ due: null })}>
              All
            </button>
            {([['overdue', 'Past its date', data.counts.overdue], ['soon', 'Due soon', data.counts.soon], ['ontrack', 'On track', data.counts.ontrack]] as const).map(([k, label, n]) => (
              <button key={k} type="button" className="chip-btn" aria-pressed={due === k} onClick={() => set({ due: k })}>
                {label} <span className="mono">{n}</span>
              </button>
            ))}
            {data.counts.paused > 0 && <Badge tone="info">{data.counts.paused} paused</Badge>}
            {data.counts.not_established > 0 && <Badge tone="warning">{data.counts.not_established} without an established date</Badge>}
          </div>
          <form
            className="filters"
            role="search"
            onSubmit={(e) => {
              e.preventDefault()
              const fd = new FormData(e.currentTarget)
              set({ q: String(fd.get('q') ?? '').trim() || null, kind: String(fd.get('kind') ?? '') || null })
            }}
          >
            <div className="field">
              <label className="field__label" htmlFor="dl-q">
                Search
              </label>
              <input id="dl-q" name="q" className="field__control" defaultValue={q} maxLength={80} placeholder="Application reference or deadline" />
            </div>
            <div className="field">
              <label className="field__label" htmlFor="dl-kind">
                Kind
              </label>
              <select id="dl-kind" name="kind" className="field__control" defaultValue={kind}>
                <option value="">All kinds</option>
                {KINDS.map((k) => (
                  <option key={k} value={k}>
                    {KIND_LABEL[k]}
                  </option>
                ))}
              </select>
            </div>
            <div className="filters__actions">
              <Button type="submit" variant="secondary" size="sm">
                Apply
              </Button>
              {(q || kind) && (
                <Button variant="quiet" size="sm" onClick={() => set({ q: null, kind: null })}>
                  Clear
                </Button>
              )}
            </div>
          </form>
          {data.items.length === 0 ? (
            <EmptyState title={q || kind || due ? 'No deadlines match these filters' : 'No deadlines apply yet'}>
              {official ? 'Deadlines appear once a case reaches a stage whose approved configuration states one.' : 'When a deadline applies to one of your applications — a response period, an appeal window, an obligation or a document’s expiry — it is listed here.'}
            </EmptyState>
          ) : (
            <ClockTable clocks={data.items} detailLinks={official} showInstitution={official} />
          )}
          <nav className="pager" aria-label="Pages">
            <Button variant="quiet" size="sm" disabled={page <= 1} onClick={() => set({ page: String(page - 1) })}>
              Previous
            </Button>
            <span className="mono">
              Page {data.page} of {Math.max(1, Math.ceil(data.total / data.page_size))} · {data.total} deadline{data.total === 1 ? '' : 's'}
            </span>
            <Button variant="quiet" size="sm" disabled={page * data.page_size >= data.total} onClick={() => set({ page: String(page + 1) })}>
              Next
            </Button>
          </nav>
        </>
      )}
    </ResourceView>
  )
}

export default function DeadlinesPage() {
  const { can } = useAuth()
  const [health] = useResource(() => (can('clock:manage') ? clocksApi.health() : Promise.resolve(null)), [])
  return (
    <>
      <PageHeader title="Deadlines" description="Clocks for the stages and obligations within your remit. Each keeps the policy and rule version it was created under." crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Deadlines' }]} />
      {health.status === 'ready' && health.data && <Health h={health.data} />}
      <DeadlinesPanel official />
    </>
  )
}

// ---- one clock ---------------------------------------------------------------------------------------------------------

export function ClockDetailPage() {
  const { id = '' } = useParams()
  const { can } = useAuth()
  const [res, reload] = useResource(() => clocksApi.detail(id), [id])
  const [shown, setShown] = useState<ClockView | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [reasonCode, setReasonCode] = useState('')
  const [why, setWhy] = useState('')
  const [ref, setRef] = useState('')
  const [confirm, setConfirm] = useState<'pause' | 'resume' | null>(null)
  async function act(fn: () => Promise<ClockView>) {
    setBusy(true)
    setError(null)
    try {
      setShown(await fn())
      setConfirm(null)
      setWhy('')
      reload()
    } catch (e) {
      setConfirm(null)
      setError(e instanceof ApiError ? e : new ApiError(0, 'Unexpected error.'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <ResourceView resource={res} reload={reload}>
      {(loaded) => {
        const c = shown && shown.id === loaded.id ? shown : loaded
        const manage = can('clock:manage')
        const reasons = c.pause?.reasons ?? []
        return (
          <>
            <PageHeader title={c.label} description={`${KIND_LABEL[c.kind]} · ${c.application.reference} · ${c.application.institution_name}`} crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Deadlines', to: '/app/deadlines' }, { label: c.label }]} />
            <Section title="The deadline" aside={<Badge tone={c.deadline_type === 'STATUTORY' ? 'info' : 'neutral'}>{TYPE_LABEL[c.deadline_type]}</Badge>}>
              <Facts>
                <Fact label="Status">{c.status === 'NOT_ESTABLISHED' ? <span>No date established. {c.not_established_reason}</span> : c.status === 'PAUSED' ? 'Paused — the date is open until it resumes' : c.status === 'COMPLETED' ? `Finished: ${humanise(c.completion_reason ?? '')} (${formatDate(c.completed_at)})` : <DueLine due={c.effective_due_at ?? c.due_at} timing={c.timing} />}</Fact>
                <Fact label="Original due date">{c.due_at ? formatDate(c.due_at) : '—'}</Fact>
                <Fact label="Approved pause time">{c.paused_seconds ? `${(c.paused_seconds / 3600).toFixed(1)} hours` : 'None'}</Fact>
                <Fact label="Triggered by">
                  {humanise(c.trigger_event)} · {formatDate(c.trigger_at)}
                </Fact>
                <Fact label="Policy">
                  <span className="mono">{c.policy_key}</span> {c.policy.duration_value ? `· ${c.policy.duration_value} ${c.policy.duration_unit} (${(c.policy.calendar_convention ?? 'convention not stated').toLowerCase()})` : ''}
                  {c.policy.demo && <Badge tone="warning">DEMO ONLY</Badge>}
                  <div className="muted">{c.policy.message}</div>
                </Fact>
                <Fact label="Source">{c.policy.source ? `${c.policy.source.title} (${humanise(c.policy.source.verification_status)})` : c.policy.authority ? `Cited: ${c.policy.authority}` : '—'}</Fact>
                <Fact label="Alerts">
                  {c.alerts && (c.alerts.pre_due_hours.length || c.alerts.at_due || c.alerts.overdue_hours.length)
                    ? [...c.alerts.pre_due_hours.map((h) => `${h} h before`), ...(c.alerts.at_due ? ['at the date'] : []), ...c.alerts.overdue_hours.map((h) => `${h} h after`)].join(', ')
                    : 'None configured — none is raised'}
                  {c.escalate_to && c.escalate_to.length > 0 && <div className="muted">Overdue alerts also go to: {c.escalate_to.map(humanise).join(', ')}</div>}
                </Fact>
              </Facts>
            </Section>

            {error && <Alert tone="error">{error.message}</Alert>}
            {manage && (c.status === 'RUNNING' || c.status === 'PAUSED') && (
              <Section title="Pause and resume">
                {c.status === 'RUNNING' && !c.pause?.allowed && <Alert tone="info">This deadline’s policy does not permit pausing. It cannot be stopped, moved or reset by anyone.</Alert>}
                {c.status === 'RUNNING' && c.pause?.allowed && (
                  <>
                    <Alert tone="info">The policy permits pausing{reasons.length ? ' for specific reasons' : ''}. The time paused is added to the date when it resumes; the original date stays on record.</Alert>
                    {reasons.length > 0 && <SelectField label="Reason" value={reasonCode} onChange={(e) => setReasonCode(e.target.value)} options={reasons.map((r) => ({ value: r, label: humanise(r) }))} />}
                    <TextAreaField label="Why it is being paused" value={why} onChange={(e) => setWhy(e.target.value)} maxLength={2000} hint="At least 5 characters." />
                    <TextField label="Reference" optional value={ref} onChange={(e) => setRef(e.target.value)} maxLength={300} />
                    <Button disabled={why.trim().length < 5 || (reasons.length > 0 && !reasonCode)} onClick={() => setConfirm('pause')}>
                      Pause this deadline
                    </Button>
                  </>
                )}
                {c.status === 'PAUSED' && <Button onClick={() => setConfirm('resume')}>Resume this deadline</Button>}
                <ConfirmDialog
                  open={confirm !== null}
                  title={confirm === 'pause' ? 'Pause this deadline?' : 'Resume this deadline?'}
                  confirmLabel={confirm === 'pause' ? 'Pause' : 'Resume'}
                  busy={busy}
                  onCancel={() => setConfirm(null)}
                  onConfirm={() => act(() => (confirm === 'pause' ? clocksApi.pause(c.id, { reason_code: reasonCode || null, reason: why, reference: ref.trim() || null, row_version: c.row_version }) : clocksApi.resume(c.id, c.row_version)))}
                >
                  <p>{confirm === 'pause' ? 'Pending reminders for this deadline are cancelled and planned again when it resumes.' : 'The time it was paused is added to the due date. Reminders are planned against the new date.'}</p>
                </ConfirmDialog>
              </Section>
            )}

            {c.segments && c.segments.length > 0 && (
              <Section title="Approved pauses">
                <ul className="plain-list">
                  {c.segments.map((s) => (
                    <li key={s.id}>
                      {formatDate(s.paused_at)} → {s.resumed_at ? formatDate(s.resumed_at) : 'still paused'} · {s.reason} {s.reference && <span className="muted">({s.reference})</span>}
                    </li>
                  ))}
                </ul>
              </Section>
            )}

            {c.alert_work && (
              <Section title="Alerts planned and sent">
                {c.alert_work.length === 0 ? (
                  <p className="note">No alert is planned for this deadline.</p>
                ) : (
                  <div className="table-wrap">
                    <table className="table">
                      <caption className="sr-only">Alerts</caption>
                      <thead>
                        <tr>
                          <th scope="col">Alert</th>
                          <th scope="col">Due to run</th>
                          <th scope="col">State</th>
                          <th scope="col">Result</th>
                        </tr>
                      </thead>
                      <tbody>
                        {c.alert_work.map((w) => (
                          <tr key={w.id}>
                            <td>
                              {humanise(w.alert_kind)} {w.offset_hours ? `· ${w.offset_hours} h` : ''}
                            </td>
                            <td className="mono">{formatDate(w.run_at)}</td>
                            <td>
                              {humanise(w.status)} <span className="muted">· attempt {w.attempts}/{w.max_attempts}</span>
                            </td>
                            <td>
                              {w.outcome ? humanise(w.outcome) : '—'}
                              {w.delivery && <div className="muted">{w.recipients} recipient{w.recipients === 1 ? '' : 's'} · stored in the app only (no email or SMS)</div>}
                              {w.last_error && <div className="note">{w.last_error}</div>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Section>
            )}

            {c.events && (
              <Section title="History">
                <ol className="history">
                  {c.events.map((e, i) => (
                    <li key={`${e.at}-${i}`} className="history__item">
                      <span className="mono history__at">{formatDate(e.at)}</span>
                      <span>
                        <strong>{humanise(e.action)}</strong>
                        {e.from_status && e.to_status && e.from_status !== e.to_status ? <span className="muted"> ({humanise(e.from_status)} → {humanise(e.to_status)})</span> : null}
                      </span>
                    </li>
                  ))}
                </ol>
              </Section>
            )}
          </>
        )
      }}
    </ResourceView>
  )
}
