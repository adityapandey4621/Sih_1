import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Button } from '../../components/ui/Button'
import { Alert, Badge, EmptyState, type Tone } from '../../components/ui/feedback'
import { formatDate } from '../../lib/format'
import { useResource } from '../../lib/useResource'
import { ResourceView } from '../admin/shared'
import { PageHeader } from '../shell/PageHeader'
import '../applications/applications.css'
import '../scrutiny/scrutiny.css'
import '../committee/committee.css'
import { decisionsApi } from './decisionsApi'
import type { DecisionState } from './types'

const BUCKETS: [DecisionState, string][] = [
  ['AWAITING_OBSERVATIONS', 'Awaiting Bureau observations'],
  ['READY_FOR_DECISION', 'Ready for decision'],
  ['DECIDED', 'Decided — not issued'],
  ['ISSUED', 'Issued'],
  ['CLOSED', 'Closed'],
]

const TONE: Record<DecisionState, Tone> = { AWAITING_OBSERVATIONS: 'warning', READY_FOR_DECISION: 'info', DECIDED: 'warning', ISSUED: 'success', CLOSED: 'neutral' }

export const DecisionStateBadge = ({ state, label }: { state: DecisionState; label: string }) => <Badge tone={TONE[state] ?? 'neutral'}>{label}</Badge>

export default function DecisionQueuePage() {
  const [params, setParams] = useSearchParams()
  const state = params.get('state') ?? ''
  const q = params.get('q') ?? ''
  const page = Number(params.get('page') ?? '1') || 1
  const [res, reload] = useResource(() => decisionsApi.queue({ state, q, page, page_size: 20 }), [state, q, page])
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
    <>
      <PageHeader
        title="Decision queue"
        description="Applications whose every mandatory stage on the approved route is complete. A case appears here only then."
        crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Decision queue' }]}
      />
      <ResourceView resource={res} reload={reload}>
        {(data) => {
          const all = Object.values(data.counts).reduce((a, b) => a + b, 0)
          const role = data.can.record ? 'You can record the Executive Committee’s decision.' : data.can.observe ? 'You can record Regulation Bureau observations and issue recorded outcomes.' : data.can.council ? 'You can record the Council’s steps: ratification and the decision on an appeal.' : 'You can read the record.'
          return (
            <>
              <Alert tone="info">
                {role} Nothing on this page approves or rejects an application: decisions are made by the competent authority and recorded here. Holding an administrator role gives no decision power.
              </Alert>
              <div className="chips" role="group" aria-label="Filter by state">
                <button type="button" className="chip-btn" aria-pressed={!state} onClick={() => set({ state: null })}>
                  All <span className="mono">{all}</span>
                </button>
                {BUCKETS.filter(([k]) => (data.counts[k] ?? 0) > 0 || k === state).map(([k, label]) => (
                  <button key={k} type="button" className="chip-btn" aria-pressed={state === k} onClick={() => set({ state: k })}>
                    {label} <span className="mono">{data.counts[k] ?? 0}</span>
                  </button>
                ))}
              </div>
              <form
                className="filters"
                role="search"
                onSubmit={(e) => {
                  e.preventDefault()
                  set({ q: String(new FormData(e.currentTarget).get('q') ?? '').trim() || null })
                }}
              >
                <div className="field">
                  <label className="field__label" htmlFor="dq">
                    Search reference
                  </label>
                  <input id="dq" name="q" className="field__control" defaultValue={q} maxLength={80} />
                </div>
                <div className="filters__actions">
                  <Button type="submit" variant="secondary" size="sm">
                    Apply
                  </Button>
                  {q && (
                    <Button variant="quiet" size="sm" onClick={() => set({ q: null })}>
                      Clear
                    </Button>
                  )}
                </div>
              </form>
              {data.items.length === 0 ? (
                <EmptyState title={q || state ? 'No cases match these filters' : 'No cases ready for decision'}>
                  A case enters this queue only when the scrutiny stage and every other mandatory stage of the application’s approved route are complete. An incomplete or ambiguous route keeps it out.
                </EmptyState>
              ) : (
                <div className="table-wrap">
                  <table className="table queue-table">
                    <caption className="sr-only">Decision cases</caption>
                    <thead>
                      <tr>
                        <th scope="col">Application</th>
                        <th scope="col">State</th>
                        <th scope="col">Outcome and appeal</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((c) => (
                        <tr key={c.id}>
                          <td>
                            <Link className="mono link" to={`/app/decisions/${c.id}`}>
                              {c.application.reference}
                            </Link>
                            <div>{c.application.institution_name}</div>
                            <div className="muted">
                              {c.application.category_name} <span className="mono">· {c.application.academic_year}</span>
                            </div>
                            <div className="mono muted">Entered {formatDate(c.opened_at)}</div>
                          </td>
                          <td>
                            <DecisionStateBadge state={c.state} label={c.state_label} />
                            {c.demo && (
                              <div>
                                <Badge tone="warning">DEMO ONLY configuration</Badge>
                              </div>
                            )}
                            {c.blocked && <div className="note">Blocked: see the case for what is missing</div>}
                          </td>
                          <td>
                            {c.outcome_label ? <strong>{c.outcome_label}</strong> : <span className="muted">No decision recorded</span>}
                            {c.appeal_label && <div className="note">Appeal: {c.appeal_label}</div>}
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
          )
        }}
      </ResourceView>
    </>
  )
}
