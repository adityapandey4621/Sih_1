import { Link } from 'react-router-dom'
import { Badge, type Tone } from '../../components/ui/feedback'
import { formatDate, humanise } from '../../lib/format'
import type { ClockTiming, ClockView } from './types'

export const TIMING_TONE: Record<ClockTiming, Tone> = {
  ON_TRACK: 'success',
  DUE_SOON: 'warning',
  OVERDUE: 'error',
  PAUSED: 'info',
  COMPLETED: 'neutral',
  CANCELLED: 'neutral',
  NOT_ESTABLISHED: 'warning',
  NO_DATE: 'neutral',
  UNKNOWN: 'neutral',
}
export const TIMING_LABEL: Record<ClockTiming, string> = {
  ON_TRACK: 'On track',
  DUE_SOON: 'Due soon',
  OVERDUE: 'Past its date',
  PAUSED: 'Paused',
  COMPLETED: 'Finished',
  CANCELLED: 'Cancelled',
  NOT_ESTABLISHED: 'No date established',
  NO_DATE: 'No due date',
  UNKNOWN: 'Not timed',
}
export const KIND_LABEL: Record<string, string> = {
  OFFICER_STAGE: 'Officer-stage service level',
  INSTITUTION_RESPONSE: 'Institution response period',
  EVC: 'Expert visit (EVC)',
  APPEAL: 'Appeal window',
  COMPLIANCE: 'Compliance obligation',
  DOCUMENT_VALIDITY: 'Document validity',
}
export const TYPE_LABEL: Record<string, string> = {
  STATUTORY: 'Statutory / regulatory',
  INTERNAL_SLA: 'Internal service level',
  SPECIFIED: 'Date specified in a decision',
  VALIDITY: 'Validity date',
}

export function TimingBadge({ timing }: { timing: ClockTiming | null }) {
  if (!timing) return null
  return <Badge tone={TIMING_TONE[timing] ?? 'neutral'}>{TIMING_LABEL[timing] ?? humanise(timing)}</Badge>
}

/** "in 3 days" / "2 days ago": computed in the browser from the server's date, for orientation only. The date itself is always shown beside it. */
export function relative(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return ''
  const ms = new Date(iso).getTime() - now.getTime()
  const abs = Math.abs(ms)
  const unit = abs >= 86_400_000 ? [Math.round(abs / 86_400_000), 'day'] : abs >= 3_600_000 ? [Math.round(abs / 3_600_000), 'hour'] : [Math.max(1, Math.round(abs / 60_000)), 'minute']
  const text = `${unit[0]} ${unit[1]}${unit[0] === 1 ? '' : 's'}`
  return ms >= 0 ? `in ${text}` : `${text} ago`
}

export function DueLine({ due, timing }: { due: string | null; timing: ClockTiming | null }) {
  if (!due) return <span className="muted">No date</span>
  return (
    <span>
      <span className="mono">{formatDate(due)}</span> <span className="muted">({relative(due)})</span> <TimingBadge timing={timing} />
    </span>
  )
}

export function ClockTable({ clocks, detailLinks, showInstitution }: { clocks: ClockView[]; detailLinks: boolean; showInstitution: boolean }) {
  return (
    <div className="table-wrap">
      <table className="table queue-table">
        <caption className="sr-only">Deadlines</caption>
        <thead>
          <tr>
            <th scope="col">Deadline</th>
            <th scope="col">Application</th>
            <th scope="col">Date and status</th>
            <th scope="col">Where it comes from</th>
          </tr>
        </thead>
        <tbody>
          {clocks.map((c) => (
            <tr key={c.id}>
              <td>
                {detailLinks ? (
                  <Link className="link" to={`/app/deadlines/${c.id}`}>
                    {c.label}
                  </Link>
                ) : (
                  <strong>{c.label}</strong>
                )}
                <div className="muted">{KIND_LABEL[c.kind] ?? humanise(c.kind)}</div>
                <Badge tone={c.deadline_type === 'STATUTORY' ? 'info' : 'neutral'}>{TYPE_LABEL[c.deadline_type] ?? humanise(c.deadline_type)}</Badge>
              </td>
              <td>
                <span className="mono">{c.application.reference}</span>
                {showInstitution && <div>{c.application.institution_name}</div>}
              </td>
              <td>
                {c.status === 'NOT_ESTABLISHED' ? (
                  <>
                    <TimingBadge timing="NOT_ESTABLISHED" />
                    <div className="note">{c.not_established_reason}</div>
                  </>
                ) : c.status === 'PAUSED' ? (
                  <>
                    <TimingBadge timing="PAUSED" />
                    <div className="note">The date is open while paused; original date {formatDate(c.due_at)}.</div>
                  </>
                ) : c.status === 'COMPLETED' ? (
                  <>
                    <TimingBadge timing="COMPLETED" />
                    <div className="note">{c.completion_reason ? humanise(c.completion_reason) : ''} · {formatDate(c.completed_at)}</div>
                  </>
                ) : (
                  <DueLine due={c.effective_due_at ?? c.due_at} timing={c.timing} />
                )}
                {c.paused_seconds > 0 && c.status !== 'PAUSED' && <div className="note">Includes {Math.round(c.paused_seconds / 3600)} h of approved pause; original date {formatDate(c.due_at)}.</div>}
              </td>
              <td>
                <div>{c.policy.source ? c.policy.source.title : c.policy.authority ? `Cited: ${c.policy.authority}` : '—'}</div>
                {c.policy.demo && <Badge tone="warning">DEMO ONLY</Badge>}
                {c.policy.clause_ref && <div className="mono muted">{c.policy.clause_ref}</div>}
                {c.policy.duration_value && (
                  <div className="muted">
                    {c.policy.duration_value} {c.policy.duration_unit}
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export const fileSize = (n: number) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`)
