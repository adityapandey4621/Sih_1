import { request } from '../../lib/api'
import type { ApplicationEvidence } from '../evidence/types'
import type { DecisionCaseDetail, DecisionQueue, InstitutionDecision } from './types'

const qs = (params: Record<string, string | number | boolean | undefined | null>) => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '' && v !== false) p.set(k, String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}
const post = <T>(path: string, body: unknown) => request<T>(path, { method: 'POST', body })

export interface DecisionQuery {
  state?: string
  q?: string
  page?: number
  page_size?: number
}

type D = DecisionCaseDetail
const base = (id: string) => `/api/decisions/cases/${id}`

/** Staff-facing decision API. Every call is checked again by the server for role and case; the UI only mirrors `allowed_actions`. */
export const decisionsApi = {
  queue: (q: DecisionQuery) => request<DecisionQueue>(`/api/decisions/cases${qs({ ...q })}`),
  detail: (id: string) => request<D>(base(id)),
  evidence: (id: string) => request<ApplicationEvidence>(`${base(id)}/evidence`),
  observe: (id: string, text: string, idempotency_key: string) => post<D>(`${base(id)}/observations`, { text, idempotency_key }),
  decide: (id: string, body: { outcome: string; reasons: string; speaking_order: string | null; meeting_reference: string | null; considered: string[]; idempotency_key: string; row_version: number }) => post<D>(`${base(id)}/decision`, body),
  correct: (id: string, body: { outcome: string; reasons: string; speaking_order: string | null; meeting_reference: string | null; considered: string[]; correction_reason: string; idempotency_key: string; row_version: number }) =>
    post<D>(`${base(id)}/decision/correct`, body),
  issue: (id: string, body: { document_reference: string; file: File | null; idempotency_key: string; row_version: number }) => {
    const form = new FormData()
    form.append('idempotency_key', body.idempotency_key)
    form.append('row_version', String(body.row_version))
    if (body.document_reference) form.append('document_reference', body.document_reference)
    if (body.file) form.append('file', body.file)
    return request<D>(`${base(id)}/issue`, { method: 'POST', body: form })
  },
  ratify: (id: string, body: { outcome: string; reasons: string; reference: string | null; correction_reason: string | null; idempotency_key: string }) => post<D>(`${base(id)}/ratification`, body),
  close: (id: string, row_version: number) => post<D>(`${base(id)}/close`, { row_version }),
  councilDecision: (appealId: string, body: { outcome: string; reasons: string; speaking_order: string | null; reference: string | null; correction_reason: string | null; idempotency_key: string }) =>
    post<D>(`/api/decisions/appeals/${appealId}/council-decision`, body),
}

/** Institution side: the official status of its own application, and its appeal. */
export const institutionDecisionApi = {
  status: (applicationId: string) => request<InstitutionDecision>(`/api/applications/${applicationId}/decision`),
  appeal: (applicationId: string, grounds: string, idempotency_key: string) => post<InstitutionDecision>(`/api/applications/${applicationId}/appeal`, { grounds, idempotency_key }),
  addFile: (appealId: string, file: File, description: string) => {
    const form = new FormData()
    form.append('file', file)
    form.append('description', description)
    return request<InstitutionDecision>(`/api/appeals/${appealId}/documents`, { method: 'POST', body: form })
  },
  addLink: (appealId: string, url: string, description: string) => post<InstitutionDecision>(`/api/appeals/${appealId}/documents/link`, { url, description }),
}
