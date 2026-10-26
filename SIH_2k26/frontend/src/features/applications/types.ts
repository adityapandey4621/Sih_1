/** Mirrors backend/app/schemas_applications.py and schemas_catalogue.py. No regulatory content lives here. */

export interface Category {
  id: string
  key: string
  name: string
  description: string
  applicability_note: string | null
  source_id: string | null
  is_active: boolean
}

export interface Source {
  id: string
  title: string
  issuing_authority: string
  url: string | null
  document_ref: string | null
  clause_ref: string | null
  page_ref: string | null
  publication_date: string | null
  effective_date: string | null
  retrieved_date: string | null
  verification_status: SourceVerification
  applicability_notes: string | null
  locked: boolean
}

export type SourceVerification = 'VERIFIED_OFFICIAL' | 'SECONDARY_UNVERIFIED' | 'PROPOSED_POLICY' | 'DEMO_ONLY'

export interface ExtractionSpec {
  field_key: string
  label: string
  kind: 'text' | 'date' | 'identifier'
  anchors: string[]
  required: boolean
  compare_group: string | null
  compare_to_application: 'institution_name' | null
}

export interface ChecklistItem {
  item_key: string
  label: string
  explanation: string
  required: boolean
  item_type: 'document_upload' | 'form_field' | 'declaration'
  field_kind: 'text' | 'long_text' | 'number' | 'select' | 'date' | null
  options: string[] | null
  validation: Record<string, number> | null
  applies_when: { item_key: string; equals?: unknown; in?: unknown[] } | null
  source_id: string | null
  clause_ref: string | null
  display_order: number
  extraction?: ExtractionSpec[] | null
}

export interface RouteStage {
  stage_key: string
  label: string
  description: string
  display_order: number
  is_conditional: boolean
  condition_description: string | null
  next_stage_keys: string[]
  source_id: string | null
  clause_ref: string | null
}

export type Verification = 'verified' | 'unverified' | 'demo'

export interface CatalogueOptions {
  categories: Category[]
  academic_years: string[]
}

export interface ResolvedChecklist {
  rule_set_key: string
  rule_set_title: string
  version_number: number
  is_demo: boolean
  demo_banner: string | null
  verification: Verification
  checklist: ChecklistItem[]
  route: RouteStage[]
  sources: Source[]
}

export type ItemStatus = 'supplied' | 'missing' | 'invalid' | 'not_applicable'

export interface ChecklistState {
  item_key: string
  label: string
  explanation: string
  required: boolean
  item_type: ChecklistItem['item_type']
  field_kind: ChecklistItem['field_kind']
  options: string[] | null
  validation: Record<string, number> | null
  applies_when: ChecklistItem['applies_when']
  clause_ref: string | null
  source_id: string | null
  applicable: boolean
  status: ItemStatus
  error: string | null
  document_id: string | null
}

export interface ChecklistSummary {
  required_total: number
  required_supplied: number
  optional_total: number
  optional_supplied: number
}

export interface ApplicationSummary {
  id: string
  reference: string
  category: Category
  academic_year: string
  status: 'DRAFT' | 'SUBMITTED'
  created_at: string
  updated_at: string
  submitted_at: string | null
  row_version: number
  is_demo: boolean
  checklist: ChecklistSummary
  outstanding_action: string | null
}

import type { DocumentState } from '../evidence/types'

export interface DocumentRecord {
  id: string
  item_key: string
  original_filename: string
  media_type: string
  size_bytes: number
  sha256: string
  uploaded_at: string
  uploaded_by: string | null
  removed: boolean
  version_number: number
  is_current: boolean
  verification_status: string
  state: DocumentState | null
}

export interface ApplicationDetail extends ApplicationSummary {
  institution: { id: string; name: string; external_identifier: string | null }
  created_by: string | null
  submitted_by: string | null
  form_data: Record<string, unknown>
  rule_version: {
    id: string
    rule_set_key: string
    rule_set_title: string
    number: number
    classification: 'OFFICIAL' | 'DEMO'
    is_demo: boolean
    demo_banner: string | null
    content_hash: string | null
    verification: Verification
    sources: Omit<Source, 'locked'>[]
    route: RouteStage[]
  }
  checklist_items: ChecklistState[]
  documents: DocumentRecord[]
  history: { at: string; actor: string | null; action: string; detail: Record<string, unknown> | null }[]
  editable: boolean
  current_rule_version_differs: boolean
  orphaned_data_keys: string[]
}

export interface ApplicationPage {
  items: ApplicationSummary[]
  total: number
  page: number
  page_size: number
}

export interface ListFilters {
  status?: string
  category_id?: string
  academic_year?: string
  q?: string
  page?: number
  page_size?: number
}
