import { useId, useRef, useState } from 'react'
import { Download, FileText, RefreshCw, Trash2, Upload } from 'lucide-react'
import { ApiError } from '../../lib/api'
import { formatBytes, formatDate } from '../../lib/format'
import { Badge } from '../../components/ui/feedback'
import { Button } from '../../components/ui/Button'
import { evidenceApi } from '../evidence/evidenceApi'
import { StatePill } from '../evidence/parts'
import { applicationsApi } from './applicationsApi'
import type { ApplicationDetail, ChecklistState, DocumentRecord } from './types'

interface Props {
  applicationId: string
  item: ChecklistState
  documents: DocumentRecord[]
  editable: boolean
  onUpdated: (d: ApplicationDetail) => void
  /** Called after a retry was requested, so the processing panel refreshes too. */
  onProcessingChanged?: () => void
}

/**
 * One upload slot tied to a checklist item. What is shown about the file is *technical state*
 * (received, queued, read, flagged): it is never "verified", and the slot says so.
 */
export function DocumentSlot({ applicationId, item, documents, editable, onUpdated, onProcessingChanged }: Props) {
  const input = useRef<HTMLInputElement>(null)
  const errId = useId()
  const [busy, setBusy] = useState<'upload' | 'remove' | 'retry' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const mine = documents.filter((d) => d.item_key === item.item_key && !d.removed)
  const active = mine.find((d) => d.is_current)
  const earlier = mine.filter((d) => !d.is_current).sort((a, b) => b.version_number - a.version_number)

  async function pick(file: File | undefined) {
    if (!file) return
    setBusy('upload')
    setError(null)
    setNotice(null)
    try {
      const next = await applicationsApi.upload(applicationId, item.item_key, file)
      const now = next.documents.find((d) => d.item_key === item.item_key && d.is_current)
      if (active && now && now.id === active.id) setNotice('This is the same file as the current version, so nothing was changed.')
      onUpdated(next)
      onProcessingChanged?.()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'The file could not be uploaded.')
    } finally {
      setBusy(null)
      if (input.current) input.current.value = ''
    }
  }

  async function remove() {
    if (!active) return
    setBusy('remove')
    setError(null)
    try {
      onUpdated(await applicationsApi.removeDocument(applicationId, active.id))
      onProcessingChanged?.()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'The file could not be removed.')
    } finally {
      setBusy(null)
    }
  }

  async function retry() {
    if (!active) return
    setBusy('retry')
    setError(null)
    try {
      await evidenceApi.retry(active.id)
      onUpdated(await applicationsApi.get(applicationId))
      onProcessingChanged?.()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'A retry could not be requested.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="slot" data-state={active ? 'filled' : 'empty'}>
      <div className="slot__head">
        <span className="slot__label">{item.label}</span>
        <span className="slot__tags">
          {busy === 'upload' && <Badge tone="info">Upload in progress…</Badge>}
          <Badge tone={item.required ? 'info' : 'neutral'}>{item.required ? 'Required' : 'Optional'}</Badge>
        </span>
      </div>
      {item.explanation && <p className="note">{item.explanation}</p>}
      {item.clause_ref && <p className="checklist__meta mono">{item.clause_ref}</p>}

      {active ? (
        <div className="slot__file">
          <FileText size={20} aria-hidden="true" />
          <div className="slot__fileinfo">
            <span className="slot__filename">{active.original_filename}</span>
            <span className="mono muted">
              Version {active.version_number} · {formatBytes(active.size_bytes)} · {active.media_type} · uploaded {formatDate(active.uploaded_at)}
            </span>
            <span className="slot__state">
              {active.state ? <StatePill state={active.state} /> : <Badge tone="info">File received</Badge>}
              <Badge tone="neutral">Contents not verified</Badge>
            </span>
            {active.state && <span className="note">{active.state.explanation}</span>}
            {active.state?.scan_note && <span className="note">{active.state.scan_note}</span>}
          </div>
          <div className="slot__actions">
            <a className="btn btn--quiet btn--sm" href={evidenceApi.downloadUrl(active.id)}>
              <Download size={16} aria-hidden="true" /> Download
            </a>
            {active.state?.can_retry && (
              <Button size="sm" variant="secondary" onClick={retry} loading={busy === 'retry'} disabled={busy !== null}>
                <RefreshCw size={16} aria-hidden="true" /> Retry processing
              </Button>
            )}
            {editable && (
              <Button size="sm" variant="quiet" onClick={remove} loading={busy === 'remove'} disabled={busy !== null} aria-label={`Remove ${active.original_filename}`}>
                <Trash2 size={16} aria-hidden="true" /> Remove
              </Button>
            )}
          </div>
        </div>
      ) : (
        <p className="slot__empty">{editable ? 'No file uploaded yet.' : 'No file was provided.'}</p>
      )}

      {editable && (
        <div className="slot__upload">
          <input ref={input} type="file" className="sr-only" tabIndex={-1} accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg" aria-label={`Choose a file for ${item.label}`} aria-describedby={error ? errId : undefined} onChange={(e) => pick(e.target.files?.[0])} />
          <Button size="sm" variant="secondary" loading={busy === 'upload'} disabled={busy !== null} onClick={() => input.current?.click()}>
            <Upload size={16} aria-hidden="true" /> {active ? 'Upload a replacement' : 'Choose file'}
          </Button>
          <span className="muted">PDF, PNG or JPEG. Checked again by the server. A replacement is kept as a new version; the earlier file is never overwritten.</span>
        </div>
      )}
      {notice && (
        <p className="note" role="status">
          {notice}
        </p>
      )}
      {earlier.length > 0 && (
        <details className="slot__earlier">
          <summary>
            {earlier.length} earlier version{earlier.length > 1 ? 's' : ''} kept on record
          </summary>
          <ul className="slot__versions">
            {earlier.map((d) => (
              <li key={d.id}>
                <span>
                  Version {d.version_number}: {d.original_filename}
                </span>
                <span className="mono muted"> · {formatDate(d.uploaded_at)} · replaced by a newer version · </span>
                <a className="link" href={evidenceApi.downloadUrl(d.id)}>
                  Download
                </a>
              </li>
            ))}
          </ul>
        </details>
      )}
      {error && (
        <p className="field__error" id={errId} role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
