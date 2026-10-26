import { request } from '../../lib/api'
import type { ApplicationDetail, ApplicationPage, CatalogueOptions, ListFilters, ResolvedChecklist } from './types'

const qs = (params: Record<string, string | number | undefined>) => {
  const s = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') s.set(k, String(v))
  const out = s.toString()
  return out ? `?${out}` : ''
}

export const applicationsApi = {
  options: () => request<CatalogueOptions>('/api/catalogue/options'),
  preview: (categoryId: string, academicYear: string) =>
    request<ResolvedChecklist>(`/api/catalogue/checklist${qs({ category_id: categoryId, academic_year: academicYear })}`),
  create: (categoryId: string, academicYear: string) =>
    request<ApplicationDetail>('/api/applications', { method: 'POST', body: { category_id: categoryId, academic_year: academicYear } }),
  list: (f: ListFilters) => request<ApplicationPage>(`/api/applications${qs({ ...f })}`),
  get: (id: string) => request<ApplicationDetail>(`/api/applications/${id}`),
  saveDraft: (id: string, formData: Record<string, unknown>, rowVersion: number) =>
    request<ApplicationDetail>(`/api/applications/${id}`, { method: 'PATCH', body: { form_data: formData, row_version: rowVersion } }),
  rebind: (id: string) => request<ApplicationDetail>(`/api/applications/${id}/rebind`, { method: 'POST' }),
  upload: (id: string, itemKey: string, file: File) => {
    const body = new FormData()
    body.set('item_key', itemKey)
    body.set('file', file)
    return request<ApplicationDetail>(`/api/applications/${id}/documents`, { method: 'POST', body })
  },
  removeDocument: (id: string, documentId: string) => request<ApplicationDetail>(`/api/applications/${id}/documents/${documentId}`, { method: 'DELETE' }),
  downloadUrl: (id: string, documentId: string) => `/api/applications/${id}/documents/${documentId}/download`,
  submit: (id: string, rowVersion: number) => request<ApplicationDetail>(`/api/applications/${id}/submit`, { method: 'POST', body: { row_version: rowVersion } }),
}
