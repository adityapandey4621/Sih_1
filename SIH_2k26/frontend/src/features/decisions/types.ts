/** Shapes returned by the decision endpoints (backend/app/decision_presenters.py). Wording such as labels and notices comes from the server. */

export type DecisionState = 'AWAITING_OBSERVATIONS' | 'READY_FOR_DECISION' | 'DECIDED' | 'ISSUED' | 'CLOSED'
export type Outcome = 'LOA' | 'LOD' | 'LOR'

export interface AppSummary {
  id: string
  reference: string
  institution_name: string
  category_name: string
  academic_year: string
  status: string
}

export interface DecisionQueueRow {
  id: string
  application: AppSummary
  state: DecisionState
  state_label: string
  row_version: number
  opened_at: string
  outcome: Outcome | null
  outcome_label: string | null
  blocked: boolean
  appeal_status: string | null
  appeal_label: string | null
  demo: boolean
}

export interface DecisionQueue {
  total: number
  page: number
  page_size: number
  counts: Record<string, number>
  items: DecisionQueueRow[]
  can: { observe: boolean; record: boolean; council: boolean }
}

export interface SyncView {
  status: 'NOT_CONFIGURED' | 'SYNC_PENDING' | 'SYNCED'
  message: string
  attempts: number
  last_error: string | null
  last_attempt_at: string | null
  confirmed_at: string | null
  external_ref: string | null
}

export interface EcDecisionOut {
  id: string
  version_number: number
  kind: 'ORIGINAL' | 'CORRECTION'
  outcome: Outcome
  outcome_label: string
  reasons: string
  speaking_order: string | null
  meeting_reference: string | null
  decided_by: string
  decided_at: string
  correction_reason: string | null
  supersedes_id: string | null
  content_hash: string
  supporting_refs?: unknown
  rule_version?: unknown
}

export interface CouncilOut {
  id: string
  kind: 'RATIFICATION' | 'APPEAL_DECISION'
  outcome: string
  outcome_label: string
  reasons: string
  speaking_order: string | null
  reference: string | null
  recorded_by: string
  recorded_at: string
  supersedes_id: string | null
  correction_reason: string | null
  content_hash: string
}

export interface IssuanceOut {
  id: string
  decision_id: string
  status: 'ISSUED' | 'SUPERSEDED'
  document_reference: string | null
  issued_at: string
  superseded_at: string | null
  file: { filename: string; size_bytes: number; sha256: string; download: string } | null
}

export interface SupportingRecords {
  scrutiny: { case_id: string | null; state: string | null; completion: { outcome?: string; summary?: string } | null; findings: { sequence: number; state: string; issue_type: string; reason: string | null }[] }
  evc: { case_id: string; state: string; record: { version: number; content_hash: string; option: string | null } | null } | null
  shc: { case_id: string; state: string; record: { version: number; content_hash: string; option: string | null } | null } | null
  bureau: { observation_ids: string[]; latest: { id: string; sequence: number } } | null
  documents: { item_key: string; document_id: string; version_number: number; sha256: string }[]
}

export interface AppealEligibility {
  state: string
  message: string
  deadline_at: string | null
  trigger_at: string | null
  outcome: string | null
  clause_ref: string | null
  window?: { policy_key?: string; state?: string; message?: string; note?: string | null; duration_value?: number; duration_unit?: string; calendar_convention?: string | null } | null
}

export interface DecisionPolicyView {
  id: string
  key: string
  version: number
  classification: 'DEMO' | 'OFFICIAL'
  title: string
  banner: string | null
  sources: { title: string; clause_ref: string; verification_status: string }[]
  decision: { outcomes: { key: string; label: string; requires_speaking_order?: boolean; clause_ref?: string }[]; bureau_observation_required?: boolean } | null
  ratification: { required?: boolean; outcomes?: { key: string; label: string }[]; clause_ref?: string; issue_requires_ratification?: boolean } | null
  appeal: { permitted?: boolean; appealable_outcomes?: string[]; council_outcomes?: { key: string; label: string; requires_speaking_order?: boolean }[]; clause_ref?: string } | null
}

export interface StaffAppeal {
  id: string
  status: 'UNDER_SAC_REVIEW' | 'AWAITING_COUNCIL_DECISION' | 'COUNCIL_DECIDED'
  status_label: string
  submitted_at: string
  grounds: string
  eligibility: AppealEligibility
  sac_case_id: string | null
  sac_state: string | null
  sac_recommendation: { version: number; option: string | null; content: { sections?: Record<string, string> }; content_hash: string; submitted_at: string } | null
  documents: { id: string; kind: 'FILE' | 'LINK'; description: string; filename: string | null; url: string | null; uploaded_at: string; download: string | null }[]
  council_decisions: CouncilOut[]
  live_council_decision: CouncilOut | null
  sync: SyncView | null
}

export interface DecisionCaseDetail {
  id: string
  application: AppSummary
  state: DecisionState
  state_label: string
  row_version: number
  opened_at: string
  first_issued_at: string | null
  closed_at: string | null
  closure: Record<string, unknown> | null
  policy: DecisionPolicyView | null
  blockers: { code: string; message: string }[]
  route: { stages: string[]; basis: { note?: string } }
  supporting: SupportingRecords
  considerable: string[]
  observations: { id: string; sequence: number; text: string; by: string; at: string }[]
  decisions: EcDecisionOut[]
  current_decision: EcDecisionOut | null
  issuances: IssuanceOut[]
  live_issuance: IssuanceOut | null
  issuance_sync: SyncView | null
  ratifications: CouncilOut[]
  live_ratification: CouncilOut | null
  appeal_eligibility: AppealEligibility
  appeal: StaffAppeal | null
  closure_blockers: { code: string; message: string }[]
  events: { at: string; actor: string; action: string; from_state: string | null; to_state: string | null; reason: string | null }[]
  allowed_actions: string[]
  not_a_decision: string
  post_decision: boolean
}

export interface InstitutionDecision {
  official_status: { state: string; label: string; outcome?: Outcome }
  decision: {
    outcome: Outcome
    outcome_label: string
    reasons: string
    speaking_order: string | null
    issued_at: string
    first_issued_at: string | null
    document_reference: string | null
    file: { filename: string; size_bytes: number; download: string } | null
    version_number: number
    corrected: boolean
    correction_reason: string | null
    earlier_issues: { issued_at: string; superseded_at: string | null }[]
    ratification: { required: boolean; recorded: boolean; outcome: string | null; recorded_at: string | null; reasons: string | null; clause_ref?: string | null }
    sync: SyncView | null
  } | null
  correction_pending?: string
  closed: boolean
  appeal: {
    eligibility: Pick<AppealEligibility, 'state' | 'message' | 'deadline_at' | 'trigger_at' | 'outcome' | 'clause_ref'>
    window?: AppealEligibility['window']
    submitted: {
      id: string
      status: StaffAppeal['status']
      status_label: string
      submitted_at: string
      grounds: string
      documents: { id: string; kind: 'FILE' | 'LINK'; description: string; filename: string | null; size_bytes: number | null; url: string | null; uploaded_at: string; download: string | null }[]
      can_add_documents: boolean
      council: { outcome: string; outcome_label: string; reasons: string; speaking_order: string | null; reference: string | null; recorded_at: string; sync: SyncView | null } | null
      note: string
    } | null
  } | null
  not_a_decision: string
}
