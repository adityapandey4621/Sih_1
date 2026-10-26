import { request } from '../../lib/api'
import type { Category, Source } from '../applications/types'
import type { Gap, RuleSetOut, SourceInput, VersionContentInput, VersionDetail } from './types'

const post = <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body: body ?? {} })

export const rulesApi = {
  sets: () => request<RuleSetOut[]>('/api/rules/sets'),
  createSet: (body: { key: string; title: string; category_id: string; classification: 'OFFICIAL' | 'DEMO' }) => post<VersionDetail>('/api/rules/sets', body),
  newVersion: (ruleSetId: string) => post<VersionDetail>(`/api/rules/sets/${ruleSetId}/versions`),
  version: (id: string) => request<VersionDetail>(`/api/rules/versions/${id}`),
  saveContent: (id: string, body: VersionContentInput) => request<VersionDetail>(`/api/rules/versions/${id}`, { method: 'PUT', body }),
  submitReview: (id: string, rowVersion: number, note?: string) => post<VersionDetail>(`/api/rules/versions/${id}/submit-review`, { row_version: rowVersion, note: note || null }),
  requestChanges: (id: string, note: string) => post<VersionDetail>(`/api/rules/versions/${id}/request-changes`, { note }),
  publish: (id: string, supersede: boolean, note?: string) => post<VersionDetail>(`/api/rules/versions/${id}/publish`, { supersede, note: note || null }),
  retire: (id: string, reason: string) => post<VersionDetail>(`/api/rules/versions/${id}/retire`, { reason }),

  categories: () => request<Category[]>('/api/rules/categories'),
  createCategory: (body: { key: string; name: string; description: string; applicability_note: string | null; source_id: string | null; is_active: boolean }) =>
    post<Category>('/api/rules/categories', body),

  sources: () => request<Source[]>('/api/rules/sources'),
  createSource: (body: SourceInput) => post<Source>('/api/rules/sources', body),
  updateSource: (id: string, body: SourceInput) => request<Source>(`/api/rules/sources/${id}`, { method: 'PUT', body }),

  gaps: () => request<Gap[]>('/api/rules/gaps'),
  resolveGap: (id: string, note: string) => post<Gap>(`/api/rules/gaps/${id}/resolve`, { note }),
}
