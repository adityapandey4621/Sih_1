/** Mirrors backend/app/schemas_scrutiny.py. State labels come from the server; the UI never maps workflow enums to wording itself, except for the badge tone. */

export type CaseState =
  | 'AWAITING_ASSIGNMENT'
  | 'READY_FOR_SCRUTINY'
  | 'UNDER_SCRUTINY'
  | 'AWAITING_NOTICE_DECISION'
  | 'AWAITING_RESPONSE'
  | 'AWAITING_RESCRUTINY'
  | 'UNDER_RESCRUTINY'
  | 'SCRUTINY_COMPLETED'
  | 'CONFIGURATION_EXCEPTION'

export interface RuleVersionRef {
  rule_set: string
  number: number
  classification: string
  demo_banner: string | null
}

export interface ProcessingSummary {
  documents: number
  still_processing: number
  failed: number
  review_recommended: number
}

export interface QueueItem {
  id: string
  application_id: string
  reference: string
  institution_name: string
  category_name: string
  academic_year: string
  state: CaseState
  state_label: string
  assignees: string[]
  submitted_at: string | null
  updated_at: string
  outstanding_actions: string[]
  due_at: string | null
  due_basis: string | null
  rule_version: RuleVersionRef
  processing: ProcessingSummary
  round_number: number | null
  round_kind: string | null
  exception: string | null
  handoff_status: string | null
  handoff_stage: string | null
  row_version: number
}

export interface QueuePage {
  items: QueueItem[]
  total: number
  page: number
  page_size: number
  counts: Record<string, number>
  state_labels: Record<string, string>
  processing_exceptions: number
  scope: 'coordinator' | 'reviewer' | 'issuer'
}

export interface Selection {
  mode: string
  available: boolean
  label: string
  notice: string
}

export interface Assignment {
  id: string
  user_id: string
  user_name: string
  source: string
  is_demo: boolean
  made_by: string
  selection_ref: Record<string, unknown> | null
  reason: string | null
  created_at: string
  revoked_at: string | null
  revoked_reason: string | null
}

export interface Round {
  id: string
  number: number
  kind: 'INITIAL' | 'RE_SCRUTINY'
  status: 'OPEN' | 'NOTICE_ISSUED' | 'COMPLETED'
  opened_at: string
  started_at: string | null
  closed_at: string | null
  triggered_by_notice: string | null
  outcome: string | null
  outcome_reason: string | null
  review_notes: string | null
  review_notes_at: string | null
  row_version: number
}

export type FindingState = 'OPEN' | 'NOTICED' | 'UNRESOLVED' | 'RESOLVED' | 'NOT_SUPPORTED'

export interface FindingEvent {
  at: string
  actor: string
  action: string
  from_state: string | null
  to_state: string | null
  reason: string | null
  round_number: number
}

export interface SourceRef {
  key: string
  label: string
  clause_ref: string | null
  verification_status: string | null
}

export interface ReviewFinding {
  id: string
  sequence: number
  round_number: number
  item_key: string | null
  item_label: string | null
  document_id: string | null
  document_version: number | null
  document_current_version: number | null
  document_superseded: boolean
  page_number: number | null
  issue_type: string
  issue_label: string
  description: string
  institution_text: string | null
  requested_correction: string | null
  source_ref: SourceRef | null
  source_ref_status: 'ESTABLISHED' | 'NEEDS_VERIFICATION'
  preliminary_finding_id: string | null
  visibility: 'INTERNAL_ONLY' | 'ELIGIBLE_FOR_NOTICE'
  state: FindingState
  state_reason: string | null
  created_by: string
  created_at: string
  updated_at: string
  row_version: number
  notice_reference: string | null
  history: FindingEvent[]
}

export interface ResponseDoc {
  document_id: string
  version_number: number
  filename: string
  uploaded_at: string
  current: boolean
  state_label: string
}

export interface StaffNoticeItem {
  id: string
  position: number
  finding_id: string
  finding_state: string
  title: string
  description: string
  requested_correction: string
  item_key: string | null
  document_id: string | null
  document_version: number | null
  document_current_version: number | null
  page_number: number | null
  source_ref: SourceRef | null
  source_ref_status: string
  response_state: 'NOT_RESPONDED' | 'DRAFT' | 'SUBMITTED' | 'REVIEWED'
  response_explanation: string
  response_documents: ResponseDoc[]
}

export type NoticeStatus = 'DRAFT' | 'IN_REVIEW' | 'ISSUED' | 'RESPONSE_RECEIVED' | 'CLOSED' | 'WITHDRAWN'

export interface StaffNotice {
  id: string
  reference: string
  sequence: number
  previous_reference: string | null
  status: NoticeStatus
  round_number: number
  summary: string
  items: StaffNoticeItem[]
  prepared_by: string
  issued_by: string | null
  issuer_role: string | null
  issued_at: string | null
  issue_reason: string | null
  returned_reason: string | null
  withdrawn_reason: string | null
  response_due_at: string | null
  policy_resolution: PolicyResolution | null
  template: { key: string; label: string; demo: boolean } | null
  content_hash: string | null
  authority: Record<string, string> | null
  row_version: number
  response: { id: string; status: string; explanation: string; submitted_by: string | null; submitted_at: string | null; after_due_date: boolean } | null
}

