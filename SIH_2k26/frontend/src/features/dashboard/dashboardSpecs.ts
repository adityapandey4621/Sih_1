import {
  Building2,
  ClipboardCheck,
  Gavel,
  Landmark,
  MapPinned,
  Scale,
  ShieldCheck,
  SlidersHorizontal,
  UsersRound,
  Users,
  type LucideIcon,
} from 'lucide-react'

/**
 * Layout and wording of each role's dashboard shell.
 *
 * This describes *structure only*. It never contains rules, durations, counts, eligibility
 * conditions or any data about applications: those arrive from the backend in later phases.
 * Access is decided by `permission`, which the backend grants (backend/app/rbac.py).
 */

export interface DashboardSection {
  title: string
  description: string
  emptyTitle: string
  emptyBody: string
  link?: { label: string; to: string; permission: string }
}

export interface DashboardSpec {
  key: string
  permission: string
  navLabel: string
  title: string
  description: string
  icon: LucideIcon
  note?: string
  sections: DashboardSection[]
}

const none = (what: string) => ({
  emptyTitle: `No ${what} yet`,
  emptyBody: 'Application workflow arrives in a later phase. This area will list real records once they exist.',
})

export const DASHBOARDS: DashboardSpec[] = [
  {
    key: 'institute',
    permission: 'dashboard:institute',
    navLabel: 'My institution',
    title: 'Institution dashboard',
    description: 'Your applications, evidence, requests from scrutiny and obligations in one place.',
    icon: Building2,
    sections: [
      { title: 'My applications', description: 'Applications your institution has started or submitted.', ...none('applications') },
      { title: 'Application status', description: 'Where each application currently stands and who must act next.', ...none('status information') },
      { title: 'Document checklist', description: 'Documents required for each application, and their validity.', ...none('checklists') },
      { title: 'Correction requests', description: 'Deficiencies or clarifications raised during scrutiny.', ...none('correction requests') },
      { title: 'Notifications and deadlines', description: 'Reminders and due dates that apply to your institution.', ...none('notifications') },
      { title: 'Appeals', description: 'Appeals, where your institution is eligible to make one.', ...none('appeals') },
      { title: 'Compliance obligations', description: 'Undertakings and continuing compliance duties.', ...none('obligations') },
    ],
  },
  {
    key: 'scrutiny',
    permission: 'dashboard:scrutiny',
    navLabel: 'Scrutiny',
    title: 'Scrutiny officer dashboard',
    description: 'Applications assigned to you for review, with their open issues and deadlines.',
    icon: ClipboardCheck,
    sections: [
      { title: 'Assigned applications', description: 'Applications allocated to you.', ...none('assigned applications') },
      { title: 'Pending scrutiny', description: 'Applications awaiting your first review.', ...none('pending scrutiny') },
      { title: 'Re-scrutiny queue', description: 'Applications returned after institutional corrections.', ...none('re-scrutiny cases') },
      { title: 'Review-ready cases', description: 'Cases whose scrutiny is complete and ready to move on.', ...none('review-ready cases') },
      { title: 'Unresolved issues', description: 'Open deficiencies across your cases.', ...none('unresolved issues') },
      { title: 'Approaching and overdue deadlines', description: 'Time-bound items that need attention.', ...none('deadlines') },
    ],
  },
  {
    key: 'evc-coordination',
    permission: 'dashboard:evc-coordination',
    navLabel: 'EVC coordination',
    title: 'EVC coordination dashboard',
    description: 'Cases that need an expert visit, and the status of arranging and completing each visit.',
    icon: MapPinned,
    note: 'Member selection will follow the applicable, authorised AICTE mechanism. No assignment algorithm is built into this phase.',
    sections: [
      { title: 'Cases requiring EVC', description: 'Cases for which an expert visit is required.', ...none('cases') },
      { title: 'Selection and scheduling status', description: 'Progress of arranging each visit.', ...none('scheduling records') },
      { title: 'Member assignment status', description: 'Which members have been assigned and whether they have responded.', ...none('assignments') },
      { title: 'Upcoming and completed visits', description: 'Visits scheduled and those already carried out.', ...none('visits') },
      { title: 'Pending reports', description: 'Visit reports not yet submitted.', ...none('pending reports') },
    ],
  },
  {
    key: 'evc-member',
    permission: 'dashboard:evc-member',
    navLabel: 'My visits',
    title: 'EVC member dashboard',
    description: 'Cases you are assigned to visit, with the documents and reports you need.',
    icon: Users,
    sections: [
      { title: 'Assigned cases', description: 'Cases assigned to you.', ...none('assigned cases') },
      { title: 'Visit schedule', description: 'Dates and details of your visits.', ...none('visits') },
      { title: 'Relevant documents', description: 'Records you may consult for each visit.', ...none('documents') },
      { title: 'Report submission', description: 'Where you will submit your visit report.', ...none('reports') },
      { title: 'Pending actions', description: 'Things waiting on you.', ...none('pending actions') },
    ],
  },
  {
    key: 'shc',
    permission: 'dashboard:shc',
    navLabel: 'SHC',
    title: 'SHC dashboard',
    description: 'Compliance and hearing cases, institutional submissions and committee recommendations.',
    icon: Scale,
    sections: [
      { title: 'Compliance and hearing cases', description: 'Cases that call for SHC consideration.', ...none('cases') },
      { title: 'Institutional submissions', description: 'Responses and evidence submitted by institutions.', ...none('submissions') },
      { title: 'Hearing schedule', description: 'Hearings arranged and their dates.', ...none('hearings') },
      { title: 'Recommendations and pending actions', description: 'Recommendations recorded and follow-ups outstanding.', ...none('recommendations') },
    ],
  },
  {
    key: 'executive',
    permission: 'dashboard:executive',
    navLabel: 'Decision queue',
    title: 'Bureau / Executive Committee dashboard',
    description: 'Cases ready for the competent decision process, with the complete record behind each.',
    icon: Landmark,
    note: 'This is a shell. Decisions are made and recorded by authorised officials; no decision logic exists in this phase.',
    sections: [
      { title: 'Cases ready for decision', description: 'Cases that have completed the preceding steps.', ...none('cases') },
      { title: 'Scrutiny and committee reports', description: 'Reports produced earlier in the process.', ...none('reports') },
      { title: 'Complete case record', description: 'The full chronological record of a case.', ...none('case records') },
      { title: 'Decision queue and deadline indicators', description: 'What is waiting for decision and by when.', ...none('queued decisions') },
    ],
  },
  {
    key: 'appeals',
    permission: 'dashboard:appeals',
    navLabel: 'Appeals',
    title: 'SAC appeals dashboard',
    description: 'Appeal cases that meet the applicable eligibility conditions, and their review.',
    icon: Gavel,
    note: 'Eligibility rules and deadlines are not defined here. They will be supplied by an approved, versioned rule set.',
    sections: [
      { title: 'Eligible appeal cases', description: 'Appeals admitted for review.', ...none('appeals') },
      { title: 'Supporting records', description: 'Records relied upon in each appeal.', ...none('records') },
      { title: 'Hearing and review status', description: 'Progress of each appeal.', ...none('review records') },
      { title: 'Pending recommendations and outcomes', description: 'Recommendations awaiting finalisation and recorded outcomes.', ...none('outcomes') },
    ],
  },
  {
    key: 'rules',
    permission: 'dashboard:rules',
    navLabel: 'Rule catalogue',
    title: 'Rules administration',
    description: 'Approved rule versions and the status of configuration that drives the workflow.',
    icon: SlidersHorizontal,
    note: 'Rules administrators maintain approved rule versions. This role carries no regulatory decision permissions.',
    sections: [
      { title: 'Approved rule versions', description: 'Versioned rule sets, their effective dates and approval state.', emptyTitle: 'No rule versions yet', emptyBody: 'The versioned rule catalogue is introduced in Phase 2. Nothing is hard-coded in the meantime.' },
      { title: 'Configuration status', description: 'Whether the system has the configuration it needs to run each process.', emptyTitle: 'Nothing to configure yet', emptyBody: 'Configuration items appear here once the rule catalogue exists.' },
    ],
  },
  {
    key: 'audit',
    permission: 'dashboard:audit',
    navLabel: 'Audit overview',
    title: 'Audit and monitoring',
    description: 'Read-only oversight of what has happened in the system and who did it.',
    icon: ShieldCheck,
    note: 'Auditors have read-only access.',
    sections: [
      {
        title: 'Audit records',
        description: 'Sign-ins, access requests, invitations and account changes recorded so far.',
        emptyTitle: 'Open the audit log',
        emptyBody: 'Phase 1 records authentication and administration events. Application events are added with the workflow.',
        link: { label: 'View audit log', to: '/app/audit-log', permission: 'audit:read' },
      },
      { title: 'Monitoring', description: 'Deadline, SLA and compliance monitoring across cases.', ...none('monitoring data') },
    ],
  },
  {
    key: 'admin',
    permission: 'dashboard:admin',
    navLabel: 'Administration',
    title: 'Platform administration',
    description: 'Review institution access requests and provision accounts.',
    icon: UsersRound,
    note: 'Administrators provision access. This role carries no regulatory decision permissions.',
    sections: [
      {
        title: 'Access requests',
        description: 'Institutions asking for access, awaiting your review.',
        emptyTitle: 'Review requests',
        emptyBody: 'Approving a request issues a single-use invitation to the applicant; it does not create an account directly.',
        link: { label: 'Open access requests', to: '/app/admin/access-requests', permission: 'admin:access-requests' },
      },
      {
        title: 'Users and invitations',
        description: 'Invite authorised officials with an explicit role, and manage existing accounts.',
        emptyTitle: 'Provision users',
        emptyBody: 'Official roles can only be assigned here. They cannot be requested or self-selected.',
        link: { label: 'Open users', to: '/app/admin/users', permission: 'admin:users' },
      },
      {
        title: 'Audit records',
        description: 'Everything done in the system, newest first.',
        emptyTitle: 'Open the audit log',
        emptyBody: 'Sign-ins, invitations, approvals and account changes are recorded here.',
        link: { label: 'View audit log', to: '/app/audit-log', permission: 'audit:read' },
      },
    ],
  },
]

export const dashboardPath = (key: string) => `/app/${key}`
export const findDashboard = (key: string | undefined) => DASHBOARDS.find((d) => d.key === key)
