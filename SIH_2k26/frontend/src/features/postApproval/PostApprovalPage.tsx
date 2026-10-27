import { useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Alert, EmptyState } from '../../components/ui/feedback'
import { SelectField, TextAreaField, TextField } from '../../components/ui/Field'
import { ApiError } from '../../lib/api'
import { useResource } from '../../lib/useResource'
import { ResourceView } from '../admin/shared'
import { useAuth } from '../auth/AuthContext'
import { Section } from '../applications/parts'
import { PageHeader } from '../shell/PageHeader'
import '../applications/applications.css'
import '../scrutiny/scrutiny.css'
import '../committee/committee.css'
import './postApproval.css'
import { StatusBadge } from './ObligationPage'
import { DeadlinesPanel } from './DeadlinesPage'
import { DueLine } from './parts'
import { newKey, postApprovalApi } from './postApprovalApi'
import { ValidityCard } from './ValidityPanels'
import type { ObligationStatus } from './types'

type Tab = 'obligations' | 'validity' | 'deadlines'
const TABS: [Tab, string][] = [
  ['obligations', 'Obligations'],
  ['validity', 'Document validity'],
  ['deadlines', 'Deadlines'],
]
const STATUS_BUCKETS: [ObligationStatus, string][] = [
  ['AWAITING_EVIDENCE', 'Awaiting evidence'],
  ['EVIDENCE_SUBMITTED', 'Awaiting verification'],
  ['CORRECTION_REQUESTED', 'Correction requested'],
  ['VERIFIED', 'Verified'],
  ['NOT_MET', 'Not met'],
  ['WITHDRAWN', 'Withdrawn'],
]

// ---- record an obligation (Regulation Bureau) -------------------------------------------------------------------------

