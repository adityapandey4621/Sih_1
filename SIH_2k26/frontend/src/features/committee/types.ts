/** Shapes returned by the EVC / SHC endpoints (backend/app/committee_presenters.py). Wording such as state labels comes from the server. */

export type Stage = 'EVC' | 'SHC'

export type CommitteeState =
  | 'SELECTION_PENDING'
  | 'SELECTION_REQUESTED'
  | 'SCHEDULE_PENDING'
  | 'SCHEDULED'
  | 'VISIT_IN_PROGRESS'
  | 'REPORT_SUBMITTED'
  | 'PREPARATION'
  | 'AWAITING_COMPLIANCE'
  | 'COMPLIANCE_RECEIVED'
  | 'UNDER_REVIEW'
  | 'RECOMMENDATION_SUBMITTED'
  | 'COMPLETED'

export interface AppSummary {
  id: string
  reference: string
  institution_name: string
  category_name: string
  academic_year: string
  status: string
}

export interface QueueRow {
  id: string
  stage: Stage
  application: AppSummary
  state: CommitteeState
  state_label: string
  row_version: number
  activated_at: string
  assignees: string[]
  schedule: { status: string; start_at: string; start_local: string | null; timezone: string; mode: string } | null
  blocked: boolean
  blockers: string[]
  demo: boolean
  awaiting_determination: boolean
  completed_at: string | null
}

export interface QueueData {
  scope: 'coordinator' | 'member'
  total: number
  page: number
  page_size: number
  counts: Record<string, number>
  items: QueueRow[]
}

export interface Schedule {
  id: string
  kind: 'VISIT' | 'HEARING'
  sequence: number
  status: 'PROPOSED' | 'CONFIRMED' | 'SUPERSEDED' | 'CANCELLED' | 'COMPLETED'
  start_at: string
  timezone: string
  start_local: string | null
  mode: string
  mode_reason: string | null
  location: string | null
  access_details: string | null
  supersedes_id: string | null
  change_reason: string | null
  created_at: string
  confirmed_at: string | null
  closed_at: string | null
  closed_reason: string | null
  row_version: number
  policy: { classification: string } | null
}

export interface SelectionRequestRow {
  id: string
  request_ref: string
  provider: string
  demo: boolean
  status: 'REQUESTED' | 'COMPLETED' | 'FAILED' | 'INCOMPLETE' | 'CANCELLED'
  requested_at: string
  result_at: string | null
  result_source: string | null
  members: { user_id: string; name: string }[]
  error_code: string | null
  error_detail: string | null
  external_ref: string | null
}

export interface Selection {
  availability: { available: boolean; mode: string; label: string; notice: string; demo: boolean }
  requests: SelectionRequestRow[]
  assignees: { user_id: string; name: string; source: string; demo: boolean; since: string }[]
  min_members: number
}

export interface ModeOption {
  key: string
  label: string
  verification: string
  requires: string[]
  requires_reason: boolean
  clause_ref: string
}

export interface ChecklistRow {
  item_key: string
  label: string
  description: string
  required: boolean
  evidence_required: boolean
  clause_ref: string
  demo?: boolean
}

export interface PolicyView {
  id: string
  key: string
  version: number
  classification: 'DEMO' | 'OFFICIAL'
  title: string
  banner: string | null
  sources: { title: string; clause_ref: string; verification_status: string }[]
  content_hash: string
  modes: ModeOption[]
  unverified_modes: { key: string; label: string; reason: string }[]
  checklist: ChecklistRow[]
  report_sections: { key: string; label: string; required: boolean }[]
  shc: {
    compliance_submission: 'PERMITTED' | 'NOT_PERMITTED' | null
    compliance_clause_ref?: string
    hearing: 'OPTIONAL' | 'REQUIRED' | 'NOT_APPLICABLE' | null
    hearing_modes: ModeOption[]
    recommendation_options: { key: string; label: string }[]
    recommendation_sections: { key: string; label: string; required: boolean }[]
  } | null
  composition: { min_members: number; note: string } | null
}

export interface Observation {
  id: string
  item_key: string
  status: 'OBSERVED' | 'NOT_OBSERVED' | 'EVIDENCE_UNAVAILABLE' | 'UNCERTAIN'
  note: string
  internal_note: string | null
  shortcoming: boolean
  document_refs: { document_id: string; item_key: string; version_number: number; page: number | null }[]
  row_version: number
  recorded_by: string
  updated_at: string
}

export interface EvidenceRow {
  id: string
  kind: 'FILE' | 'LINK'
  item_key: string | null
  description: string
  filename: string | null
  media_type: string | null
  size_bytes: number | null
  sha256: string | null
  url: string | null
  uploaded_at: string
  download: string | null
}

export interface ReportVersion {
  id: string
  kind: 'EVC_REPORT' | 'SHC_RECOMMENDATION'
  version_number: number
  status: 'DRAFT' | 'SUBMITTED'
  content: { sections?: Record<string, string>; option?: string | null }
  row_version: number
  created_at: string
  submitted_at: string | null
  submitted_by: string | null
  created_by: string
  content_hash: string | null
  supersedes_id: string | null
  amendment_reason: string | null
  source_snapshot?: Record<string, unknown> | null
}

