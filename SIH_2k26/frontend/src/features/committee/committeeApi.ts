import { request } from '../../lib/api'
import type { ApplicationEvidence } from '../evidence/types'
import type { CommitteeCaseDetail, ComplianceRound, ComplianceSummary, InstitutionStatus, QueueData, Stage } from './types'

const qs = (params: Record<string, string | number | boolean | undefined | null>) => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '' && v !== false) p.set(k, String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}
const post = <T>(path: string, body: unknown) => request<T>(path, { method: 'POST', body })
const put = <T>(path: string, body: unknown) => request<T>(path, { method: 'PUT', body })
const patch = <T>(path: string, body: unknown) => request<T>(path, { method: 'PATCH', body })

export interface QueueQuery {
  state?: string
  q?: string
  mine?: boolean
  blocked?: boolean
  visit_from?: string
  visit_to?: string
  page?: number
  page_size?: number
}

export interface ScheduleInput {
  start_local: string
  timezone: string
  mode: string
  mode_reason?: string | null
  location?: string | null
  access_details?: string | null
  idempotency_key: string
}

export const newKey = () => (typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `k-${Date.now()}-${Math.random().toString(16).slice(2)}`)

const base = (stage: Stage, id: string) => `/api/${stage.toLowerCase()}/cases/${id}`
type D = CommitteeCaseDetail