export interface PolicyResolution {
  state: string
  message: string
  policy_key: string | null
  duration_value: number | null
  duration_unit: string | null
  calendar_convention: string | null
  demo: boolean
  source: { title: string; verification_status: string } | null
}

export interface NoticePreview {
  notice_id: string
  content: {
    reference: string
    sequence: number
    previous_notice_reference: string | null
    application: { reference: string; institution: string; category: string; academic_year: string }
    rule_version: { rule_set: string; number: number; classification: string }
    demo_notice: string | null
    template: { key: string; label: string; demo: boolean } | null
    summary: string
    items: { position: number; title: string; description: string; requested_correction: string; item_key: string | null; document_version: number | null; page_number: number | null; source_ref: SourceRef | null; source_ref_status: string }[]
  }
  content_hash: string
  policy: PolicyResolution
  blockers: { code: string; message: string }[]
  unsourced_positions: number[]
  can_issue: boolean
  row_version: number
  notice_status: string
}

export interface SourceOption {
  key: string
  label: string
  clause_ref: string | null
  verification_status: string | null
  origin: string
}

export interface CaseEvent {
  id: string
  at: string
  actor: string
  action: string
  object_type: string | null
  object_id: string | null
  from_state: string | null
  to_state: string | null
  reason: string | null
  detail: Record<string, unknown> | null
}

export interface Handoff {
  id: string
  status: 'QUEUED' | 'AWAITING_DETERMINATION' | 'NO_FURTHER_STAGE' | 'EXCEPTION'
  from_stage_key: string | null
  to_stage_key: string | null
  to_stage_label: string | null
  reason: string | null
  options: { stage_key: string; label: string; is_conditional: boolean; condition_description: string | null }[]
  determined_by: string | null
  determined_at: string | null
  determination_reason: string | null
  created_at: string
  module_available: boolean
}

export interface ChecklistRow {
  item_key: string
  label: string
  explanation: string
  required: boolean
  item_type: string
  clause_ref: string | null
  answer: unknown
  document_id: string | null
  document_version: number | null
  document_filename: string | null
}

export interface CaseDetail {
  queue: QueueItem
  scope: ('reviewer' | 'issuer' | 'coordinator')[]
  selection: Selection
  assignments: Assignment[]
  rounds: Round[]
  findings: ReviewFinding[]
  notices: StaffNotice[]
  source_options: SourceOption[]
  issue_types: Record<string, string>
  events: CaseEvent[]
  allowed_actions: string[]
  completion: Record<string, unknown> | null
  handoff: Handoff | null
  checklist: ChecklistRow[]
  not_an_approval: string
}

export interface Assignable {
  id: string
  name: string
  email: string
  active_cases: number
}

// ---- institution side ----

export interface InstResponseDoc extends ResponseDoc {}

export interface InstItem {
  id: string
  position: number
  title: string
  description: string
  requested_correction: string
  item_key: string | null
  item_label: string | null
  accepts_files: boolean
  document_version: number | null
  document_current_version: number | null
  page_number: number | null
  source_label: string | null
  source_note: string
  response_state: 'NOT_RESPONDED' | 'DRAFT' | 'SUBMITTED' | 'REVIEWED'
  response_explanation: string
  response_documents: InstResponseDoc[]
}

export interface InstNotice {
  id: string
  reference: string
  sequence: number
  previous_reference: string | null
  status: NoticeStatus
  status_label: string
  application_id: string
  application_reference: string
  issued_at: string | null
  response_due_at: string | null
  due_note: string
  demo_notice: string | null
  template_label: string | null
  summary: string
  items: InstItem[]
  response: { status: 'DRAFT' | 'SUBMITTED'; explanation: string; row_version: number; submitted_at: string | null; submitted_by: string | null } | null
  can_respond: boolean
  not_an_approval: string
}

export interface InstNoticeSummary {
  id: string
  reference: string
  sequence: number
  status: NoticeStatus
  status_label: string
  application_id: string
  application_reference: string
  issued_at: string | null
  response_due_at: string | null
  items: number
  items_answered: number
}

export interface InstScrutiny {
  application_id: string
  state_label: string
  explanation: string
  events: { at: string; label: string }[]
  notices: InstNoticeSummary[]
  not_an_approval: string
}

export interface AppNotification {
  id: string
  event_type: string
  title: string
  body: string
  link: string | null
  created_at: string
  read: boolean
  delivery_state: string
  external_delivery: string
  application_reference: string | null
}

export interface NotificationPage {
  items: AppNotification[]
  total: number
  unread: number
  page: number
  page_size: number
}
