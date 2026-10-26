import { DemoBanner, VerificationNote } from '../../components/ui/Banners'
import { Alert, Badge, type Tone } from '../../components/ui/feedback'
import { formatDate, formatDay, humanise } from '../../lib/format'
import { ChecklistPanel, HistoryList, RouteList, Section, SourceList } from '../applications/parts'
import type { DeadlinePolicy, Issue, VersionDetail, VersionStatus } from './types'

const STATUS: Record<VersionStatus, { tone: Tone; label: string }> = {
  draft: { tone: 'neutral', label: 'Draft' },
  in_review: { tone: 'warning', label: 'In review' },
  published: { tone: 'success', label: 'Published' },
  retired: { tone: 'neutral', label: 'Retired' },
}

export const VersionStatusBadge = ({ status }: { status: VersionStatus }) => <Badge tone={STATUS[status].tone}>{STATUS[status].label}</Badge>

export const ClassificationBadge = ({ classification }: { classification: 'OFFICIAL' | 'DEMO' }) =>
  classification === 'DEMO' ? <Badge tone="warning">DEMO ONLY</Badge> : <Badge tone="info">Official classification</Badge>

/** Why a version cannot be published yet. An empty list means every gate currently passes. */
export function ReadinessPanel({ issues, status }: { issues: Issue[]; status: VersionStatus }) {
  if (status === 'published' || status === 'retired') return null
  if (issues.length === 0) return <Alert tone="success" title="Every publication check currently passes">An approver can still decline to publish. Checks are re-run when publishing.</Alert>
  return (
    <Alert tone="warning" title={`${issues.length} thing${issues.length > 1 ? 's' : ''} to resolve before this can be published`}>
      <ul className="issue-list">
        {issues.map((i, n) => (
          <li key={i.code + i.path + n}>
            {i.message} <span className="mono muted">[{i.code}]</span>
          </li>
        ))}
      </ul>
    </Alert>
  )
}

function Deadlines({ items }: { items: DeadlinePolicy[] }) {
  if (!items.length) return <p className="note">No deadline policies are defined. Phase 2 only stores definitions; nothing is scheduled or enforced.</p>
  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="sr-only">Deadline policy definitions</caption>
        <thead>
          <tr>
            <th scope="col">Policy</th>
            <th scope="col">Type</th>
            <th scope="col">Trigger</th>
            <th scope="col">Duration</th>
            <th scope="col">Basis</th>
          </tr>
        </thead>
        <tbody>
          {items.map((d) => (
            <tr key={d.policy_key}>
              <td>
                {d.label}
                <br />
                <span className="mono muted">{d.policy_key}</span>
              </td>
              <td>{d.deadline_type === 'STATUTORY' ? 'Statutory' : 'Internal SLA'}</td>
              <td className="mono">{d.trigger_event}</td>
              <td>
                {d.duration_value === null ? (
                  <span className="muted">Not established</span>
                ) : (
                  <>
                    {d.duration_value} {d.duration_unit}
                    <br />
                    <span className="muted">{d.calendar_convention ? `${humanise(d.calendar_convention.toLowerCase())} days` : 'Calendar convention unspecified'}</span>
                  </>
                )}
              </td>
              <td className="mono">{d.clause_ref ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Everything about a version except its actions: used by the detail and review screens. */
export function VersionBody({ v, showReadiness = true }: { v: VersionDetail; showReadiness?: boolean }) {
  return (
    <>
      {v.demo_banner && <DemoBanner text={v.demo_banner} />}
      {v.review_note && v.status === 'draft' && (
        <Alert tone="warning" title="Changes were requested by the reviewer">
          {v.review_note}
        </Alert>
      )}
      {showReadiness && <ReadinessPanel issues={v.publication_issues} status={v.status} />}

      <Section title="Summary" aside={<VerificationNote verification={v.verification} />}>
        <dl className="facts">
          <div>
            <dt>Rule set</dt>
            <dd>
              {v.rule_set.title} <span className="mono muted">· {v.rule_set.key}</span>
            </dd>
          </div>
          <div>
            <dt>Category</dt>
            <dd>{v.category.name}</dd>
          </div>
          <div>
            <dt>Academic years</dt>
            <dd className="mono">{v.academic_years.join(', ') || '—'}</dd>
          </div>
          <div>
            <dt>Effective</dt>
            <dd className="mono">
              {v.effective_from || v.effective_to ? `${formatDay(v.effective_from)} → ${v.effective_to ? formatDay(v.effective_to) : 'open-ended'}` : 'Not date-limited'}
            </dd>
          </div>
          <div>
            <dt>Created by</dt>
            <dd>{v.created_by ?? '—'}</dd>
          </div>
          {v.published_at && (
            <div>
              <dt>Published</dt>
              <dd className="mono">
                {formatDate(v.published_at)}
                {v.published_by ? ` · ${v.published_by}` : ''}
              </dd>
            </div>
          )}
          {v.retired_at && (
            <div>
              <dt>Retired</dt>
              <dd className="mono">
                {formatDate(v.retired_at)}
                {v.retired_by ? ` · ${v.retired_by}` : ''}
              </dd>
            </div>
          )}
          {v.content_hash && (
            <div>
              <dt>Content hash</dt>
              <dd className="mono hash">{v.content_hash}</dd>
            </div>
          )}
        </dl>
        <h3 className="panel__sub">Change summary</h3>
        <p>{v.change_summary || <span className="muted">Not yet written.</span>}</p>
        {v.retirement_reason && (
          <>
            <h3 className="panel__sub">Retirement reason</h3>
            <p>{v.retirement_reason}</p>
          </>
        )}
      </Section>

      <Section title="Sources">
        <SourceList sources={v.sources} />
      </Section>
      <Section title={`Requirement checklist (${v.checklist.length})`}>
        {v.checklist.length === 0 ? <p className="note">The checklist is empty.</p> : <ChecklistPanel items={v.checklist} showStatus={false} />}
      </Section>
      <Section title={`Processing route (${v.route.length} stage${v.route.length === 1 ? '' : 's'})`}>
        <RouteList stages={v.route} />
      </Section>
      <Section title="Deadline policy definitions">
        <Deadlines items={v.deadlines} />
      </Section>
      <Section title="Version history">
        <HistoryList items={v.events.map((e) => ({ at: e.at, actor: e.actor, action: e.note && ['changes_requested', 'retired'].includes(e.action) ? `${e.action} — ${e.note}` : e.action }))} />
      </Section>
    </>
  )
}
