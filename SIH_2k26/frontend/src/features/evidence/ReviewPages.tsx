import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError } from '../../lib/api'
import { formatDate } from '../../lib/format'
import { usePolling } from '../../lib/usePolling'
import { useResource } from '../../lib/useResource'
import { Alert, EmptyState, ErrorState, LoadingState } from '../../components/ui/feedback'
import { ResourceView } from '../admin/shared'
import { Section } from '../applications/parts'
import { PageHeader } from '../shell/PageHeader'
import '../applications/applications.css'
import { DocumentViewer } from './DocumentViewer'
import { evidenceApi } from './evidenceApi'
import { FindingList, StatePill } from './parts'
import type { ApplicationEvidence } from './types'
import './evidence.css'

export function ReviewListPage() {
  const [res, reload] = useResource(evidenceApi.reviewList, [])
  return (
    <>
      <PageHeader
        title="Evidence review"
        description="Submitted applications and what automated document reading found. Read-only: scrutiny actions arrive in a later phase."
        crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Evidence review' }]}
      />
      <Alert tone="info">
        Every submitted application is listed here because no assignment mechanism exists yet. Drafts are the institution's work in progress and are not shown.
      </Alert>
      <ResourceView resource={res} reload={reload}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title="No submitted applications yet">When an institution submits an application, it appears here with its documents and the preliminary results of reading them.</EmptyState>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <caption className="sr-only">Submitted applications</caption>
                <thead>
                  <tr>
                    <th scope="col">Reference</th>
                    <th scope="col">Institution</th>
                    <th scope="col">Category / year</th>
                    <th scope="col">Submitted</th>
                    <th scope="col">Files</th>
                    <th scope="col">Needs a look</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((a) => (
                    <tr key={a.id}>
                      <td>
                        <Link className="mono link" to={`/app/review/${a.id}`}>
                          {a.reference}
                        </Link>
                      </td>
                      <td>{a.institution_name}</td>
                      <td>
                        {a.category_name} <span className="mono muted">· {a.academic_year}</span>
                      </td>
                      <td className="mono nowrap">{formatDate(a.submitted_at)}</td>
                      <td className="mono">
                        {a.documents}
                        {a.still_processing > 0 && <span className="muted"> · {a.still_processing} still processing</span>}
                      </td>
                      <td>
                        {a.open_findings > 0 || a.review_recommended > 0 ? (
                          <span>
                            {a.open_findings} preliminary issue{a.open_findings === 1 ? '' : 's'}
                            {a.review_recommended > 0 && <span className="muted"> · {a.review_recommended} file{a.review_recommended === 1 ? '' : 's'} flagged</span>}
                          </span>
                        ) : (
                          <span className="muted">None found</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </ResourceView>
    </>
  )
}

export function ReviewApplicationPage() {
  const { id = '' } = useParams()
  const [data, setData] = useState<ApplicationEvidence | null>(null)
  const [error, setError] = useState<{ message: string; notFound: boolean } | null>(null)
  const [selected, setSelected] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const d = await evidenceApi.reviewEvidence(id)
      setData(d)
      setSelected((cur) => cur ?? d.items[0]?.item_key ?? null)
      setError(null)
    } catch (e) {
      setError({ message: e instanceof ApiError ? e.message : 'The evidence could not be loaded.', notFound: e instanceof ApiError && e.status === 404 })
    }
  }, [id])
  useEffect(() => {
    void load()
  }, [load])
  usePolling(load, !!data?.items.some((i) => i.slot_state && !i.slot_state.terminal))

  if (error && !data) {
    return error.notFound ? (
      <EmptyState title="Application not found">It may not exist, or it has not been submitted yet.</EmptyState>
    ) : (
      <ErrorState message={error.message} onRetry={() => void load()} />
    )
  }
  if (!data) return <LoadingState label="Loading evidence…" />
  const item = data.items.find((i) => i.item_key === selected) ?? data.items[0]

  return (
    <>
      <PageHeader
        title={data.reference}
        description={`${data.institution_name} · ${data.category_name} · ${data.academic_year}`}
        crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Evidence review', to: '/app/review' }, { label: data.reference }]}
      />
      <Alert tone="info" title="Read-only evidence package">
        {data.disclaimer} Nothing on this page approves, rejects or advances the application, and no scrutiny action is available yet.
        {!data.ocr_available && ' Text recognition is not available on this system, so scanned pages could not be read.'}
        {data.malware_scanner === 'none' && ' No malware scanner is configured: files have not been scanned.'}
      </Alert>

      <div className="review-layout">
        <nav className="review-items" aria-label="Documents">
          <ul>
            {data.items.map((i) => (
              <li key={i.item_key}>
                <button type="button" className="review-items__btn" aria-current={i.item_key === item?.item_key ? 'true' : undefined} onClick={() => setSelected(i.item_key)}>
                  <span className="review-items__label">{i.label}</span>
                  <span className="muted">{i.required ? 'Required' : 'Optional'}</span>
                  {i.slot_state ? <StatePill state={i.slot_state} /> : <span className="muted">No file submitted</span>}
                  {i.findings.length > 0 && <span className="mono">{i.findings.length} preliminary issue{i.findings.length > 1 ? 's' : ''}</span>}
                </button>
              </li>
            ))}
          </ul>
        </nav>
        <div className="review-main">{item && <DocumentViewer key={item.item_key} item={item} onChanged={() => void load()} />}</div>
      </div>

      {data.cross_document_findings.length > 0 && (
        <Section title="Across documents">
          <FindingList findings={data.cross_document_findings} reviewer />
        </Section>
      )}
    </>
  )
}
