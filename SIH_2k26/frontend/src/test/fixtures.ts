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
