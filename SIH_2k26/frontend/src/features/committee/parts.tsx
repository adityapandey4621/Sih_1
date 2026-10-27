import { Badge, type Tone } from '../../components/ui/feedback'
import { formatDate, humanise } from '../../lib/format'
import type { CaseEvent, CommitteeState, Schedule } from './types'

const TONE: Record<CommitteeState, Tone> = {
  SELECTION_PENDING: 'warning',
  SELECTION_REQUESTED: 'warning',
  SCHEDULE_PENDING: 'warning',
  SCHEDULED: 'info',
  VISIT_IN_PROGRESS: 'info',
  REPORT_SUBMITTED: 'info',
  PREPARATION: 'warning',
  AWAITING_COMPLIANCE: 'warning',
  COMPLIANCE_RECEIVED: 'info',
  UNDER_REVIEW: 'info',
  RECOMMENDATION_SUBMITTED: 'info',
  COMPLETED: 'success',
}

/** The wording is the server's `label`; only the tone is chosen here. The label is always shown, never colour alone. */
export const CommitteeStateBadge = ({ state, label }: { state: CommitteeState; label: string }) => <Badge tone={TONE[state] ?? 'neutral'}>{label}</Badge>

export const SCHEDULE_TONE: Record<Schedule['status'], Tone> = { PROPOSED: 'warning', CONFIRMED: 'success', SUPERSEDED: 'neutral', CANCELLED: 'neutral', COMPLETED: 'info' }
export const SCHEDULE_WORDS: Record<Schedule['status'], string> = { PROPOSED: 'Proposed — not yet confirmed', CONFIRMED: 'Confirmed', SUPERSEDED: 'Replaced by a later schedule', CANCELLED: 'Cancelled', COMPLETED: 'Took place' }

/** "2030-03-04T10:00" + "Asia/Kolkata" -> "4 Mar 2030, 10:00 (Asia/Kolkata)". The time is shown as it was entered, in the zone it was entered in. */
export function localTime(startLocal: string | null | undefined, tz: string): string {
  if (!startLocal) return '—'
  const [d = '', t = ''] = startLocal.split('T')
  const date = new Date(`${d}T00:00:00`)
  const day = Number.isNaN(date.getTime()) ? d : date.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
  return `${day}, ${t} (${tz})`
}

export const MODE_WORDS: Record<string, string> = { ONLINE: 'Online', PHYSICAL: 'Physical (in person)', HYBRID: 'Hybrid' }

export const ASSESSMENT_WORDS: Record<string, string> = {
  EVIDENCE_SUPPORTS: 'The evidence supports it',
  EVIDENCE_DOES_NOT_SUPPORT: 'The evidence does not support it',
  INCONCLUSIVE: 'Inconclusive',
}
export const OBSERVATION_WORDS: Record<string, string> = {
  OBSERVED: 'Observed',
  NOT_OBSERVED: 'Not observed',
  EVIDENCE_UNAVAILABLE: 'Evidence unavailable',
  UNCERTAIN: 'Uncertain',
}

export function EventList({ events }: { events: CaseEvent[] }) {
  if (events.length === 0) return <p className="note">No events yet.</p>
  return (
    <ol className="history">
      {events.map((e, i) => (
        <li key={`${e.at}-${i}`} className="history__item">
          <span className="mono history__at">{formatDate(e.at)}</span>
          <span>
            <strong>{humanise(e.action)}</strong> — {e.actor}
            {e.from_state && e.to_state && e.from_state !== e.to_state ? <span className="muted"> ({humanise(e.from_state)} → {humanise(e.to_state)})</span> : null}
            {e.reason && <em> — {e.reason}</em>}
          </span>
        </li>
      ))}
    </ol>
  )
}

/** What the recorded configuration permits, with where each part comes from; DEMO configuration is always labelled. */
export const stageWord = (stage: 'EVC' | 'SHC' | 'SAC') => (stage === 'EVC' ? 'Expert visit (EVC)' : stage === 'SHC' ? 'Standing Hearing Committee (SHC)' : 'Standing Appellate Committee (SAC)')
