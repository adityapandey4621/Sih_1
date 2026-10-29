import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../../lib/api'
import { usePolling } from '../../lib/usePolling'
import { Alert, ErrorState, LoadingState } from '../../components/ui/feedback'
import { Section } from '../applications/parts'
import { DocumentViewer, type FindingContext } from '../evidence/DocumentViewer'
import { FindingList, StatePill } from '../evidence/parts'
import type { ApplicationEvidence } from '../evidence/types'
import '../evidence/evidence.css'
import { scrutinyApi } from './scrutinyApi'

/** The original documents with what was read from them, beside the case. Every extracted value and automated issue names its version and page. */
export function EvidenceTab({ caseId, focusItem, refreshKey, onRecordFinding, load: loadEvidence }: { caseId: string; focusItem: string | null; refreshKey: number; onRecordFinding?: (ctx: FindingContext) => void; load?: () => Promise<ApplicationEvidence> }) {
  const [data, setData] = useState<ApplicationEvidence | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const d = await (loadEvidence ? loadEvidence() : scrutinyApi.evidence(caseId))
      setData(d)
      setSelected((cur) => cur ?? d.items[0]?.item_key ?? null)
      setError(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'The evidence could not be loaded.')
    }
  }, [caseId, loadEvidence])
  useEffect(() => {
    void load()
  }, [load, refreshKey])
  useEffect(() => {
    if (focusItem) setSelected(focusItem)
  }, [focusItem])
  usePolling(load, !!data?.items.some((i) => i.slot_state && !i.slot_state.terminal))

  if (error && !data) return <ErrorState message={error} onRetry={() => void load()} />
  if (!data) return <LoadingState label="Loading evidence…" />
  const item = data.items.find((i) => i.item_key === selected) ?? data.items[0]

  return (
    <>
      <Alert tone="info" title="Original evidence and automated readings">
        {data.disclaimer} Uncertain or missing readings are shown as such. Nothing on this tab is a finding until you record one.
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
                  {i.current && <span className="mono">version {i.current.version_number}{i.versions.length > 1 ? ` of ${i.versions.length}` : ''}</span>}
                  {i.findings.length > 0 && <span className="mono">{i.findings.length} automated issue{i.findings.length > 1 ? 's' : ''}</span>}
                </button>
              </li>
            ))}
          </ul>
        </nav>
        <div className="review-main">{item && <DocumentViewer key={item.item_key} item={item} onChanged={() => void load()} onRecordFinding={onRecordFinding} />}</div>
      </div>
      {data.cross_document_findings.length > 0 && (
        <Section title="Across documents (automated)">
          <FindingList findings={data.cross_document_findings} reviewer onReview={onRecordFinding ? (f) => onRecordFinding({ item_key: '', document_id: '', version_number: 0, page: 0, preliminary_finding_id: f.id }) : undefined} />
        </Section>
      )}
    </>
  )
}
