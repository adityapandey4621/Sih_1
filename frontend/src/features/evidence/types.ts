/** Mirrors backend/app/schemas_evidence.py. Every label shown to people comes from the server (`state.label`, `plain_text`). */

export type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'error'

export interface DocumentState {
  code: string
  label: string
  explanation: string
  tone: Tone
  terminal: boolean // false while processing is still expected: the screen keeps refreshing
  can_retry: boolean
  technical_status: string | null
  scan_status: string | null
  scan_note: string | null
  extraction_status: string | null
  page_count: number | null
  text_method: string | null
  review_recommended: boolean
  error_code: string | null
}

export interface Version {
  id: string
  item_key: string
  version_number: number
  original_filename: string
  media_type: string
  size_bytes: number
  sha256: string
  uploaded_at: string
  uploaded_by: string | null
  is_current: boolean
  previous_version_id: string | null
  state: DocumentState
}

export interface PageInfo {
  page_number: number
  method: string
  status: string
  confidence: number | null
  char_count: number
  note: string | null
  text: string | null
}

export interface ExtractedFieldOut {
  field_key: string
  label: string
  kind: string
  status: 'EXTRACTED' | 'UNCERTAIN' | 'NOT_FOUND'
  value: string | null
  normalised_value: string | null
  page_number: number | null
  method: string | null
  confidence: number | null
  evidence: string | null
  note: string | null
  document_id: string
  version_number: number
}

export interface Finding {
  id: string
  issue_type: string
  label: string
  status: string
  priority: 'info' | 'low' | 'medium' | 'high' | null
  check_key: string | null
  item_key: string | null
  document_id: string | null
  version_number: number | null
  pages: number[]
  values: Record<string, unknown>[]
  explanation: string
  plain_text: string
  rule_ref: string | null
  review_recommended: boolean
  created_at: string
  disclaimer: string
}

export interface ItemEvidence {
  item_key: string
  label: string
  explanation: string
  required: boolean
  clause_ref: string | null
  configured_fields: string[]
  slot_state: DocumentState | null // null = no file submitted (different from an unreadable file)
  current: Version | null
  versions: Version[]
  fields: ExtractedFieldOut[]
  pages: PageInfo[]
  findings: Finding[]
}

export interface ApplicationEvidence {
  application_id: string
  reference: string
  status: 'DRAFT' | 'SUBMITTED'
  academic_year: string
  category_name: string
  institution_name: string | null
  viewer: 'owner' | 'reviewer'
  items: ItemEvidence[]
  cross_document_findings: Finding[]
  disclaimer: string
  ocr_available: boolean
  malware_scanner: string
}

export interface Extraction {
  document_id: string
  version_number: number
  state: DocumentState
  pages: PageInfo[]
  fields: ExtractedFieldOut[]
}

export interface HistoryEvent {
  at: string
  actor: string | null
  action: string
  detail: Record<string, unknown> | null
}

export interface ReviewListItem {
  id: string
  reference: string
  institution_name: string
  category_name: string
  academic_year: string
  submitted_at: string | null
  documents: number
  still_processing: number
  review_recommended: number
  open_findings: number
}
