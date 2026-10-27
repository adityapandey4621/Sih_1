import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Button } from '../../components/ui/Button'
import { Alert, Badge, EmptyState } from '../../components/ui/feedback'
import { formatDate } from '../../lib/format'
import { useResource } from '../../lib/useResource'
import { ResourceView } from '../admin/shared'
import { useAuth } from '../auth/AuthContext'
import { PageHeader } from '../shell/PageHeader'
import '../applications/applications.css'
import '../scrutiny/scrutiny.css'
import './committee.css'
import { committeeApi } from './committeeApi'
import { CommitteeStateBadge, MODE_WORDS, localTime } from './parts'
import type { CommitteeState, QueueData, Stage } from './types'

const BUCKETS: Record<Stage, [CommitteeState, string][]> = {
  EVC: [
    ['SELECTION_PENDING', 'Awaiting authorised selection'],
    ['SELECTION_REQUESTED', 'Selection requested'],
    ['SCHEDULE_PENDING', 'Awaiting schedule'],
    ['SCHEDULED', 'Scheduled'],
    ['VISIT_IN_PROGRESS', 'Visit in progress'],
    ['REPORT_SUBMITTED', 'Report submitted'],
    ['COMPLETED', 'Completed'],
  ],
  SHC: [
    ['PREPARATION', 'Being prepared'],
    ['AWAITING_COMPLIANCE', 'Awaiting compliance'],
    ['COMPLIANCE_RECEIVED', 'Compliance received'],
    ['UNDER_REVIEW', 'Under review'],
    ['RECOMMENDATION_SUBMITTED', 'Recommendation submitted'],
    ['COMPLETED', 'Completed'],
  ],
  SAC: [
    ['SELECTION_PENDING', 'Awaiting authorised selection'],
    ['SELECTION_REQUESTED', 'Selection requested'],
    ['UNDER_REVIEW', 'Under review'],
    ['RECOMMENDATION_SUBMITTED', 'Recommendation submitted'],
    ['COMPLETED', 'Completed'],
  ],
}

const SCOPE_TEXT = (stage: Stage, scope: QueueData['scope']) =>
  scope === 'coordinator'
    ? `You see every ${stage} case so you can arrange the process. Members are recorded only through the authorised selection step, and nothing on this page can approve or decide an application.`
    : `You see only the ${stage} cases you were assigned to. Holding a member role alone does not show you any case.`

function Chips({ stage, data, state, flag, set }: { stage: Stage; data: QueueData; state: string; flag: string; set: (p: Record<string, string | null>) => void }) {
  const all = Object.values(data.counts).reduce((a, b) => a + b, 0)
  return (
    <div className="chips" role="group" aria-label="Filter by state">
      <button type="button" className="chip-btn" aria-pressed={!state && !flag} onClick={() => set({ state: null, flag: null })}>
        All <span className="mono">{all}</span>
      </button>
      {BUCKETS[stage].filter(([k]) => (data.counts[k] ?? 0) > 0 || k === state).map(([k, label]) => (
        <button key={k} type="button" className="chip-btn" aria-pressed={state === k} onClick={() => set({ state: k, flag: null })}>
          {label} <span className="mono">{data.counts[k] ?? 0}</span>
        </button>
      ))}
      <button type="button" className="chip-btn" aria-pressed={flag === 'blocked'} onClick={() => set({ flag: flag === 'blocked' ? null : 'blocked', state: null })}>
        Blocked by configuration
      </button>
    </div>
  )
}

