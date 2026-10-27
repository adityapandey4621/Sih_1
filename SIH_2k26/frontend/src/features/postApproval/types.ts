/** Shapes returned by the post-approval endpoints (backend/app/compliance_presenters.py). Wording such as notes and labels comes from the server. */

export type ClockKind = 'OFFICER_STAGE' | 'INSTITUTION_RESPONSE' | 'EVC' | 'APPEAL' | 'COMPLIANCE' | 'DOCUMENT_VALIDITY'
export type ClockTiming = 'ON_TRACK' | 'DUE_SOON' | 'OVERDUE' | 'PAUSED' | 'COMPLETED' | 'CANCELLED' | 'NOT_ESTABLISHED' | 'NO_DATE' | 'UNKNOWN'

export interface AppRef {
  id: string
  reference: string
  institution_name: string
  category_name?: string
  academic_year?: string
}

export interface ClockPolicyView {
  state: string | null
  message: string | null
  source: { title: string; verification_status: string; clause_ref: string | null } | null
  demo: boolean | null
  clause_ref: string | null
  duration_value: number | null
  duration_unit: string | null
  calendar_convention: string | null
  authority: string | null
  note: string | null
  verified_date: string | null
}

export interface ClockView {
  id: string
  kind: ClockKind
  deadline_type: 'STATUTORY' | 'INTERNAL_SLA' | 'SPECIFIED' | 'VALIDITY'
  label: string
  status: string
  timing: ClockTiming
  policy_key: string
  trigger_event: string
  trigger_at: string | null
  due_at: string | null
  effective_due_at: string | null
  paused_seconds: number
  not_established_reason: string | null
  application: AppRef
  completed_at: string | null
  completion_reason: string | null
  row_version: number
  subject_type: string
  subject_id: string
  policy: ClockPolicyView
  // officials only
  responsible_role?: string | null
  alerts?: { pre_due_hours: number[]; at_due: boolean; overdue_hours: number[] } | null
  pause?: { allowed: boolean; reasons: string[] }
  escalate_to?: string[]
  segments?: { id: string; paused_at: string; resumed_at: string | null; reason: string; reference: string | null }[]
  events?: { at: string; action: string; from_status: string | null; to_status: string | null; detail: Record<string, unknown> | null }[]
  alert_work?: { id: string; alert_kind: string; offset_hours: number; run_at: string; status: string; attempts: number; max_attempts: number; outcome: string | null; last_error: string | null; delivery: string | null; recipients: number; processed_at: string | null }[]
}

export interface ClockList {
  items: ClockView[]
  total: number
  page: number
  page_size: number
  counts: { overdue: number; soon: number; ontrack: number; paused: number; not_established: number }
}

export interface SchedulerHealth {
  work: Record<string, number>
  due_and_waiting: number
  oldest_waiting_lag_seconds: number
  live_clocks: number
  failed: { id: string; clock_id: string; alert_kind: string; attempts: number; last_error: string | null; updated_at: string }[]
  note: string
}

export type ObligationStatus = 'AWAITING_EVIDENCE' | 'EVIDENCE_SUBMITTED' | 'CORRECTION_REQUESTED' | 'VERIFIED' | 'NOT_MET' | 'WITHDRAWN'

export interface ObligationSummary {
  id: string
  reference: string
  title: string
  status: ObligationStatus
  status_label: string
  application: AppRef
  due_basis: 'POLICY' | 'SPECIFIED' | 'NONE'
  due_at: string | null
  effective_due_at: string | null
  timing: ClockTiming
  verifier: string | null
  defined_at: string
  closed_at: string | null
  submissions: number
  row_version: number
}

export interface ObligationList {
  items: ObligationSummary[]
  total: number
  page: number
  page_size: number
  counts: Record<ObligationStatus, number>
}

export interface EvidenceDoc {
  id: string
  label: string
  version_number: number
  filename: string
  size_bytes: number
  sha256: string
  media_type: string
  scan_status: string
  uploaded_at: string
  download: string
}

export interface SubmissionView {
  id: string
  sequence: number
  note: string
  submitted_at: string
  submitted_by: string
  documents: EvidenceDoc[]
  review: { outcome: string; reason: string; reviewed_at: string; reviewed_by: string; internal_note?: string | null } | null
}

export interface ObligationDetail extends ObligationSummary {
  condition_text: string
  condition_ref: string | null
  required_evidence: string
  decision: { id: string; source: 'EC_DECISION' | 'COUNCIL_APPEAL_DECISION'; version_number: number | null; outcome: string; outcome_label: string; decided_at: string }
  due: { basis: string; due_at: string | null; authority: string | null; policy: Record<string, unknown> }
  clock: ClockView | null
  submissions_detail: SubmissionView[]
  events: { at: string; action: string; from_status: string | null; to_status: string | null; actor: string | null; detail: Record<string, unknown> | null }[]
  withdrawn_reason: string | null
  notes: { upload: string; penalty: string }
  can: { submit: boolean; review: boolean; withdraw: boolean; reassign: boolean }
  latest_submission_id: string | null
  defined_by?: string
  verifier_id?: string
}

export interface ValidityVersionView {
  id: string
  version_number: number
  source: 'EXTRACTED' | 'DECLARED'
  status: 'UNVERIFIED' | 'NEEDS_REVIEW' | 'VERIFIED' | 'REJECTED' | 'SUPERSEDED'
  value_date: string | null
  raw_value: string | null
  verified_date: string | null
  verified_at: string | null
  needs_review_reason: string | null
  review_reason: string | null
  created_at: string
  provenance: { document_id: string | null; document_version: number | null; page: number | null; method: string | null; confidence: number | null; evidence: string | null } | null
  file: { filename: string; size_bytes: number; sha256: string; scan_status: string | null; download: string } | null
  conflict: { with_versions: number[]; dates: string[] } | null
  clock: ClockView | null
  internal_note?: string | null
  verified_by?: string | null
  source_download?: string | null
}

export interface ValidityItemView {
  id: string
  label: string
  status: 'ACTIVE' | 'RESOLVED'
  application: AppRef
  versions: ValidityVersionView[]
  resolved_at: string | null
  resolution_reason: string | null
  current_verified_date: string | null
  expiry_timing: ClockTiming | null
  effective_due_at: string | null
  awaiting_review: number
  can: { renew: boolean; review: boolean }
  note: string
}

export interface ValidityList {
  items: ValidityItemView[]
  total: number
  page: number
  page_size: number
}

export interface InstitutionOverview {
  obligations: ObligationList
  validity: ValidityList
  clocks: ClockList
  notes: { upload: string; penalty: string }
}

export interface IssuedCase {
  application_id: string
  reference: string
  institution_name: string
  outcome: string | null
  outcome_label: string | null
  decision_version: number | null
  issued_at: string | null
  council_appeal_outcome: string | null
}

export interface ObligationInput {
  source: 'EC_DECISION' | 'COUNCIL_APPEAL_DECISION'
  title: string
  condition_text: string
  condition_ref: string | null
  required_evidence: string
  verifier_id: string
  due: { basis: 'POLICY' | 'SPECIFIED' | 'NONE'; policy_key?: string | null; due_at?: string | null; authority?: string | null }
  idempotency_key: string
}
