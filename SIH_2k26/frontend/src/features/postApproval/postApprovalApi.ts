import { request } from '../../lib/api'
import type { ClockList, ClockView, InstitutionOverview, IssuedCase, ObligationDetail, ObligationInput, ObligationList, SchedulerHealth, ValidityItemView, ValidityList } from './types'

const qs = (params: Record<string, string | number | boolean | undefined | null>) => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '' && v !== false) p.set(k, String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}
const post = <T>(path: string, body: unknown) => request<T>(path, { method: 'POST', body })
const B = '/api/post-approval'

export const newKey = (prefix: string) => `${prefix}-${crypto.randomUUID().replace(/-/g, '').slice(0, 16)}`

/** Every call is checked again by the server for role and scope; the UI only mirrors what the response says is allowed (`can`). */
export const postApprovalApi = {
  overview: () => request<InstitutionOverview>(`${B}/overview`),
  obligations: (q: { status?: string; q?: string; due?: string; page?: number }) => request<ObligationList>(`${B}/obligations${qs({ ...q, page_size: 20 })}`),
  obligation: (id: string) => request<ObligationDetail>(`${B}/obligations/${id}`),
  cases: (q: string) => request<{ items: IssuedCase[] }>(`${B}/cases${qs({ q })}`),
  verifiers: () => request<{ items: { id: string; name: string }[] }>(`${B}/verifiers`),
  define: (applicationId: string, body: ObligationInput) => post<ObligationDetail>(`${B}/applications/${applicationId}/obligations`, body),
  submitEvidence: (id: string, body: { files: { label: string; file: File }[]; note: string; row_version: number; idempotency_key: string }) => {
    const form = new FormData()
    form.append('idempotency_key', body.idempotency_key)
    form.append('row_version', String(body.row_version))
    form.append('note', body.note)
    for (const f of body.files) {
      form.append('labels', f.label)
      form.append('files', f.file)
    }
    return request<ObligationDetail>(`${B}/obligations/${id}/evidence`, { method: 'POST', body: form })
  },
  review: (id: string, body: { outcome: string; submission_id: string; reason: string; internal_note: string | null; row_version: number; idempotency_key: string }) => post<ObligationDetail>(`${B}/obligations/${id}/review`, body),
  withdraw: (id: string, reason: string, row_version: number) => post<ObligationDetail>(`${B}/obligations/${id}/withdraw`, { reason, row_version }),
  reassign: (id: string, verifier_id: string, reason: string) => post<ObligationDetail>(`${B}/obligations/${id}/reassign`, { verifier_id, reason }),
  validity: (q: { status?: string; q?: string; page?: number }) => request<ValidityList>(`${B}/validity${qs({ ...q, page_size: 20 })}`),
  renew: (itemId: string, body: { file: File; declared_date: string; idempotency_key: string }) => {
    const form = new FormData()
    form.append('file', body.file)
    form.append('declared_date', body.declared_date)
    form.append('idempotency_key', body.idempotency_key)
    return request<ValidityItemView>(`${B}/validity/items/${itemId}/renewals`, { method: 'POST', body: form })
  },
  verifyDate: (versionId: string, body: { verified_date: string; reason: string; internal_note: string | null; idempotency_key: string }) => post<ValidityItemView>(`${B}/validity/versions/${versionId}/verify`, body),
  rejectDate: (versionId: string, reason: string, internal_note: string | null) => post<ValidityItemView>(`${B}/validity/versions/${versionId}/reject`, { reason, internal_note }),
  resolveItem: (itemId: string, reason: string) => post<ValidityItemView>(`${B}/validity/items/${itemId}/resolve`, { reason }),
}

export const clocksApi = {
  list: (q: { kind?: string; status?: string; due?: string; q?: string; page?: number }) => request<ClockList>(`/api/clocks${qs({ ...q, page_size: 25 })}`),
  detail: (id: string) => request<ClockView>(`/api/clocks/${id}`),
  health: () => request<SchedulerHealth>('/api/clocks/health'),
  pause: (id: string, body: { reason_code: string | null; reason: string; reference: string | null; row_version: number }) => post<ClockView>(`/api/clocks/${id}/pause`, body),
  resume: (id: string, row_version: number) => post<ClockView>(`/api/clocks/${id}/resume`, { row_version }),
}
