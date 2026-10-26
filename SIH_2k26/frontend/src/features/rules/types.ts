import type { Category, ChecklistItem, RouteStage, Source, Verification } from '../applications/types'

export type VersionStatus = 'draft' | 'in_review' | 'published' | 'retired'

export interface RuleSetRef {
  id: string
  key: string
  title: string
  classification: 'OFFICIAL' | 'DEMO'
}

export interface VersionSummary {
  id: string
  rule_set: RuleSetRef
  category: Category
  version_number: number
  status: VersionStatus
  academic_years: string[]
  effective_from: string | null
  effective_to: string | null
  change_summary: string
  is_demo: boolean
  verification: Verification
  created_at: string
  updated_at: string
  published_at: string | null
}

export interface RuleSetOut {
  id: string
  key: string
  title: string
  classification: 'OFFICIAL' | 'DEMO'
  category: Category
  versions: VersionSummary[]
}

export interface DeadlinePolicy {
  policy_key: string
  label: string
  deadline_type: 'STATUTORY' | 'INTERNAL_SLA'
  trigger_event: string
  duration_value: number | null
  duration_unit: 'days' | 'hours' | null
  calendar_convention: 'CALENDAR' | 'WORKING' | null
  pause_resume_note: string | null
  escalation: Record<string, unknown> | null
  source_id: string | null
  clause_ref: string | null
}

export interface Issue {
  code: string
  message: string
  path: string
}

export interface VersionEvent {
  at: string
  actor: string | null
  action: string
  from_status: string | null
  to_status: string | null
  note: string | null
}

export interface VersionDetail extends VersionSummary {
  row_version: number
  created_by: string | null
  submitted_by: string | null
  submitted_at: string | null
  review_note: string | null
  published_by: string | null
  retired_by: string | null
  retired_at: string | null
  retirement_reason: string | null
  supersedes_version_id: string | null
  content_hash: string | null
  sources: Source[]
  checklist: ChecklistItem[]
  route: RouteStage[]
  deadlines: DeadlinePolicy[]
  events: VersionEvent[]
  publication_issues: Issue[]
  demo_banner: string | null
}

export interface Gap {
  id: string
  category: Category
  academic_year: string
  reason: 'no_approved_version' | 'ambiguous' | 'incomplete'
  status: 'open' | 'resolved'
  report_count: number
  first_reported_at: string
  last_reported_at: string
  resolution_note: string | null
}

export interface SourceInput {
  title: string
  issuing_authority: string
  url: string | null
  document_ref: string | null
  clause_ref: string | null
  page_ref: string | null
  publication_date: string | null
  effective_date: string | null
  retrieved_date: string | null
  verification_status: Source['verification_status']
  applicability_notes: string | null
}

export interface VersionContentInput {
  academic_years: string[]
  effective_from: string | null
  effective_to: string | null
  change_summary: string
  source_ids: string[]
  checklist: ChecklistItem[]
  route: RouteStage[]
  deadlines: DeadlinePolicy[]
  row_version: number
}
