import type { ChecklistItem, ExtractionSpec, RouteStage } from '../applications/types'
import type { DeadlinePolicy, VersionContentInput, VersionDetail } from './types'

/** Editable form state. Everything is a string/boolean so inputs stay controlled; conversion happens at the edges. */
export interface ExtractionDraft {
  uid: string
  field_key: string
  label: string
  kind: ExtractionSpec['kind']
  anchorsText: string // one label per line
  required: boolean
  compare_group: string
  compare_to_application: boolean
}

export interface ItemDraft {
  uid: string
  extraction: ExtractionDraft[]
  item_key: string
  label: string
  explanation: string
  required: boolean
  item_type: ChecklistItem['item_type']
  field_kind: NonNullable<ChecklistItem['field_kind']>
  optionsText: string
  min_length: string
  max_length: string
  min: string
  max: string
  when_item: string
  when_value: string
  source_id: string
  clause_ref: string
}

export interface StageDraft {
  uid: string
  stage_key: string
  label: string
  description: string
  is_conditional: boolean
  condition_description: string
  next: string[]
  source_id: string
  clause_ref: string
}

export interface PolicyDraft {
  uid: string
  policy_key: string
  label: string
  deadline_type: DeadlinePolicy['deadline_type']
  trigger_event: string
  duration_value: string
  duration_unit: string
  calendar_convention: string
  pause_resume_note: string
  source_id: string
  clause_ref: string
}

export interface EditorState {
  academic_years: string[]
  effective_from: string
  effective_to: string
  change_summary: string
  source_ids: string[]
  items: ItemDraft[]
  stages: StageDraft[]
  policies: PolicyDraft[]
}

let n = 0
export const uid = () => `u${++n}`

const s = (v: unknown) => (v === null || v === undefined ? '' : String(v))

export const blankItem = (): ItemDraft => ({
  uid: uid(), extraction: [], item_key: '', label: '', explanation: '', required: true, item_type: 'form_field', field_kind: 'text',
  optionsText: '', min_length: '', max_length: '', min: '', max: '', when_item: '', when_value: '', source_id: '', clause_ref: '',
})
export const blankExtraction = (): ExtractionDraft => ({ uid: uid(), field_key: '', label: '', kind: 'text', anchorsText: '', required: true, compare_group: '', compare_to_application: false })
export const blankStage = (): StageDraft => ({ uid: uid(), stage_key: '', label: '', description: '', is_conditional: false, condition_description: '', next: [], source_id: '', clause_ref: '' })
export const blankPolicy = (): PolicyDraft => ({
  uid: uid(), policy_key: '', label: '', deadline_type: 'INTERNAL_SLA', trigger_event: '', duration_value: '', duration_unit: '', calendar_convention: '', pause_resume_note: '', source_id: '', clause_ref: '',
})

export function fromVersion(v: VersionDetail): EditorState {
  return {
    academic_years: [...v.academic_years],
    effective_from: s(v.effective_from),
    effective_to: s(v.effective_to),
    change_summary: v.change_summary,
    source_ids: v.sources.map((x) => x.id),
    items: v.checklist.map((i) => ({
      extraction: (i.extraction ?? []).map((x) => ({ uid: uid(), field_key: x.field_key, label: x.label, kind: x.kind, anchorsText: x.anchors.join('\n'), required: x.required, compare_group: x.compare_group ?? '', compare_to_application: x.compare_to_application === 'institution_name' })),
      uid: uid(), item_key: i.item_key, label: i.label, explanation: i.explanation, required: i.required, item_type: i.item_type,
      field_kind: i.field_kind ?? 'text', optionsText: (i.options ?? []).join('\n'),
      min_length: s(i.validation?.min_length), max_length: s(i.validation?.max_length), min: s(i.validation?.min), max: s(i.validation?.max),
      when_item: i.applies_when?.item_key ?? '', when_value: s(i.applies_when?.equals ?? i.applies_when?.in?.[0]),
      source_id: s(i.source_id), clause_ref: s(i.clause_ref),
    })),
    stages: v.route.map((r) => ({
      uid: uid(), stage_key: r.stage_key, label: r.label, description: r.description, is_conditional: r.is_conditional,
      condition_description: s(r.condition_description), next: [...r.next_stage_keys], source_id: s(r.source_id), clause_ref: s(r.clause_ref),
    })),
    policies: v.deadlines.map((p) => ({
      uid: uid(), policy_key: p.policy_key, label: p.label, deadline_type: p.deadline_type, trigger_event: p.trigger_event,
      duration_value: s(p.duration_value), duration_unit: s(p.duration_unit), calendar_convention: s(p.calendar_convention),
      pause_resume_note: s(p.pause_resume_note), source_id: s(p.source_id), clause_ref: s(p.clause_ref),
    })),
  }
}

