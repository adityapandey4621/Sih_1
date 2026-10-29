import { request } from '../../lib/api'
import type { ApplicationEvidence, Extraction, HistoryEvent, ReviewListItem, Version } from './types'

export const evidenceApi = {
  ownEvidence: (applicationId: string) => request<ApplicationEvidence>(`/api/applications/${applicationId}/evidence`),
  versions: (applicationId: string) => request<Version[]>(`/api/applications/${applicationId}/documents`),
  reviewList: () => request<ReviewListItem[]>('/api/review/applications'),
  reviewEvidence: (applicationId: string) => request<ApplicationEvidence>(`/api/review/applications/${applicationId}/evidence`),
  extraction: (documentId: string) => request<Extraction>(`/api/documents/${documentId}/extraction`),
  history: (documentId: string) => request<HistoryEvent[]>(`/api/documents/${documentId}/history`),
  retry: (documentId: string) => request<Extraction>(`/api/documents/${documentId}/retry`, { method: 'POST' }),
  downloadUrl: (documentId: string) => `/api/documents/${documentId}/download`,
  previewUrl: (documentId: string, page: number) => `/api/documents/${documentId}/pages/${page}/preview`,
}
