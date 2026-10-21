/**
 * Demo workflow configuration. Stage sequences, SLA values and escalation thresholds are
 * illustrative and configurable; real routing depends on the applicable APH and application category.
 */

export type StageId = 'submitted' | 'scrutiny' | 'rescrutiny' | 'evc' | 'sac' | 'decision'
export type ClockState = 'idle' | 'running' | 'paused' | 'stopped'

export interface StageConfig {
  id: StageId
  name: string
  short: string
  role: string
  /** Demo deadline / SLA text. */
  deadline: string
  description: string
  /** Set when the stage only applies under some conditions. */
  conditional?: string
  /** Position in the 1000×260 pipeline coordinate space. */
  x: number
  y: number
}

export const STAGES: readonly StageConfig[] = [
  {
    id: 'submitted', name: 'Application submitted', short: 'Submitted', role: 'Institution',
    deadline: 'Starts the case', x: 70, y: 120,
    description: 'The institution submits the application and supporting documents. The case receives its ID and is assigned to a scrutiny officer.',
  },
  {
    id: 'scrutiny', name: 'Scrutiny', short: 'Scrutiny', role: 'Scrutiny officer',
    deadline: 'Officer SLA: 10 days (demo)', x: 242, y: 70,
    description: 'The officer checks the application against the applicable rules. If documents are deficient the application is returned, and a written reason must be recorded.',
  },
  {
    id: 'rescrutiny', name: 'Re-scrutiny', short: 'Re-scrutiny', role: 'Scrutiny officer',
    deadline: 'Officer clock resumes (demo policy)', x: 414, y: 165,
    conditional: 'Only when the application was returned for deficiencies',
    description: 'After the institution resubmits, the paused officer SLA clock resumes and scrutiny continues from where it stopped.',
  },
  {
    id: 'evc', name: 'Expert Visit Committee (EVC)', short: 'EVC', role: 'Regional office · EVC panel',
    deadline: 'Visit + report: 21 days (demo)', x: 586, y: 70,
    conditional: 'Applies only where the APH and category require a visit',
    description: 'A committee visit is scheduled and tracked. Reminders, deadline warnings and escalation run against the visit date.',
  },
  {
    id: 'sac', name: 'SAC / SHC review', short: 'SAC / SHC', role: 'Advisory committee',
    deadline: 'Review: 14 days (demo)', x: 758, y: 165,
    conditional: 'Committee stage depends on the applicable APH',
    description: 'The relevant advisory committee reviews the file. Whether this stage exists, and its sequence, is part of the configured workflow.',
  },
  {
    id: 'decision', name: 'Competent Authority decision', short: 'Decision', role: 'Competent Authority',
    deadline: 'Set by regulation, never by automation', x: 930, y: 105,
    description: 'Only the authorised official records the decision. Automation coordinates the work, but never approves or rejects an application.',
  },
]

export const STAGE_BY_ID = Object.fromEntries(STAGES.map((s) => [s.id, s])) as Record<StageId, StageConfig>

export interface JourneyStep {
  stageId: StageId
  title: string
  detail: string
  officerClock: ClockState
  officerDay: number
  institutionClock: ClockState
  institutionDay: number
  /** True while the case is waiting on the institution. */
  paused?: boolean
}

export interface Scenario {
  id: 'returned' | 'clean'
  label: string
  steps: readonly JourneyStep[]
}

export const SLA_POLICY = { officerDays: 10, institutionResponseDays: 15 } as const

export const SCENARIOS: readonly Scenario[] = [
  {
    id: 'returned',
    label: 'Returned for deficiencies',
    steps: [
      { stageId: 'submitted', title: 'Application submitted', detail: 'The institution files AICTE-2026-0042. No clock runs until the case is assigned.', officerClock: 'idle', officerDay: 0, institutionClock: 'idle', institutionDay: 0 },
      { stageId: 'scrutiny', title: 'Scrutiny begins', detail: 'The scrutiny officer starts review. The officer SLA clock starts running.', officerClock: 'running', officerDay: 4, institutionClock: 'idle', institutionDay: 0 },
      { stageId: 'scrutiny', title: 'Returned for deficiencies', detail: 'Reason recorded: "Fire-safety certificate missing". The officer clock pauses and the institution response clock starts.', officerClock: 'paused', officerDay: 4, institutionClock: 'running', institutionDay: 6, paused: true },
      { stageId: 'rescrutiny', title: 'Resubmitted: re-scrutiny', detail: 'The institution responds. Its clock stops and the officer clock resumes from day 4.', officerClock: 'running', officerDay: 6, institutionClock: 'stopped', institutionDay: 9 },
      { stageId: 'evc', title: 'EVC visit', detail: 'Scrutiny is complete. A committee visit is scheduled and tracked against its own deadline.', officerClock: 'stopped', officerDay: 7, institutionClock: 'stopped', institutionDay: 9 },
      { stageId: 'sac', title: 'SAC / SHC review', detail: 'The committee reviews the visit report and the file, per the configured route.', officerClock: 'stopped', officerDay: 7, institutionClock: 'stopped', institutionDay: 9 },
      { stageId: 'decision', title: 'Awaiting authority decision', detail: 'The file reaches the Competent Authority. Only that official can record the decision.', officerClock: 'stopped', officerDay: 7, institutionClock: 'stopped', institutionDay: 9 },
    ],
  },
  {
    id: 'clean',
    label: 'Clean scrutiny',
    steps: [
      { stageId: 'submitted', title: 'Application submitted', detail: 'The institution files the application with complete documents.', officerClock: 'idle', officerDay: 0, institutionClock: 'idle', institutionDay: 0 },
      { stageId: 'scrutiny', title: 'Scrutiny', detail: 'No deficiencies found. The officer clock runs without interruption.', officerClock: 'running', officerDay: 3, institutionClock: 'idle', institutionDay: 0 },
      { stageId: 'evc', title: 'EVC visit', detail: 'Scrutiny completes in 5 days. Re-scrutiny is never triggered, so that stage is skipped.', officerClock: 'stopped', officerDay: 5, institutionClock: 'idle', institutionDay: 0 },
      { stageId: 'sac', title: 'SAC / SHC review', detail: 'The committee reviews the file, per the configured route.', officerClock: 'stopped', officerDay: 5, institutionClock: 'idle', institutionDay: 0 },
      { stageId: 'decision', title: 'Awaiting authority decision', detail: 'The Competent Authority decides. Automation never substitutes for that decision.', officerClock: 'stopped', officerDay: 5, institutionClock: 'idle', institutionDay: 0 },
    ],
  },
]