const orNull = (x: string) => (x.trim() === '' ? null : x.trim())
const num = (x: string) => (x.trim() === '' ? undefined : Number(x))

export function toPayload(e: EditorState, rowVersion: number): VersionContentInput {
  const items = e.items.map((i, idx): ChecklistItem => {
    const isField = i.item_type === 'form_field'
    const validation: Record<string, number> = {}
    if (isField) {
      for (const [k, v] of [['min_length', i.min_length], ['max_length', i.max_length], ['min', i.min], ['max', i.max]] as const) {
        const x = num(v)
        if (x !== undefined && !Number.isNaN(x)) validation[k] = x
      }
    }
    const whenField = e.items.find((o) => o.item_key === i.when_item)
    const whenValue: unknown = whenField?.field_kind === 'number' && i.when_value !== '' ? Number(i.when_value) : i.when_value
    return {
      item_key: i.item_key.trim(), label: i.label.trim(), explanation: i.explanation.trim(), required: i.required, item_type: i.item_type,
      field_kind: isField ? i.field_kind : null,
      options: isField && i.field_kind === 'select' ? i.optionsText.split('\n').map((o) => o.trim()).filter(Boolean) : null,
      validation: isField && Object.keys(validation).length ? validation : null,
      applies_when: i.when_item ? { item_key: i.when_item, equals: whenValue } : null,
      source_id: orNull(i.source_id), clause_ref: orNull(i.clause_ref), display_order: idx,
      extraction: i.item_type === 'document_upload' && i.extraction.length
        ? i.extraction.map((x) => ({
            field_key: x.field_key.trim(), label: x.label.trim(), kind: x.kind, anchors: x.anchorsText.split('\n').map((a) => a.trim()).filter(Boolean),
            required: x.required, compare_group: orNull(x.compare_group), compare_to_application: x.compare_to_application ? ('institution_name' as const) : null,
          }))
        : null,
    }
  })
  const route = e.stages.map((st, idx): RouteStage => ({
    stage_key: st.stage_key.trim(), label: st.label.trim(), description: st.description.trim(), display_order: idx, is_conditional: st.is_conditional,
    condition_description: st.is_conditional ? orNull(st.condition_description) : null, next_stage_keys: st.next,
    source_id: orNull(st.source_id), clause_ref: orNull(st.clause_ref),
  }))
  const deadlines = e.policies.map((p): DeadlinePolicy => ({
    policy_key: p.policy_key.trim(), label: p.label.trim(), deadline_type: p.deadline_type, trigger_event: p.trigger_event.trim(),
    duration_value: p.duration_value.trim() === '' ? null : Number(p.duration_value), duration_unit: (orNull(p.duration_unit) as DeadlinePolicy['duration_unit']),
    calendar_convention: (orNull(p.calendar_convention) as DeadlinePolicy['calendar_convention']), pause_resume_note: orNull(p.pause_resume_note),
    escalation: null, source_id: orNull(p.source_id), clause_ref: orNull(p.clause_ref),
  }))
  return {
    academic_years: e.academic_years, effective_from: orNull(e.effective_from), effective_to: orNull(e.effective_to),
    change_summary: e.change_summary.trim(), source_ids: e.source_ids, checklist: items, route, deadlines, row_version: rowVersion,
  }
}

export const isAcademicYear = (y: string) => {
  const m = /^(\d{4})-(\d{2})$/.exec(y)
  return !!m && (Number(m[1]) + 1) % 100 === Number(m[2])
}
