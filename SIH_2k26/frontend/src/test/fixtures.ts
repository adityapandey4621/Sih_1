import type { ApplicationDetail, ApplicationSummary, Category, ChecklistState, ResolvedChecklist } from '../features/applications/types'
import type { VersionDetail } from '../features/rules/types'

export const category: Category = { id: 'cat-1', key: 'cat-a', name: 'Category A', description: '', applicability_note: null, source_id: null, is_active: true }

const item = (over: Partial<ChecklistState>): ChecklistState => ({
  item_key: 'k', label: 'Item', explanation: 'Explains the item.', required: true, item_type: 'form_field', field_kind: 'text', options: null,
  validation: null, applies_when: null, clause_ref: null, source_id: null, applicable: true, status: 'missing', error: null, document_id: null, ...over,
})

export const demoItems: ChecklistState[] = [
  item({ item_key: 'contact', label: 'Contact person' }),
  item({ item_key: 'kind', label: 'Kind of change', field_kind: 'select', options: ['A', 'B'] }),
  item({ item_key: 'b-detail', label: 'Detail for B', applies_when: { item_key: 'kind', equals: 'B' }, applicable: false, status: 'not_applicable' }),
  item({ item_key: 'doc-main', label: 'Main document', item_type: 'document_upload', field_kind: null }),
  item({ item_key: 'decl', label: 'Declaration', item_type: 'declaration', field_kind: null }),
]

export const summary = (over: Partial<ApplicationSummary> = {}): ApplicationSummary => ({
  id: 'app-1', reference: 'APP-AAAA-BBBB', category, academic_year: '2026-27', status: 'DRAFT', created_at: '2026-10-26T10:00:00Z', updated_at: '2026-10-26T10:00:00Z',
  submitted_at: null, row_version: 1, is_demo: true, checklist: { required_total: 4, required_supplied: 0, optional_total: 0, optional_supplied: 0 }, outstanding_action: 'Supply 4 required items', ...over,
})

export const detail = (over: Partial<ApplicationDetail> = {}): ApplicationDetail => ({
  ...summary(),
  institution: { id: 'inst-1', name: 'Test Institute', external_identifier: null },
  created_by: 'Test Person', submitted_by: null, form_data: {},
  rule_version: {
    id: 'v-1', rule_set_key: 'set-a', rule_set_title: 'Set A', number: 1, classification: 'DEMO', is_demo: true,
    demo_banner: 'DEMO ONLY — NOT VERIFIED AS CURRENT OFFICIAL RULES', content_hash: 'ab'.repeat(32), verification: 'demo', sources: [], route: [],
  },
  checklist_items: demoItems, documents: [], history: [{ at: '2026-10-26T10:00:00Z', actor: 'Test Person', action: 'created', detail: null }],
  editable: true, current_rule_version_differs: false, orphaned_data_keys: [], ...over,
})

export const resolved: ResolvedChecklist = {
  rule_set_key: 'set-a', rule_set_title: 'Set A', version_number: 1, is_demo: true, demo_banner: 'DEMO ONLY — NOT VERIFIED AS CURRENT OFFICIAL RULES', verification: 'demo',
  checklist: demoItems.map((i, n) => ({ ...i, display_order: n })), route: [], sources: [],
}

export const version = (over: Partial<VersionDetail> = {}): VersionDetail => ({
  id: 'rv-1', rule_set: { id: 'rs-1', key: 'set-a', title: 'Set A', classification: 'DEMO' }, category, version_number: 1, status: 'draft', academic_years: ['2026-27'],
  effective_from: null, effective_to: null, change_summary: 'Initial', is_demo: true, verification: 'demo', created_at: '2026-10-26T10:00:00Z', updated_at: '2026-10-26T10:00:00Z',
  published_at: null, row_version: 1, created_by: 'Author', submitted_by: null, submitted_at: null, review_note: null, published_by: null, retired_by: null, retired_at: null,
  retirement_reason: null, supersedes_version_id: null, content_hash: null, sources: [], checklist: [], route: [], deadlines: [], events: [], publication_issues: [],
  demo_banner: 'DEMO ONLY — NOT VERIFIED AS CURRENT OFFICIAL RULES', ...over,
})

