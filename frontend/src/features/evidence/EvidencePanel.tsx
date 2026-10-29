import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../../lib/api'
import { usePolling } from '../../lib/usePolling'
import { Alert, ErrorState, LoadingState } from '../../components/ui/feedback'
import { Section } from '../applications/parts'
import { evidenceApi } from './evidenceApi'
import { FieldList, FindingList, StatePill } from './parts'
import type { ApplicationEvidence } from './types'
import './evidence.css'

/** The institution's own view of document processing: state, values read, and preliminary issues, with plain disclaimers. */
export function EvidencePanel({ applicationId, refreshKey }: { applicationId: string; refreshKey: number }) {
  const [data, setData] = useState<ApplicationEvidence | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setData(await evidenceApi.ownEvidence(applicationId))
      setError(null)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Document processing status could not be loaded.')
    }
  }, [applicationId])

  useEffect(() => {
    void load()
  }, [load, refreshKey])

  const pending = !!data?.items.some((i) => i.slot_state && !i.slot_state.terminal)
  usePolling(load, pending)

  if (error && !data) return <ErrorState title="Processing status unavailable" message={error} onRetry={() => void load()} />
  if (!data) return <LoadingState label="Loading document processing status…" />
  const withFiles = data.items.filter((i) => i.current)

  return (
    <Section title="Document processing" aside={pending ? <span className="muted" role="status">Updating…</span> : undefined}>
      <Alert tone="info">
        Uploaded files are read automatically to help reviewers. This is a <strong>technical check only</strong>: it does not verify or accept any document, and it never changes the status of your application.
        {data.status === 'SUBMITTED' && ' Authorised officials review submitted evidence separately.'}
      </Alert>
      {!data.ocr_available && <p className="note">Scanned pages cannot be read on this system right now (text recognition is not available). Digital PDFs are unaffected.</p>}
      {withFiles.length === 0 && <p className="note">No files have been uploaded yet. Upload them in the Documents step; their processing status will appear here.</p>}
      <div className="evidence-items">
        {data.items.map((item) => (
          <article key={item.item_key} className="evidence-item">
            <div className="evidence-item__head">
              <h3 className="panel__sub">{item.label}</h3>
              {item.slot_state ? <StatePill state={item.slot_state} /> : <span className="muted">No file submitted</span>}
            </div>
            {item.current && (
              <>
                <p className="mono muted">
                  {item.current.original_filename} · version {item.current.version_number}
                  {item.versions.length > 1 ? ` (${item.versions.length - 1} earlier version${item.versions.length > 2 ? 's' : ''} kept)` : ''}
                </p>
                <p className="note">{item.slot_state?.explanation}</p>
                {item.slot_state?.scan_note && <p className="note">{item.slot_state.scan_note}</p>}
                {item.fields.length > 0 && <FieldList fields={item.fields} configured={item.configured_fields} />}
              </>
            )}
            {item.findings.length > 0 && <FindingList findings={item.findings} reviewer={false} />}
          </article>
        ))}
      </div>
      {data.cross_document_findings.length > 0 && (
        <>
          <h3 className="panel__sub">Across documents</h3>
          <FindingList findings={data.cross_document_findings} reviewer={false} />
        </>
      )}
    </Section>
  )
}
