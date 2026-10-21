export const SITE = {
  brand: 'Halcyon',
  edition: 'Prototype',
  cta: 'Walk the journey',
  colophon:
    'Demonstration prototype. Institutions, application IDs, dates and SLA values are fictional and configurable. Not affiliated with or endorsed by AICTE.',
} as const

export const NAV_ITEMS = [
  { label: 'Overview', href: '#top' },
  { label: 'Journey', href: '#journey' },
  { label: 'Deadlines', href: '#deadlines' },
  { label: 'Compliance', href: '#compliance' },
  { label: 'Audit', href: '#audit' },
  { label: 'Authority', href: '#authority' },
] as const

export const HERO = {
  eyebrow: 'Approval workflow & institutional compliance',
  headline: 'Every approval. One accountable workflow.',
  accentWords: [3, 4] as const,
  description:
    'A configurable platform for application processing, institutional compliance, deadline monitoring, and transparent administrative oversight.',
  note: 'Demo data throughout. Institutions, IDs and dates are fictional.',
} as const

export interface SceneCopy {
  id: string
  eyebrow: string
  headline: string
  accent: readonly number[]
  intro: string
  note: string
}

export const SCENES = {
  journey: {
    id: 'journey',
    eyebrow: '01 · The application journey',
    headline: 'From submission to decision.',
    accent: [3],
    intro:
      'Applications move through configurable stages. A return for deficiencies pauses the officer\'s SLA clock, starts the institution\'s response clock, and resumes scrutiny when documents are resubmitted.',
    note: 'Illustrative route. Actual routing depends on the applicable APH and application category; dashed stages are conditional.',
  },
  deadlines: {
    id: 'deadlines',
    eyebrow: '02 · Deadlines',
    headline: 'Every deadline has an owner.',
    accent: [4],
    intro:
      'A reminder becomes a deadline warning, then an overdue alert, then an escalation to the next authorised official, until someone acknowledges it.',
    note: 'Thresholds are configurable demo values. Escalation alerts never approve or reject an application.',
  },
  compliance: {
    id: 'compliance',
    eyebrow: '03 · Compliance',
    headline: 'Approval is not the end of the process.',
    accent: [7],
    intro:
      'Undertakings, evidence and document validity stay tracked after approval. Partial submissions remain pending, and verified completion stops unnecessary future reminders.',
    note: 'Evidence only counts once an officer has confirmed it.',
  },
  audit: {
    id: 'audit',
    eyebrow: '04 · Audit trail',
    headline: 'A record of what happened, and who acted.',
    accent: [6, 7],
    intro:
      'Every action is recorded with its time, actor, reason and event ID, and each entry is chained to the one before it.',
    note: 'Verification proves the stored chain is internally consistent under this model. It is not absolute immunity from tampering: an actor who could rewrite every entry and hash would need additional controls such as access restrictions and external anchoring.',
  },
  authority: {
    id: 'authority',
    eyebrow: '05 · Human authority',
    headline: 'Automation supports decisions. It never replaces authority.',
    accent: [5, 6],
    intro:
      'OCR proposes values from scanned documents. An authorised officer confirms or corrects each one, and the Competent Authority explicitly signs off the decision.',
    note: 'Machine-extracted values are suggestions until a human verifies them.',
  },
} as const satisfies Record<string, SceneCopy>

export const CLOSING = {
  headline: 'Less manual follow-up. More accountable processing.',
  accent: [4, 5] as const,
  items: [
    { title: 'Workflow automation', copy: 'Configurable stages and SLA clocks that pause and resume by policy.', href: '#journey' },
    { title: 'Role-based visibility', copy: 'Each official sees the cases, deadlines and escalations that are theirs.', href: '#deadlines' },
    { title: 'Deadline management', copy: 'Reminders, warnings and escalation with a named owner at every step.', href: '#deadlines' },
    { title: 'Institutional compliance', copy: 'Undertakings, evidence and document validity tracked after approval.', href: '#compliance' },
    { title: 'Auditability', copy: 'A chained, timestamped record of every action and who took it.', href: '#audit' },
  ],
} as const
