/** Demo deadline policy: every threshold is configurable and illustrative. */
export const DEADLINE_POLICY = {
  /** Day offsets are relative to the visit date (0 = due day). */
  reminderDaysBefore: 7,
  approachingDaysBefore: 3,
  minDay: -10,
  maxDay: 10,
  escalations: [
    { level: 1, afterDays: 1, role: 'Regional Head' },
    { level: 2, afterDays: 4, role: 'Bureau Head' },
    { level: 3, afterDays: 8, role: 'Competent Authority' },
  ],
} as const

export const HIERARCHY = ['Assigned officer', ...DEADLINE_POLICY.escalations.map((e) => e.role)] as const

/** Fictional EVC visit date used by the calendar (18 Nov 2026). */
export const VISIT_DATE = new Date(2026, 10, 18)

export type DeadlinePhase = 'scheduled' | 'reminder' | 'approaching' | 'overdue' | 'acknowledged'

export interface DeadlineView {
  phase: DeadlinePhase
  /** 0 = assigned officer; higher = more senior official notified. */
  level: number
  headline: string
  detail: string
}

export function getDeadlineView(day: number, acknowledged: boolean): DeadlineView {
  const { reminderDaysBefore, approachingDaysBefore, escalations } = DEADLINE_POLICY
  const level = escalations.reduce((lvl, e) => (day >= e.afterDays ? e.level : lvl), 0)

  if (acknowledged && day > 0) {
    return {
      phase: 'acknowledged', level,
      headline: 'Acknowledged: escalation paused',
      detail: `${HIERARCHY[level]} acknowledged the overdue visit. The case stays open; the visit still needs a new recorded date.`,
    }
  }
  if (day > 0) {
    return {
      phase: 'overdue', level,
      headline: level === 0 ? `Overdue by ${day} day${day > 1 ? 's' : ''}` : `Escalated to ${HIERARCHY[level]}`,
      detail: level === 0
        ? 'The visit date passed. Escalation starts at the next configured threshold.'
        : `Overdue by ${day} days. ${HIERARCHY[level]} has been notified to act. This is an alert only: no application is approved or rejected.`,
    }
  }
  if (day >= -approachingDaysBefore) {
    return { phase: 'approaching', level: 0, headline: `Deadline approaching: ${day === 0 ? 'due today' : `${-day} days left`}`, detail: 'The assigned officer and EVC panel receive a deadline warning.' }
  }
  if (day >= -reminderDaysBefore) {
    return { phase: 'reminder', level: 0, headline: `Reminder sent: ${-day} days to visit`, detail: 'A reminder is sent to the assigned officer and the committee coordinator.' }
  }
  return { phase: 'scheduled', level: 0, headline: `Scheduled: ${-day} days to visit`, detail: 'The visit is on the calendar. No reminders are due yet.' }
}
