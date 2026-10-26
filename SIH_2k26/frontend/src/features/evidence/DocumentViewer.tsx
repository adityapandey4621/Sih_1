import { useCallback, useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, Download } from 'lucide-react'
import { ApiError } from '../../lib/api'
import { formatDate, humanise } from '../../lib/format'
import { usePolling } from '../../lib/usePolling'
import { Button } from '../../components/ui/Button'
import { Alert, Badge, LoadingState } from '../../components/ui/feedback'
import { evidenceApi } from './evidenceApi'
import { FieldList, FindingList, StatePill, VersionList } from './parts'
import type { Extraction, HistoryEvent, ItemEvidence, Version } from './types'
import './evidence.css'

type Tab = 'values' | 'text' | 'findings' | 'versions'

const PAGE_STATUS: Record<string, string> = {
  OK: 'Read',
  LOW_CONFIDENCE: 'Low confidence',
  EMPTY: 'No text recognised',
  OCR_UNAVAILABLE: 'Could not be read (no text recognition available)',
  FAILED: 'Failed',
}

/** Two columns: the page as the institution supplied it, and what was read from it, with uncertainty visible. */
export function DocumentViewer({ item, onChanged }: { item: ItemEvidence; onChanged: () => void }) {
  const [version, setVersion] = useState<Version | null>(item.current)
  const [page, setPage] = useState(1)
  const [tab, setTab] = useState<Tab>('values')
  const [extraction, setExtraction] = useState<Extraction | null>(null)
  const [history, setHistory] = useState<HistoryEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const [imageFailed, setImageFailed] = useState(false)
  const [retrying, setRetrying] = useState(false)

  // a different checklist item, or a new current version, resets the view
  useEffect(() => {
    setVersion(item.current)
    setPage(1)
  }, [item.item_key, item.current?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const load = useCallback(async () => {
    if (!version) return
    try {
      const [ex, hist] = await Promise.all([evidenceApi.extraction(version.id), evidenceApi.history(version.id)])
      setExtraction(ex)
      setHistory(hist)
      setError(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'The extraction could not be loaded.')
    }
  }, [version?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setExtraction(null)
    setImageFailed(false)
    void load()
  }, [load])
  usePolling(load, !!extraction && !extraction.state.terminal)
  useEffect(() => setImageFailed(false), [page, version?.id])

  if (!version) {
    return (
      <div className="viewer-empty">
        <p>
          <strong>No file has been submitted</strong> for “{item.label}”.
        </p>
        <p className="note">{item.required ? 'This is a required item.' : 'This item is optional.'} A missing file is different from an unreadable one: nothing was attached to this requirement.</p>
        {item.findings.length > 0 && <FindingList findings={item.findings} reviewer />}
      </div>
    )
  }
  const state = extraction?.state ?? version.state
  const pageCount = extraction?.state.page_count ?? version.state.page_count ?? 1
  const pageInfo = extraction?.pages.find((p) => p.page_number === page)
  const readable = state.technical_status !== 'FAILED' && state.scan_status !== 'INFECTED'

  async function retry() {
    setRetrying(true)
    try {
      await evidenceApi.retry(version!.id)
      await load()
      onChanged()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'The retry could not be requested.')
    } finally {
      setRetrying(false)
    }
  }

  return (
    <div className="viewer">
      <div className="viewer__bar">
        <div>
          <strong>{version.original_filename}</strong>
          <span className="mono muted"> · version {version.version_number}{version.is_current ? ' (current)' : ' (earlier version)'}</span>
        </div>
        <div className="viewer__bar-actions">
          <StatePill state={state} />
          <a className="btn btn--quiet btn--sm" href={evidenceApi.downloadUrl(version.id)}>
            <Download size={16} aria-hidden="true" /> Download original
          </a>
        </div>
      </div>
      <p className="note">{state.explanation}</p>
      {state.scan_note && <p className="note">{state.scan_note}</p>}
      {error && <Alert tone="error">{error}</Alert>}
      {state.can_retry && (
        <div className="inline-actions">
          <Button size="sm" variant="secondary" onClick={retry} loading={retrying}>
            Retry processing
          </Button>
          <span className="muted">Only offered where trying again can help. Corrupt or unsupported files cannot be fixed by retrying.</span>
        </div>
      )}

      <div className="viewer__columns">
        <section className="viewer__page" aria-label="Page preview">
          <div className="viewer__pager">
            <Button size="sm" variant="quiet" disabled={page <= 1} onClick={() => setPage((p) => p - 1)} aria-label="Previous page">
              <ChevronLeft size={16} aria-hidden="true" />
            </Button>
            <span className="mono">
              Page {page} of {pageCount}
            </span>
            <Button size="sm" variant="quiet" disabled={page >= pageCount} onClick={() => setPage((p) => p + 1)} aria-label="Next page">
              <ChevronRight size={16} aria-hidden="true" />
            </Button>
            {pageInfo && <Badge tone={pageInfo.status === 'OK' ? 'success' : 'warning'}>{PAGE_STATUS[pageInfo.status] ?? pageInfo.status}</Badge>}
          </div>
          {pageInfo && (
            <p className="mono muted">
              {pageInfo.method === 'OCR' ? 'text recognition' : 'PDF text layer'}
              {pageInfo.confidence !== null ? ` · ${pageInfo.confidence.toFixed(0)}% mean confidence` : ''}
            </p>
          )}
          {readable && !imageFailed ? (
            <img className="viewer__img" src={evidenceApi.previewUrl(version.id, page)} alt={`Page ${page} of ${version.original_filename}`} onError={() => setImageFailed(true)} />
          ) : (
            <div className="viewer__noimg">{readable ? 'This page could not be previewed.' : 'No preview: the file could not be read or is quarantined.'}</div>
          )}
        </section>

        <section className="viewer__data" aria-label="Extracted information">
          <div className="tabbar" role="tablist" aria-label="Extracted information">
            {(
              [
                ['values', 'Values'],
                ['text', 'Page text'],
                ['findings', `Findings (${item.findings.length})`],
                ['versions', `Versions (${item.versions.length})`],
              ] as [Tab, string][]
            ).map(([k, label]) => (
              <button key={k} type="button" role="tab" className="tabbar__btn" aria-selected={tab === k} onClick={() => setTab(k)}>
                {label}
              </button>
            ))}
          </div>
          {!extraction && !error && <LoadingState label="Loading extraction…" />}
          {extraction && tab === 'values' && (
            <>
              <FieldList fields={extraction.fields} onPage={setPage} configured={item.configured_fields} />
              {extraction.pages.length > 0 && (
                <p className="note">
                  Pages: {extraction.pages.map((p) => `${p.page_number} (${p.method === 'OCR' ? 'scan' : 'text'}${p.status !== 'OK' ? `, ${PAGE_STATUS[p.status]?.toLowerCase() ?? p.status}` : ''})`).join(' · ')}
                </p>
              )}
            </>
          )}
          {extraction && tab === 'text' && (
            <div>
              {pageInfo?.text ? <pre className="viewer__text" tabIndex={0} aria-label={`Text of page ${page}`}>{pageInfo.text}</pre> : <p className="note">No text was read from this page. {pageInfo?.note}</p>}
            </div>
          )}
          {tab === 'findings' && <FindingList findings={item.findings} reviewer />}
          {tab === 'versions' && (
            <>
              <VersionList versions={item.versions} selectedId={version.id} onSelect={setVersion} downloadUrl={evidenceApi.downloadUrl} />
              <h3 className="panel__sub">Processing history of this version</h3>
              <ol className="history">
                {history.map((h, i) => (
                  <li key={i} className="history__item">
                    <span className="mono history__at">{formatDate(h.at)}</span>
                    <span>
                      <strong>{humanise(h.action)}</strong> — {h.actor}
                    </span>
                  </li>
                ))}
              </ol>
            </>
          )}
        </section>
      </div>
    </div>
  )
}