export interface Requirement {
  id: string
  position: number
  title: string
  description: string
  requested_evidence: string
  item_key: string | null
  origin: { type: string; id?: string } | null
  source_ref: { key: string; label: string; clause_ref: string | null; verification_status: string | null } | null
  source_ref_status: string
  row_version: number
  round_id: string | null
}

export interface ResponseStaff {
  id: string
  status: 'DRAFT' | 'SUBMITTED'
  explanation: string
  submitted_at: string | null
  items: { requirement_id: string; explanation: string; documents: ({ id: string; item_key: string; version_number: number; filename: string } | null)[] }[]
}

export interface Round {
  id: string
  number: number
  status: string
  instructions: string
  requested_at: string
  response_received_at: string | null
  policy_resolution: { state: string; message: string } | null
  requirements: Requirement[]
  response: ResponseStaff | null
}

export interface ShcFinding {
  id: string
  sequence: number
  requirement_id: string | null
  assessment: 'EVIDENCE_SUPPORTS' | 'EVIDENCE_DOES_NOT_SUPPORT' | 'INCONCLUSIVE'
  text: string
  row_version: number
  by: string
  by_id: string
  at: string
}

export interface Proceeding {
  id: string
  schedule_id: string | null
  held_at: string
  participants: string
  text: string
  by: string
  recorded_at: string
}

export interface Shortcoming {
  origin: string
  id: string
  summary: string
  item_key?: string | null
  sequence?: number
}

export interface NextStage {
  status: 'QUEUED' | 'AWAITING_DETERMINATION' | 'NO_FURTHER_STAGE' | 'EXCEPTION'
  to_stage_key: string | null
  to_stage_label: string | null
  reason: string | null
  options: { stage_key: string; label: string; is_conditional: boolean; condition_description: string | null }[]
  determined_at: string | null
  determination_reason: string | null
}

export interface CaseEvent {
  at: string
  actor: string
  action: string
  from_state: string | null
  to_state: string | null
  reason: string | null
}

export interface CommitteeCaseDetail {
  id: string
  stage: Stage
  application: AppSummary
  state: CommitteeState
  state_label: string
  row_version: number
  scope: ('coordinator' | 'member')[]
  activated_at: string
  completed_at: string | null
  completion: Record<string, unknown> | null
  trigger: {
    stage_key: string
    from_stage_key: string
    source: string
    determination: { reason: string; at: string } | null
    scrutiny_outcome: { outcome: string; approved: boolean } | null
    rule_version: { rule_set: string; number: number; classification: string }
    route_clause: { clause_ref: string | null; label: string; is_conditional: boolean } | null
  }
  policy: PolicyView | null
  blockers: { code: string; message: string }[]
  selection: Selection
  schedule: { active: Schedule | null; history: Schedule[] }
  scrutiny: { state: string | null; completion: { outcome?: string; summary?: string } | null; handoff: { status: string; to_stage_key: string | null; reason: string | null } | null }
  next: NextStage | null
  decision_handoff: { status: string; created_at: string; basis: { note: string; stages: { stage_key: string }[] } } | null
  events: CaseEvent[]
  allowed_actions: string[]
  outstanding_actions: string[]
  not_an_approval: string
  evc?: { observations: Observation[]; evidence: EvidenceRow[]; draft: ReportVersion | null; versions: ReportVersion[] }
  shc?: { shortcomings: Shortcoming[]; prepared: Requirement[]; rounds: Round[]; findings: ShcFinding[]; proceedings: Proceeding[]; draft: ReportVersion | null; versions: ReportVersion[] }
}

export type InstitutionSchedule = Pick<Schedule, 'id' | 'kind' | 'status' | 'start_at' | 'timezone' | 'start_local' | 'mode' | 'location' | 'access_details' | 'closed_reason' | 'change_reason'>

export interface InstitutionStatus {
  evc: {
    state: CommitteeState
    label: string
    schedule: InstitutionSchedule | null
    report: {
      version_number: number
      submitted_at: string
      sections: { key: string; label: string; text: string }[]
      observations: { item_key: string; label: string; status: string; note: string }[]
      demo: boolean
    } | null
    timeline: CaseEvent[]
  } | null
  shc: {
    state: CommitteeState
    label: string
    hearing: InstitutionSchedule | null
    rounds: { id: string; number: number; status: string; requested_at: string }[]
    timeline: CaseEvent[]
  } | null
  ready_for_decision_stage: boolean
  not_a_decision: string
}

export interface ComplianceSummary {
  id: string
  number: number
  status: string
  requested_at: string
  application: { id: string; reference: string }
  case_state: CommitteeState
  can_respond: boolean
}

export interface ComplianceRound {
  id: string
  number: number
  status: string
  case_state: CommitteeState
  instructions: string
  requested_at: string
  response_due_at: string | null
  due_note: string | null
  can_respond: boolean
  demo_notice: string | null
  application: { id: string; reference: string }
  requirements: {
    id: string
    position: number
    title: string
    description: string
    requested_evidence: string
    item_key: string | null
    source_ref: { label: string; clause_ref: string | null } | null
    explanation: string
    documents: ({ id: string; item_key: string; version_number: number; filename: string } | null)[]
  }[]
  response: { id: string; status: 'DRAFT' | 'SUBMITTED'; explanation: string; row_version: number; submitted_at: string | null } | null
  received_note: string | null
}
