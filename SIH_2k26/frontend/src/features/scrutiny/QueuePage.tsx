import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Button } from '../../components/ui/Button'
import { Alert, EmptyState } from '../../components/ui/feedback'
import { formatDate } from '../../lib/format'
import { useResource } from '../../lib/useResource'
import { ResourceView } from '../admin/shared'
import { PageHeader } from '../shell/PageHeader'
import '../applications/applications.css'
import { CaseStateBadge, RuleTag } from './parts'
import { scrutinyApi } from './scrutinyApi'
import type { QueuePage as QueueData } from './types'
import './scrutiny.css'

/** The order the buckets are shown in. Wording is the server's (`state_labels`); a bucket appears only when it has cases or is active. */
const BUCKETS = ['AWAITING_ASSIGNMENT', 'READY_FOR_SCRUTINY', 'UNDER_SCRUTINY', 'AWAITING_NOTICE_DECISION', 'AWAITING_RESPONSE', 'AWAITING_RESCRUTINY', 'UNDER_RESCRUTINY', 'SCRUTINY_COMPLETED', 'CONFIGURATION_EXCEPTION']

const SCOPE_TEXT: Record<QueueData['scope'], string> = {
  coordinator: 'You see every case in the queue so you can manage assignment and routing. You do not see document contents, findings or notices.',
  reviewer: 'You see only the cases that were assigned to you through the authorised assignment process.',
  issuer: 'You see only the cases that have a notice prepared for an issuing decision.',
}

function Chips({ data, state, exceptions, set }: { data: QueueData; state: string; exceptions: boolean; set: (patch: Record<string, string | null>) => void }) {
  const all = Object.values(data.counts).reduce((a, b) => a + b, 0)
  return (
    <div className="chips" role="group" aria-label="Filter by state">
      <button type="button" className="chip-btn" aria-pressed={!state && !exceptions} onClick={() => set({ state: null, exceptions: null })}>
        All <span className="mono">{all}</span>
      </button>
      {BUCKETS.filter((b) => (data.counts[b] ?? 0) > 0 || b === state).map((b) => (
        <button key={b} type="button" className="chip-btn" aria-pressed={state === b} onClick={() => set({ state: b, exceptions: null })}>
          {data.state_labels[b] ?? b} <span className="mono">{data.counts[b] ?? 0}</span>
        </button>
      ))}
      {(data.processing_exceptions > 0 || exceptions) && (
        <button type="button" className="chip-btn" aria-pressed={exceptions} onClick={() => set({ exceptions: '1', state: null })}>
          Document processing problems <span className="mono">{data.processing_exceptions}</span>
        </button>
      )}
    </div>
  )
}

export default function ScrutinyQueuePage() {
  const [params, setParams] = useSearchParams()
  const state = params.get('state') ?? ''
  const q = params.get('q') ?? ''
  const assigned = params.get('assigned') ?? ''
  const exceptions = params.get('exceptions') === '1'
  const page = Number(params.get('page') ?? '1') || 1

  const [res, reload] = useResource(() => scrutinyApi.queue({ state, q, assigned, processing_exceptions: exceptions, page, page_size: 20 }), [state, q, assigned, exceptions, page])
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
        title="Scrutiny queue"
        description="Submitted applications moving through scrutiny, notices, responses and re-scrutiny. Every figure on this page is counted from real records."
        crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Scrutiny queue' }]}
      />
      <ResourceView resource={res} reload={reload}>
        {(data) => (
          <>
            <Alert tone="info">{SCOPE_TEXT[data.scope]} Completing scrutiny is never an approval.</Alert>
            <Chips data={data} state={state} exceptions={exceptions} set={set} />
            <form
              className="filters"
              role="search"
              onSubmit={(e) => {
                e.preventDefault()
                const f = new FormData(e.currentTarget)
                set({ q: String(f.get('q') ?? '').trim() || null, assigned: String(f.get('assigned') ?? '') || null })
              }}
            >
              <div className="field">
                <label className="field__label" htmlFor="q">
                  Search reference or institution
                </label>
                <input id="q" name="q" className="field__control" defaultValue={q} maxLength={80} />
              </div>
              {data.scope === 'coordinator' && (
                <div className="field">
                  <label className="field__label" htmlFor="assigned">
                    Assignment
                  </label>
                  <select id="assigned" name="assigned" className="field__control" defaultValue={assigned}>
                    <option value="">Any</option>
                    <option value="unassigned">Not assigned</option>
                  </select>
                </div>
              )}
              <div className="filters__actions">
                <Button type="submit" variant="secondary" size="sm">
                  Apply
                </Button>
                {(q || assigned) && (
                  <Button variant="quiet" size="sm" onClick={() => set({ q: null, assigned: null })}>
                    Clear
                  </Button>
                )}
              </div>
            </form>

            {data.items.length === 0 ? (
              <EmptyState title={q || state || assigned || exceptions ? 'No cases match these filters' : 'No cases in your scope yet'}>
                {q || state || assigned || exceptions
                  ? 'Clear a filter to see more.'
                  : 'A case appears here when an application is submitted (coordinators), when you are assigned to it (reviewers), or when a notice is prepared for your decision (issuers).'}
              </EmptyState>
            ) : (
              <div className="table-wrap">
                <table className="table queue-table">
                  <caption className="sr-only">Scrutiny cases</caption>
                  <thead>
                    <tr>
                      <th scope="col">Application</th>
                      <th scope="col">State and assignment</th>
                      <th scope="col">Outstanding and due</th>
                      <th scope="col">Rule version and documents</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((c) => (
                      <tr key={c.id}>
                        <td>
                          <Link className="mono link" to={`/app/scrutiny/${c.id}`}>
                            {c.reference}
                          </Link>
                          <div>{c.institution_name}</div>
                          <div className="muted">
                            {c.category_name} <span className="mono">· {c.academic_year}</span>
                          </div>
                          <div className="mono muted">Submitted {formatDate(c.submitted_at)}</div>
                        </td>
                        <td>
                          <CaseStateBadge state={c.state} label={c.state_label} />
                          {c.round_number && (
                            <div className="mono muted">
                              Round {c.round_number} · {c.round_kind === 'RE_SCRUTINY' ? 're-scrutiny' : 'initial'}
                            </div>
                          )}
                          <div>{c.assignees.length ? c.assignees.join(', ') : <span className="muted">Not assigned</span>}</div>
                        </td>
                        <td>
                          <ul className="plain-list">
                            {c.outstanding_actions.map((a) => (
                              <li key={a}>{a}</li>
                            ))}
                          </ul>
                          {c.due_at && (
                            <div>
                              Due <span className="mono">{formatDate(c.due_at)}</span>
                              {c.due_basis && <div className="note">{c.due_basis}</div>}
                            </div>
                          )}
                        </td>
                        <td>
                          <RuleTag rule={c.rule_version} />
                          <div className="mono">
                            {c.processing.documents} document{c.processing.documents === 1 ? '' : 's'}
                            {c.processing.still_processing > 0 && <div className="muted">{c.processing.still_processing} processing</div>}
                            {c.processing.failed > 0 && <div>{c.processing.failed} failed</div>}
                            {c.processing.review_recommended > 0 && <div className="muted">{c.processing.review_recommended} flagged</div>}
                          </div>
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
