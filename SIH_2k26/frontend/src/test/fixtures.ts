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

// ---- Phase 4: scrutiny, notices, responses ----
import type { CaseDetail, InstItem, InstNotice, QueueItem, ReviewFinding, StaffNotice, StaffNoticeItem } from '../features/scrutiny/types'

export const queueItem = (over: Partial<QueueItem> = {}): QueueItem => ({
  id: 'case-1', application_id: 'app-1', reference: 'APP-AAAA-BBBB', institution_name: 'Test Institute', category_name: 'Category A', academic_year: '2026-27',
  state: 'UNDER_SCRUTINY', state_label: 'Under scrutiny', assignees: ['Test Reviewer'], submitted_at: '2026-10-26T10:00:00Z', updated_at: '2026-10-26T11:00:00Z',
  outstanding_actions: ['Record findings or complete the round'], due_at: null, due_basis: null,
  rule_version: { rule_set: 'set-a', number: 1, classification: 'DEMO', demo_banner: 'DEMO ONLY — NOT VERIFIED AS CURRENT OFFICIAL RULES' },
  processing: { documents: 1, still_processing: 0, failed: 0, review_recommended: 0 }, round_number: 1, round_kind: 'INITIAL', exception: null, handoff_status: null, handoff_stage: null, row_version: 3, ...over,
})

export const reviewFinding = (over: Partial<ReviewFinding> = {}): ReviewFinding => ({
  id: 'sf-1', sequence: 1, round_number: 1, item_key: 'doc-main', item_label: 'Main document', document_id: 'd1', document_version: 1, document_current_version: 1, document_superseded: false,
  page_number: 1, issue_type: 'incomplete_information', issue_label: 'Incomplete information', description: 'INTERNAL: the letter does not show a validity period.',
  institution_text: 'Please show the validity period.', requested_correction: 'Upload a letter that states it.', source_ref: null, source_ref_status: 'NEEDS_VERIFICATION', preliminary_finding_id: null,
  visibility: 'ELIGIBLE_FOR_NOTICE', state: 'OPEN', state_reason: null, created_by: 'Test Reviewer', created_at: '2026-10-26T11:00:00Z', updated_at: '2026-10-26T11:00:00Z', row_version: 1,
  notice_reference: null, history: [{ at: '2026-10-26T11:00:00Z', actor: 'Test Reviewer', action: 'recorded', from_state: null, to_state: 'OPEN', reason: null, round_number: 1 }], ...over,
})

export const staffNoticeItem = (over: Partial<StaffNoticeItem> = {}): StaffNoticeItem => ({
  id: 'ni-1', position: 1, finding_id: 'sf-1', finding_state: 'OPEN', title: 'Main document', description: 'Please show the validity period.', requested_correction: 'Upload a letter that states it.',
  item_key: 'doc-main', document_id: 'd1', document_version: 1, document_current_version: 1, page_number: 1, source_ref: null, source_ref_status: 'NEEDS_VERIFICATION',
  response_state: 'NOT_RESPONDED', response_explanation: '', response_documents: [], ...over,
})

export const staffNotice = (over: Partial<StaffNotice> = {}): StaffNotice => ({
  id: 'n-1', reference: 'DN-AAAA-BBBB', sequence: 1, previous_reference: null, status: 'DRAFT', round_number: 1, summary: 'We need the validity period.', items: [staffNoticeItem()],
  prepared_by: 'Test Reviewer', issued_by: null, issuer_role: null, issued_at: null, issue_reason: null, returned_reason: null, withdrawn_reason: null, response_due_at: null,
  policy_resolution: null, template: null, content_hash: null, authority: null, row_version: 1, response: null, ...over,
})

export const caseDetail = (over: Partial<CaseDetail> = {}): CaseDetail => ({
  queue: queueItem(), scope: ['reviewer'],
  selection: { mode: 'DEMO_MANUAL', available: true, label: 'Demonstration assignment', notice: 'Demonstration assignment: a coordinator chose a fictional reviewer. This is NOT the official AICTE committee selection process.' },
  assignments: [], rounds: [{ id: 'r1', number: 1, kind: 'INITIAL', status: 'OPEN', opened_at: '2026-10-26T10:00:00Z', started_at: '2026-10-26T10:30:00Z', closed_at: null, triggered_by_notice: null, outcome: null, outcome_reason: null, review_notes: null, review_notes_at: null, row_version: 1 }],
  findings: [], notices: [], source_options: [{ key: 'item:doc-main', label: 'Main document', clause_ref: 'cl. 9', verification_status: 'DEMO_ONLY', origin: 'checklist' }],
  issue_types: { incomplete_information: 'Incomplete information', missing_document: 'Missing document', other: 'Other' }, events: [],
  allowed_actions: ['record_finding', 'edit_finding', 'conclude_finding', 'save_notes', 'complete', 'prepare_notice'], completion: null, handoff: null,
  checklist: [{ item_key: 'doc-main', label: 'Main document', explanation: 'Required file.', required: true, item_type: 'document_upload', clause_ref: 'cl. 9', answer: null, document_id: 'd1', document_version: 1, document_filename: 'main.pdf' }],
  not_an_approval: 'Completing scrutiny is not an approval. Approval or rejection is decided only by the authorised decision process, which is not part of this release.', ...over,
})

export const instItem = (over: Partial<InstItem> = {}): InstItem => ({
  id: 'ni-1', position: 1, title: 'Main document', description: 'Please show the validity period.', requested_correction: 'Upload a letter that states it.', item_key: 'doc-main', item_label: 'Main document',
  accepts_files: true, document_version: 1, document_current_version: 1, page_number: 1, source_label: null, source_note: 'No source reference has been established for this item.',
  response_state: 'NOT_RESPONDED', response_explanation: '', response_documents: [], ...over,
})

export const instNotice = (over: Partial<InstNotice> = {}): InstNotice => ({
  id: 'n-1', reference: 'DN-AAAA-BBBB', sequence: 1, previous_reference: null, status: 'ISSUED', status_label: 'Issued — response awaited', application_id: 'app-1', application_reference: 'APP-AAAA-BBBB',
  issued_at: '2026-10-27T10:00:00Z', response_due_at: null, due_note: 'No response deadline has been established for this notice. (DEMO ONLY)', demo_notice: 'DEMO ONLY — NOT VERIFIED AS CURRENT OFFICIAL RULES',
  template_label: 'DEMO ONLY — demonstration template (no official wording, signature or seal)', summary: 'We need the validity period.', items: [instItem()], response: null, can_respond: true,
  not_an_approval: 'A deficiency notice is a request for information. It is not a rejection, and your response is not accepted until re-scrutiny has examined it.', ...over,
})