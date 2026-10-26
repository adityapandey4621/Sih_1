/**
 * Copy for the public landing page.
 *
 * Everything here is descriptive and illustrative. It is not a statement of AICTE rules:
 * no durations, thresholds, categories or eligibility conditions appear, because those
 * will come from an approved, versioned rule catalogue (see docs/ROADMAP).
 */

export interface RouteStage {
  id: string
  label: string
  short: string
  actor: string
  summary: string
  evidence: string
  conditional?: boolean
}

export const ROUTE_STAGES: RouteStage[] = [
  {
    id: 'intake',
    label: 'Intake',
    short: 'Application and documents received',
    actor: 'Institution',
    summary: 'The institution submits its application and supporting documents against a checklist.',
    evidence: 'Each submission is time-stamped and attributed to the person who made it.',
  },
  {
    id: 'evidence',
    label: 'Evidence checks',
    short: 'Completeness and validity reviewed',
    actor: 'System flags · officer confirms',
    summary: 'Documents are checked for completeness and validity. Gaps are raised as deficiencies for the institution to correct.',
    evidence: 'Automated flags are suggestions. A named officer confirms or dismisses each one.',
  },
  {
    id: 'scrutiny',
    label: 'Scrutiny',
    short: 'Review by the scrutiny officer',
    actor: 'Scrutiny officer',
    summary: 'The scrutiny officer reviews the case and, after corrections, reviews it again until the issues are resolved.',
    evidence: 'Every finding and its resolution is part of the case record.',
  },
  {
    id: 'committee',
    label: 'Committee processes',
    short: 'Expert visit and hearing as the case requires',
    actor: 'Expert and hearing committees',
    summary: 'Where the case calls for it, an expert visit and a compliance hearing are arranged and their reports recorded.',
    evidence: 'These steps are conditional: cases that do not need them move straight on.',
    conditional: true,
  },
  {
    id: 'decision',
    label: 'Decision',
    short: 'Recorded by authorised officials',
    actor: 'Competent authority',
    summary: 'The competent authority takes the decision. The system presents the full record and captures the outcome; it does not decide.',
    evidence: 'The decision, who took it and the record it rested on are preserved together.',
  },
  {
    id: 'after',
    label: 'After the decision',
    short: 'Appeals and continuing compliance',
    actor: 'Appeals committee · institution',
    summary: 'Eligible appeals are heard, and undertakings, document validity and deadlines continue to be tracked.',
    evidence: 'Reminders and due dates are generated from approved rules, not from the interface.',
  },
]

export interface OverviewItem {
  title: string
  body: string
  status: string
}

export const OVERVIEW: OverviewItem[] = [
  {
    title: 'Application and document intake',
    body: 'One place for an institution to submit an application and its evidence, against a checklist generated from the applicable approved rules.',
    status: 'Planned · Phase 2',
  },
  {
    title: 'Document intelligence and deficiency handling',
    body: 'Documents are read for completeness and validity. Problems become clear, trackable correction requests rather than scattered correspondence.',
    status: 'Planned · Phase 3',
  },
  {
    title: 'Human-led scrutiny and conditional committee processes',
    body: 'Scrutiny, re-scrutiny, expert visits and hearings follow the official process, each step assigned to an accountable person.',
    status: 'Planned · Phases 3–5',
  },
  {
    title: 'Decisions, appeals and continuing compliance',
    body: 'Authorised decisions are recorded with their supporting record. Eligible appeals and later obligations are followed through.',
    status: 'Planned · Phase 6',
  },
  {
    title: 'Deadlines, notifications and audit trail',
    body: 'Clocks, reminders and a durable record of who did what and when. The audit trail already covers sign-ins and account administration.',
    status: 'Audit foundation built · deadlines Phase 7',
  },
]

export interface Assurance {
  term: string
  detail: string
}

export const ASSURANCES: Assurance[] = [
  { term: 'Decisions', detail: 'Taken by authorised officials. The system supports, records and reminds; it never decides.' },
  { term: 'Access', detail: 'Institutions request access and an administrator reviews it. Official roles are provisioned by invitation and cannot be self-selected.' },
  { term: 'Sessions', detail: 'Server-managed sessions in HttpOnly cookies, with CSRF protection and idle and absolute expiry. No tokens in browser storage.' },
  { term: 'Passwords', detail: 'Stored only as Argon2id hashes. Repeated failed sign-ins are throttled.' },
  { term: 'Traceability', detail: 'Sign-ins, access requests, invitations and account changes are recorded and visible to auditors.' },
]