export function CommitteeQueuePage({ stage }: { stage: Stage }) {
  const { can } = useAuth()
  const [params, setParams] = useSearchParams()
  const state = params.get('state') ?? ''
  const q = params.get('q') ?? ''
  const flag = params.get('flag') ?? ''
  const from = params.get('from') ?? ''
  const page = Number(params.get('page') ?? '1') || 1
  const [res, reload] = useResource(
    () => committeeApi.queue(stage, { state, q, blocked: flag === 'blocked', visit_from: from ? `${from}T00:00:00Z` : undefined, page, page_size: 20 }),
    [stage, state, q, flag, from, page],
  )
  const set = useMemo(
    () => (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(params)
      for (const [k, v] of Object.entries(patch)) (v ? next.set(k, v) : next.delete(k))
      if (!('page' in patch)) next.delete('page')
      setParams(next, { replace: true })
    },
    [params, setParams],
  )
  const coordinator = can(stage === 'EVC' ? 'evc:coordinate' : stage === 'SAC' ? 'sac:coordinate' : 'shc:coordinate')
  const path = stage.toLowerCase()
  const title = stage === 'EVC' ? (coordinator ? 'EVC coordination' : 'My visits') : stage === 'SAC' ? (coordinator ? 'Appeals (SAC)' : 'My appeal cases') : coordinator ? 'SHC coordination' : 'My SHC cases'

  return (
    <>
      <PageHeader
        title={title}
        description={stage === 'EVC' ? 'Applications whose approved route requires an expert visit, and where arranging and completing each visit stands.' : stage === 'SAC' ? 'Appeals against Executive Committee decisions that the institution submitted within the configured period. The SAC recommends; the Council decides.' : 'Applications whose approved route requires the Standing Hearing Committee compliance / hearing process.'}
        crumbs={[{ label: 'Workspace', to: '/app' }, { label: title }]}
      />
      <ResourceView resource={res} reload={reload}>
        {(data) => (
          <>
            <Alert tone="info">{SCOPE_TEXT(stage, data.scope)}</Alert>
            <Chips stage={stage} data={data} state={state} flag={flag} set={set} />
            <form
              className="filters"
              role="search"
              onSubmit={(e) => {
                e.preventDefault()
                const f = new FormData(e.currentTarget)
                set({ q: String(f.get('q') ?? '').trim() || null, from: String(f.get('from') ?? '') || null })
              }}
            >
              <div className="field">
                <label className="field__label" htmlFor="q">
                  Search reference
                </label>
                <input id="q" name="q" className="field__control" defaultValue={q} maxLength={80} />
              </div>
              <div className="field">
                <label className="field__label" htmlFor="from">
                  Scheduled on or after
                </label>
                <input id="from" name="from" type="date" className="field__control" defaultValue={from} />
              </div>
              <div className="filters__actions">
                <Button type="submit" variant="secondary" size="sm">
                  Apply
                </Button>
                {(q || from) && (
                  <Button variant="quiet" size="sm" onClick={() => set({ q: null, from: null })}>
                    Clear
                  </Button>
                )}
              </div>
            </form>
            {data.items.length === 0 ? (
              <EmptyState title={q || state || flag || from ? 'No cases match these filters' : 'No cases in your scope yet'}>
                {data.scope === 'member'
                  ? 'A case appears here only once you have been assigned to it through the authorised selection step.'
                  : stage === 'SAC' ? 'A case appears here when an institution submits an appeal that the recorded rules allow.' : 'A case appears here when the approved route of a submitted application requires this stage.'}
              </EmptyState>
            ) : (
              <div className="table-wrap">
                <table className="table queue-table">
                  <caption className="sr-only">{stage} cases</caption>
                  <thead>
                    <tr>
                      <th scope="col">Application</th>
                      <th scope="col">State</th>
                      <th scope="col">{stage === 'EVC' ? 'Visit' : 'Hearing'} and members</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((c) => (
                      <tr key={c.id}>
                        <td>
                          <Link className="mono link" to={`/app/${path}/${c.id}`}>
                            {c.application.reference}
                          </Link>
                          <div>{c.application.institution_name}</div>
                          <div className="muted">
                            {c.application.category_name} <span className="mono">· {c.application.academic_year}</span>
                          </div>
                          <div className="mono muted">Activated {formatDate(c.activated_at)}</div>
                        </td>
                        <td>
                          <CommitteeStateBadge state={c.state} label={c.state_label} />
                          {c.demo && <div><Badge tone="warning">DEMO ONLY configuration</Badge></div>}
                          {c.blocked && <div className="note">Blocked by missing configuration</div>}
                          {c.awaiting_determination && <div className="note">The next stage needs a determination</div>}
                        </td>
                        <td>
                          {c.schedule ? (
                            <>
                              <div>
                                <strong>{localTime(c.schedule.start_local, c.schedule.timezone)}</strong>
                              </div>
                              <div className="muted">
                                {MODE_WORDS[c.schedule.mode] ?? c.schedule.mode} · {c.schedule.status === 'CONFIRMED' ? 'confirmed' : 'proposed'}
                              </div>
                            </>
                          ) : (
                            <span className="muted">Not scheduled</span>
                          )}
                          <div>{c.assignees.length ? c.assignees.join(', ') : <span className="muted">No members assigned</span>}</div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <nav className="pager" aria-label="Pages">
              <Button variant="quiet" size="sm" disabled={page <= 1} onClick={() => set({ page: String(page - 1) })}>
                Previous
              </Button>
              <span className="mono">
                Page {data.page} of {Math.max(1, Math.ceil(data.total / data.page_size))} · {data.total} case{data.total === 1 ? '' : 's'}
              </span>
              <Button variant="quiet" size="sm" disabled={page * data.page_size >= data.total} onClick={() => set({ page: String(page + 1) })}>
                Next
              </Button>
            </nav>
          </>
        )}
      </ResourceView>
    </>
  )
}

export const EvcQueuePage = () => <CommitteeQueuePage stage="EVC" />
export const ShcQueuePage = () => <CommitteeQueuePage stage="SHC" />
export const SacQueuePage = () => <CommitteeQueuePage stage="SAC" />
