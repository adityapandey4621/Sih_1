import { request } from '../../lib/api'
import type { ApplicationEvidence } from '../evidence/types'
import type { Assignable, CaseDetail, InstNotice, InstNoticeSummary, InstScrutiny, NotificationPage, NoticePreview, QueuePage } from './types'

const qs = (params: Record<string, string | number | boolean | undefined | null>) => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '' && v !== false) p.set(k, String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}

const post = <T>(path: string, body: unknown) => request<T>(path, { method: 'POST', body })

export interface QueueQuery {
  state?: string
  processing_exceptions?: boolean
  q?: string
  assigned?: string
  page?: number
  page_size?: number
}

export interface FindingInput {
  issue_type: string
  description: string
  item_key?: string | null
  document_id?: string | null
  page_number?: number | null
  institution_text?: string | null
  requested_correction?: string | null
  source_ref_key?: string | null
  visibility: 'INTERNAL_ONLY' | 'ELIGIBLE_FOR_NOTICE'
  preliminary_finding_id?: string | null
}

const base = (id: string) => `/api/scrutiny/cases/${id}`

/** Staff-facing scrutiny API. Every call is scope-checked on the server; the UI only mirrors `allowed_actions`. */
export const scrutinyApi = {
  queue: (q: QueueQuery) => request<QueuePage>(`/api/scrutiny/cases${qs({ ...q })}`),
  detail: (id: string) => request<CaseDetail>(base(id)),
  evidence: (id: string) => request<ApplicationEvidence>(`${base(id)}/evidence`),
  assignable: () => request<Assignable[]>('/api/scrutiny/assignable'),
  assign: (id: string, user_id: string, reason: string | null, row_version: number) => post<CaseDetail>(`${base(id)}/assign`, { user_id, reason, row_version }),
  determine: (id: string, stage_key: string | null, reason: string) => post<CaseDetail>(`${base(id)}/handoff/determine`, { stage_key, reason }),
  start: (id: string, row_version: number) => post<CaseDetail>(`${base(id)}/start`, { row_version }),
  saveNotes: (id: string, notes: string, row_version: number) => request<CaseDetail>(`${base(id)}/notes`, { method: 'PUT', body: { notes, row_version } }),
  recordFinding: (id: string, f: FindingInput) => post<CaseDetail>(`${base(id)}/findings`, f),
  editFinding: (id: string, findingId: string, f: Partial<FindingInput> & { row_version: number }) => request<CaseDetail>(`${base(id)}/findings/${findingId}`, { method: 'PATCH', body: f }),
  conclude: (id: string, findingId: string, state: 'RESOLVED' | 'UNRESOLVED' | 'NOT_SUPPORTED', reason: string, row_version: number) =>
    post<CaseDetail>(`${base(id)}/findings/${findingId}/disposition`, { state, reason, row_version }),
  complete: (id: string, row_version: number, summary: string, acknowledge_unresolved: boolean) => post<CaseDetail>(`${base(id)}/complete`, { row_version, summary, acknowledge_unresolved }),

  createNotice: (id: string, finding_ids: string[], summary: string) => post<CaseDetail>(`${base(id)}/notices`, { finding_ids, summary }),
  updateNotice: (noticeId: string, body: { row_version: number; summary?: string; items?: { id: string; title?: string; description?: string; requested_correction?: string }[]; add_finding_ids?: string[] }) =>
    request<CaseDetail>(`/api/scrutiny/notices/${noticeId}`, { method: 'PATCH', body }),
  submitNotice: (noticeId: string, row_version: number) => post<CaseDetail>(`/api/scrutiny/notices/${noticeId}/submit`, { row_version }),
  withdrawNotice: (noticeId: string, reason: string) => post<CaseDetail>(`/api/scrutiny/notices/${noticeId}/withdraw`, { reason }),
  previewNotice: (noticeId: string) => request<NoticePreview>(`/api/scrutiny/notices/${noticeId}/preview`),
  issueNotice: (noticeId: string, body: { content_hash: string; issue_reason: string; acknowledge_unsourced: boolean; idempotency_key: string; row_version: number }) =>
    post<CaseDetail>(`/api/scrutiny/notices/${noticeId}/issue`, body),
  returnNotice: (noticeId: string, reason: string) => post<CaseDetail>(`/api/scrutiny/notices/${noticeId}/return`, { reason }),
}

/** Institution-facing notices and responses. */
export const noticesApi = {
  list: (openOnly = false) => request<InstNoticeSummary[]>(`/api/notices${qs({ open_only: openOnly })}`),
  get: (id: string) => request<InstNotice>(`/api/notices/${id}`),
  saveDraft: (id: string, body: { explanation?: string | null; items: { notice_item_id: string; explanation: string }[]; row_version: number | null }) =>
    request<InstNotice>(`/api/notices/${id}/response`, { method: 'PUT', body }),
  upload: (id: string, itemId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<InstNotice>(`/api/notices/${id}/items/${itemId}/documents`, { method: 'POST', body: form })
  },
  submit: (id: string, idempotency_key: string, row_version: number) => post<InstNotice>(`/api/notices/${id}/response/submit`, { idempotency_key, row_version }),
  applicationScrutiny: (applicationId: string) => request<InstScrutiny>(`/api/applications/${applicationId}/scrutiny`),
  downloadUrl: (documentId: string) => `/api/documents/${documentId}/download`,
}

export const notificationsApi = {
  list: (page = 1, unreadOnly = false) => request<NotificationPage>(`/api/notifications${qs({ page, unread_only: unreadOnly })}`),
  markRead: (id: string) => post<unknown>(`/api/notifications/${id}/read`, undefined),
  markAll: () => post<{ marked: number }>('/api/notifications/read-all', undefined),
}