function DefineObligation({ onDone }: { onDone: () => void }) {
  const [search, setSearch] = useState('')
  const [cases] = useResource(() => postApprovalApi.cases(search), [search])
  const [verifiers] = useResource(() => postApprovalApi.verifiers(), [])
  const [appId, setAppId] = useState('')
  const [f, setF] = useState({ title: '', condition_text: '', condition_ref: '', required_evidence: '', verifier_id: '', basis: 'NONE', policy_key: '', due_at: '', authority: '', source: 'EC_DECISION' })
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const key = useRef(newKey('obl'))
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value })
  const ready = appId && f.title.trim().length >= 3 && f.condition_text.trim().length >= 5 && f.required_evidence.trim().length >= 5 && f.verifier_id && (f.basis !== 'SPECIFIED' || (f.due_at && f.authority.trim().length >= 5)) && (f.basis !== 'POLICY' || f.policy_key.trim())
  async function submit() {
    setBusy(true)
    setError(null)
    try {
      await postApprovalApi.define(appId, {
        source: f.source as 'EC_DECISION' | 'COUNCIL_APPEAL_DECISION',
        title: f.title, condition_text: f.condition_text, condition_ref: f.condition_ref.trim() || null, required_evidence: f.required_evidence, verifier_id: f.verifier_id,
        due: { basis: f.basis as 'POLICY' | 'SPECIFIED' | 'NONE', policy_key: f.basis === 'POLICY' ? f.policy_key.trim() : null, due_at: f.basis === 'SPECIFIED' ? new Date(f.due_at).toISOString() : null, authority: f.basis === 'SPECIFIED' ? f.authority : null },
        idempotency_key: key.current,
      })
      setConfirm(false)
      onDone()
    } catch (e) {
      setConfirm(false)
      setError(e instanceof ApiError ? e : new ApiError(0, 'Unexpected error.'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Section title="Record an obligation">
      <Alert tone="info">
        You are recording a condition that the decision places on the institution, exactly as it appears in the decision. The system derives no obligation from a decision. Its date is computed from a configured policy, taken from the decision with a citation, or left empty.
      </Alert>
      <TextField label="Find an application whose outcome was issued" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Application reference" maxLength={80} />
      {cases.status === 'ready' && cases.data.items.length === 0 && <p className="note">No application with an issued outcome matches.</p>}
      {cases.status === 'ready' && cases.data.items.length > 0 && (
        <SelectField
          label="Application"
          value={appId}
          onChange={(e) => setAppId(e.target.value)}
          options={cases.data.items.map((c) => ({ value: c.application_id, label: `${c.reference} — ${c.institution_name} — ${c.outcome_label ?? ''}` }))}
        />
      )}
      {cases.status === 'ready' && cases.data.items.find((c) => c.application_id === appId)?.council_appeal_outcome && (
        <SelectField
          label="The condition follows from"
          value={f.source}
          onChange={set('source')}
          placeholder="Choose"
          options={[
            { value: 'EC_DECISION', label: 'The Executive Committee’s issued decision' },
            { value: 'COUNCIL_APPEAL_DECISION', label: 'The Council’s decision on the appeal' },
          ]}
          hint="You choose; the system does not infer which decision a condition belongs to."
        />
      )}
      <TextField label="Title" value={f.title} onChange={set('title')} maxLength={200} />
      <TextAreaField label="Condition, as stated in the decision" value={f.condition_text} onChange={set('condition_text')} maxLength={8000} />
      <TextField label="Where it appears in the decision" optional value={f.condition_ref} onChange={set('condition_ref')} maxLength={300} />
      <TextAreaField label="Evidence the institution should submit" value={f.required_evidence} onChange={set('required_evidence')} maxLength={8000} />
      {verifiers.status === 'ready' && <SelectField label="Verifier" value={f.verifier_id} onChange={set('verifier_id')} options={verifiers.data.items.map((v) => ({ value: v.id, label: v.name }))} hint="Only this person can verify the evidence." />}
      <SelectField
        label="Due date"
        value={f.basis}
        onChange={set('basis')}
        placeholder="No due date"
        options={[
          { value: 'NONE', label: 'No due date (none is assumed)' },
          { value: 'SPECIFIED', label: 'A date the decision specifies' },
          { value: 'POLICY', label: 'From a configured deadline policy' },
        ]}
      />
      {f.basis === 'SPECIFIED' && (
        <>
          <TextField label="Date and time" type="datetime-local" value={f.due_at} onChange={set('due_at')} />
          <TextField label="Where the decision states this date" value={f.authority} onChange={set('authority')} maxLength={300} />
        </>
      )}
      {f.basis === 'POLICY' && <TextField label="Policy key" value={f.policy_key} onChange={set('policy_key')} maxLength={80} hint="The key of a deadline policy on the application’s approved configuration. If it cannot establish a date, nothing is recorded." />}
      {error && <Alert tone="error">{error.message}</Alert>}
      <Button disabled={!ready} onClick={() => setConfirm(true)}>
        Record the obligation
      </Button>
      <ConfirmDialog open={confirm} title="Record this obligation?" confirmLabel="Record" busy={busy} onCancel={() => setConfirm(false)} onConfirm={submit}>
        <p>The institution and the verifier are notified in the application. Nothing is sent by email or SMS.</p>
      </ConfirmDialog>
    </Section>
  )
}

// ---- the tabs ----------------------------------------------------------------------------------------------------------

function ObligationsTab({ official, canDefine }: { official: boolean; canDefine: boolean }) {
  const [params, setParams] = useSearchParams()
  const status = params.get('status') ?? ''
  const q = params.get('q') ?? ''
  const due = params.get('due') ?? ''
  const page = Number(params.get('page') ?? '1') || 1
  const [res, reload] = useResource(() => postApprovalApi.obligations({ status, q, due, page }), [status, q, due, page])
  const [adding, setAdding] = useState(false)
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
      {(data) => {
        const all = Object.values(data.counts).reduce((a, b) => a + b, 0)
        return (
          <>
            <Alert tone="info">
              Submitting evidence is not verification: an obligation is verified only when the assigned verifier records that outcome. Recording an outcome revokes nothing and imposes nothing; any consequence is decided by the competent authority.
            </Alert>
            {canDefine && (adding ? <DefineObligation onDone={() => { setAdding(false); reload() }} /> : <Button onClick={() => setAdding(true)}>Record an obligation</Button>)}
            <div className="chips" role="group" aria-label="Filter by status">
              <button type="button" className="chip-btn" aria-pressed={!status} onClick={() => set({ status: null })}>
                All <span className="mono">{all}</span>
              </button>
              {STATUS_BUCKETS.filter(([k]) => (data.counts[k] ?? 0) > 0 || k === status).map(([k, label]) => (
                <button key={k} type="button" className="chip-btn" aria-pressed={status === k} onClick={() => set({ status: k })}>
                  {label} <span className="mono">{data.counts[k] ?? 0}</span>
                </button>
              ))}
            </div>
            <form
              className="filters"
              role="search"
              onSubmit={(e) => {
                e.preventDefault()
                const fd = new FormData(e.currentTarget)
                set({ q: String(fd.get('q') ?? '').trim() || null, due: String(fd.get('due') ?? '') || null })
              }}
            >
              <div className="field">
                <label className="field__label" htmlFor="ob-q">
                  Search
                </label>
                <input id="ob-q" name="q" className="field__control" defaultValue={q} maxLength={80} placeholder="Reference or title" />
              </div>
              <div className="field">
                <label className="field__label" htmlFor="ob-due">
                  Timing
                </label>
                <select id="ob-due" name="due" className="field__control" defaultValue={due}>
                  <option value="">Any</option>
                  <option value="overdue">Past its date</option>
                  <option value="soon">Due soon</option>
                  <option value="ontrack">On track</option>
                </select>
              </div>
              <div className="filters__actions">
                <Button type="submit" variant="secondary" size="sm">
                  Apply
                </Button>
                {(q || due) && (
                  <Button variant="quiet" size="sm" onClick={() => set({ q: null, due: null })}>
                    Clear
                  </Button>
                )}
              </div>
            </form>
            {data.items.length === 0 ? (
              <EmptyState title={q || status || due ? 'No obligations match these filters' : official ? 'No obligations recorded yet' : 'You have no obligations'}>
                {official ? 'An obligation is recorded by the Regulation Bureau against an issued decision.' : 'If a decision places a condition on your institution, it appears here with the evidence required and any date that applies.'}
              </EmptyState>
            ) : (
              <div className="table-wrap">
                <table className="table queue-table">
                  <caption className="sr-only">Obligations</caption>
                  <thead>
                    <tr>
                      <th scope="col">Obligation</th>
                      <th scope="col">Application</th>
                      <th scope="col">Status</th>
                      <th scope="col">Due</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((o) => (
                      <tr key={o.id}>
                        <td>
                          <Link className="link" to={`/app/post-approval/obligations/${o.id}`}>
                            {o.title}
                          </Link>
                          <div className="mono muted">{o.reference}</div>
                          {official && o.verifier && <div className="muted">Verifier: {o.verifier}</div>}
                        </td>
                        <td>
                          <span className="mono">{o.application.reference}</span>
                          {official && <div>{o.application.institution_name}</div>}
                        </td>
                        <td>
                          <StatusBadge status={o.status} label={o.status_label} />
                        </td>
                        <td>{o.due_at ? <DueLine due={o.effective_due_at ?? o.due_at} timing={['VERIFIED', 'NOT_MET', 'WITHDRAWN'].includes(o.status) ? 'COMPLETED' : o.timing} /> : <span className="muted">No due date</span>}</td>
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
                Page {data.page} of {Math.max(1, Math.ceil(data.total / data.page_size))} · {data.total} obligation{data.total === 1 ? '' : 's'}
              </span>
              <Button variant="quiet" size="sm" disabled={page * data.page_size >= data.total} onClick={() => set({ page: String(page + 1) })}>
                Next
              </Button>
            </nav>
          </>
        )
      }}
    </ResourceView>
  )
}

function ValidityTab({ official }: { official: boolean }) {
  const [params, setParams] = useSearchParams()
  const status = params.get('vstatus') ?? ''
  const [res, reload] = useResource(() => postApprovalApi.validity({ status }), [status])
  const filters: [string, string][] = [['', 'All'], ['needs_review', 'Awaiting review'], ['expiring', 'Expiring or expired'], ['resolved', 'No longer tracked']]
  return (
    <>
      <div className="chips" role="group" aria-label="Filter documents">
        {filters.map(([k, label]) => (
          <button
            key={k}
            type="button"
            className="chip-btn"
            aria-pressed={status === k}
            onClick={() => {
              const next = new URLSearchParams(params)
              k ? next.set('vstatus', k) : next.delete('vstatus')
              setParams(next, { replace: true })
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <ResourceView resource={res} reload={reload}>
        {(data) =>
          data.items.length === 0 ? (
            <EmptyState title={status ? 'No documents match this filter' : 'No documents are tracked for validity'}>
              {official
                ? 'A document is tracked when its approved checklist marks a date field as a validity date and the submitted document has been read.'
                : 'Where a document you submitted has a validity period that matters (for example a lease), it is listed here with its confirmed date and any renewal.'}
            </EmptyState>
          ) : (
            <>
              {data.items.map((it) => (
                <ValidityCard key={it.id} item={it} onChanged={reload} />
              ))}
            </>
          )
        }
      </ResourceView>
    </>
  )
}

export default function PostApprovalPage() {
  const { can } = useAuth()
  const official = can('compliance:queue')
  const [params, setParams] = useSearchParams()
  const tab = (TABS.find(([k]) => k === params.get('tab'))?.[0] ?? 'obligations') as Tab
  return (
    <>
      <PageHeader
        title={official ? 'Compliance, validity and deadlines' : 'Obligations and deadlines'}
        description={
          official
            ? 'Conditions that follow an issued decision, document expiry dates, and the deadlines that apply. Evidence is verified by a person; deadlines raise alerts and change nothing else.'
            : 'Conditions placed on your institution after a decision, the validity of your documents, and the dates that apply to you.'
        }
        crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Obligations and deadlines' }]}
      />
      <div className="tabs" role="tablist" aria-label="Sections">
        {TABS.map(([k, label]) => (
          <button
            key={k}
            type="button"
            role="tab"
            id={`tab-${k}`}
            aria-selected={tab === k}
            aria-controls="tab-panel"
            className="tabs__tab"
            onClick={() => {
              const next = new URLSearchParams()
              next.set('tab', k)
              setParams(next, { replace: true })
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div id="tab-panel" role="tabpanel" aria-labelledby={`tab-${tab}`}>
        {tab === 'obligations' && <ObligationsTab official={official} canDefine={can('compliance:define')} />}
        {tab === 'validity' && <ValidityTab official={official} />}
        {tab === 'deadlines' && <DeadlinesPanel official={official} />}
      </div>
    </>
  )
}