/** Staff-facing committee API. Every call is checked again by the server for role and for this exact case; the UI only mirrors `allowed_actions`. */
export const committeeApi = {
  queue: (stage: Stage, q: QueueQuery) => request<QueueData>(`/api/${stage.toLowerCase()}/cases${qs({ ...q })}`),
  detail: (stage: Stage, id: string) => request<D>(base(stage, id)),
  evidence: (stage: Stage, id: string) => request<ApplicationEvidence>(`${base(stage, id)}/evidence`),
  assignable: (stage: Stage) => request<{ demo_only: boolean; members: { id: string; name: string }[] }>(`/api/${stage.toLowerCase()}/assignable`),

  requestSelection: (stage: Stage, id: string, user_ids: string[], idempotency_key: string) => post<D>(`${base(stage, id)}/selection`, { user_ids, idempotency_key }),
  refreshSelection: (stage: Stage, id: string) => post<D>(`${base(stage, id)}/selection/refresh`, {}),
  cancelSelection: (stage: Stage, id: string, reason: string) => post<D>(`${base(stage, id)}/selection/cancel`, { reason }),
  reselect: (id: string, reason: string, row_version: number) => post<D>(`${base('EVC', id)}/reselect`, { reason, row_version }),

  propose: (stage: Stage, id: string, body: ScheduleInput) => post<D>(`${base(stage, id)}/schedule`, body),
  reschedule: (stage: Stage, id: string, body: ScheduleInput & { reason: string }) => post<D>(`${base(stage, id)}/schedule/reschedule`, body),
  confirm: (stage: Stage, id: string, scheduleId: string, row_version: number) => post<D>(`${base(stage, id)}/schedule/${scheduleId}/confirm`, { row_version }),
  cancelSchedule: (stage: Stage, id: string, scheduleId: string, reason: string) => post<D>(`${base(stage, id)}/schedule/${scheduleId}/cancel`, { reason }),
  determine: (stage: Stage, id: string, stage_key: string | null, reason: string) => post<D>(`${base(stage, id)}/route/determine`, { stage_key, reason }),

  // EVC
  startVisit: (id: string, row_version: number) => post<D>(`${base('EVC', id)}/visit/start`, { row_version }),
  observe: (id: string, itemKey: string, body: { status: string; note: string; internal_note?: string | null; shortcoming: boolean; document_refs: { document_id: string; page?: number | null }[]; row_version: number | null }) =>
    put<D>(`${base('EVC', id)}/observations/${itemKey}`, body),
  addEvidenceFile: (id: string, file: File, itemKey: string | null, description: string) => {
    const form = new FormData()
    form.append('file', file)
    if (itemKey) form.append('item_key', itemKey)
    form.append('description', description)
    return request<D>(`${base('EVC', id)}/evidence`, { method: 'POST', body: form })
  },
  addEvidenceLink: (id: string, url: string, description: string, item_key: string | null) => post<D>(`${base('EVC', id)}/evidence/link`, { url, description, item_key }),
  removeEvidence: (id: string, evidenceId: string) => request<D>(`${base('EVC', id)}/evidence/${evidenceId}`, { method: 'DELETE' }),
  saveReport: (id: string, sections: Record<string, string>, row_version: number) => put<D>(`${base('EVC', id)}/report`, { sections, row_version }),
  submitReport: (id: string, idempotency_key: string, row_version: number) => post<D>(`${base('EVC', id)}/report/submit`, { idempotency_key, row_version }),
  amendReport: (id: string, reason: string, row_version: number) => post<D>(`${base('EVC', id)}/report/amend`, { reason, row_version }),
  completeEvc: (id: string, summary: string, row_version: number) => post<D>(`${base('EVC', id)}/complete`, { summary, row_version }),

  // SHC
  addRequirement: (id: string, body: { title: string; description: string; requested_evidence: string; item_key?: string | null; origin?: { type: string; id?: string } | null }) => post<D>(`${base('SHC', id)}/requirements`, body),
  editRequirement: (id: string, reqId: string, body: { title?: string; description?: string; requested_evidence?: string; row_version: number }) => patch<D>(`${base('SHC', id)}/requirements/${reqId}`, body),
  removeRequirement: (id: string, reqId: string) => request<D>(`${base('SHC', id)}/requirements/${reqId}`, { method: 'DELETE' }),
  requestCompliance: (id: string, instructions: string, row_version: number) => post<D>(`${base('SHC', id)}/compliance/request`, { instructions, row_version }),
  startReview: (id: string, row_version: number) => post<D>(`${base('SHC', id)}/review/start`, { row_version }),
  recordFinding: (id: string, body: { requirement_id: string | null; assessment: string; text: string }) => post<D>(`${base('SHC', id)}/findings`, body),
  editFinding: (id: string, findingId: string, body: { assessment?: string; text?: string; row_version: number }) => patch<D>(`${base('SHC', id)}/findings/${findingId}`, body),
  recordProceeding: (id: string, body: { held_local: string; timezone: string; participants: string; text: string; schedule_id: string | null }) => post<D>(`${base('SHC', id)}/proceedings`, body),
  saveRecommendation: (id: string, option: string | null, sections: Record<string, string>, row_version: number) => put<D>(`${base('SHC', id)}/recommendation`, { option, sections, row_version }),
  submitRecommendation: (id: string, idempotency_key: string, row_version: number) => post<D>(`${base('SHC', id)}/recommendation/submit`, { idempotency_key, row_version }),
  amendRecommendation: (id: string, reason: string, row_version: number) => post<D>(`${base('SHC', id)}/recommendation/amend`, { reason, row_version }),
  completeShc: (id: string, summary: string, row_version: number) => post<D>(`${base('SHC', id)}/complete`, { summary, row_version }),
}

/** Institution side: committee status of its own applications, and SHC compliance. */
export const complianceApi = {
  status: (applicationId: string) => request<InstitutionStatus>(`/api/applications/${applicationId}/committee`),
  list: () => request<ComplianceSummary[]>('/api/compliance'),
  get: (id: string) => request<ComplianceRound>(`/api/compliance/${id}`),
  save: (id: string, body: { explanation?: string | null; items: { requirement_id: string; explanation: string }[]; row_version: number | null }) => put<ComplianceRound>(`/api/compliance/${id}/response`, body),
  upload: (id: string, requirementId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<ComplianceRound>(`/api/compliance/${id}/requirements/${requirementId}/documents`, { method: 'POST', body: form })
  },
  submit: (id: string, idempotency_key: string, row_version: number) => post<ComplianceRound>(`/api/compliance/${id}/response/submit`, { idempotency_key, row_version }),
  downloadUrl: (documentId: string) => `/api/documents/${documentId}/download`,
}