import type { DocumentState } from '../features/evidence/types'

export const docState = (over: Partial<DocumentState> = {}): DocumentState => ({
  code: 'extraction_complete', label: 'Extraction complete', explanation: 'Text and values were read. These are technical checks on the file only. They are not verification, and do not mean the document has been accepted.',
  tone: 'success', terminal: true, can_retry: false, technical_status: 'PASSED', scan_status: 'SCANNER_UNAVAILABLE', scan_note: 'Malware scan: NOT performed (no scanner is configured). This file has not been scanned.',
  extraction_status: 'COMPLETE', page_count: 1, text_method: 'PDF_TEXT', review_recommended: false, error_code: null, ...over,
})

import type { ApplicationEvidence, ExtractedFieldOut, Finding, ItemEvidence, Version } from '../features/evidence/types'

export const versionOf = (over: Partial<Version> = {}): Version => ({
  id: 'd1', item_key: 'doc-main', version_number: 1, original_filename: 'main.pdf', media_type: 'application/pdf', size_bytes: 2048, sha256: 'ab'.repeat(32),
  uploaded_at: '2026-10-26T10:00:00Z', uploaded_by: 'Test Person', is_current: true, previous_version_id: null, state: docState(), ...over,
})

export const fieldOf = (over: Partial<ExtractedFieldOut> = {}): ExtractedFieldOut => ({
  field_key: 'institution_name', label: 'Institution name', kind: 'text', status: 'EXTRACTED', value: 'Test Institute', normalised_value: 'test institute', page_number: 1,
  method: 'PDF_TEXT', confidence: null, evidence: 'Name of the Institution: Test Institute', note: null, document_id: 'd1', version_number: 1, ...over,
})

export const findingOf = (over: Partial<Finding> = {}): Finding => ({
  id: 'f1', issue_type: 'cross_document_inconsistency', label: 'Possible inconsistency between values', status: 'PRELIMINARY_ISSUE', priority: null, check_key: null, item_key: 'doc-main',
  document_id: 'd1', version_number: 1, pages: [1], values: [{ source: 'document', page: 1, value: 'Elsewhere College' }, { source: 'application_record', value: 'Test Institute' }],
  explanation: '“Institution name” in this document differs from the institution name on the application record.', plain_text: 'A value in this document appears different from the same value elsewhere in your application. A reviewer will look at it.',
  rule_ref: 'Main document (cl. 9)', review_recommended: true, created_at: '2026-10-26T10:05:00Z',
  disclaimer: 'This is a preliminary, automated observation awaiting scrutiny review. It is not a deficiency notice and does not affect the status of the application.', ...over,
})

export const itemEvidence = (over: Partial<ItemEvidence> = {}): ItemEvidence => {
  const v = versionOf()
  return {
    item_key: 'doc-main', label: 'Main document', explanation: 'Required file.', required: true, clause_ref: 'cl. 9', configured_fields: ['Institution name', 'Document date'],
    slot_state: v.state, current: v, versions: [v], fields: [fieldOf()], pages: [{ page_number: 1, method: 'PDF_TEXT', status: 'OK', confidence: null, char_count: 40, note: null, text: null }], findings: [], ...over,
  }
}

export const evidenceOf = (over: Partial<ApplicationEvidence> = {}): ApplicationEvidence => ({
  application_id: 'app-1', reference: 'APP-AAAA-BBBB', status: 'DRAFT', academic_year: '2026-27', category_name: 'Category A', institution_name: null, viewer: 'owner',
  items: [itemEvidence()], cross_document_findings: [], disclaimer: 'This is a preliminary, automated observation awaiting scrutiny review. It is not a deficiency notice and does not affect the status of the application.',
  ocr_available: true, malware_scanner: 'none', ...over,
})