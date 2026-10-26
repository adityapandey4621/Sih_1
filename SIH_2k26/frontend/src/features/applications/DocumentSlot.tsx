import { useId, useRef, useState } from 'react'
import { Download, FileText, Trash2, Upload } from 'lucide-react'
import { ApiError } from '../../lib/api'
import { formatBytes, formatDate } from '../../lib/format'
import { Badge } from '../../components/ui/feedback'
import { Button } from '../../components/ui/Button'
import { applicationsApi } from './applicationsApi'
import type { ApplicationDetail, ChecklistState, DocumentRecord } from './types'

interface Props {
  applicationId: string
  item: ChecklistState
  documents: DocumentRecord[]
  editable: boolean
  onUpdated: (d: ApplicationDetail) => void
}

/** One upload slot tied to a checklist item. Upload state is a technical fact, never a regulatory finding. */
export function DocumentSlot({ applicationId, item, documents, editable, onUpdated }: Props) {
  const input = useRef<HTMLInputElement>(null)
  const errId = useId()
  const [busy, setBusy] = useState<'upload' | 'remove' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const active = documents.find((d) => d.item_key === item.item_key && !d.removed)
  const earlier = documents.filter((d) => d.item_key === item.item_key && d.removed).length

  async function pick(file: File | undefined) {
    if (!file) return
    setBusy('upload')
    setError(null)
    try {
      onUpdated(await applicationsApi.upload(applicationId, item.item_key, file))
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
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'The file could not be removed.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="slot" data-state={active ? 'filled' : 'empty'}>
      <div className="slot__head">
        <span className="slot__label">{item.label}</span>
        <Badge tone={item.required ? 'info' : 'neutral'}>{item.required ? 'Required' : 'Optional'}</Badge>
      </div>
      {item.explanation && <p className="note">{item.explanation}</p>}
      {item.clause_ref && <p className="checklist__meta mono">{item.clause_ref}</p>}

      {active ? (
        <div className="slot__file">
          <FileText size={20} aria-hidden="true" />
          <div className="slot__fileinfo">
            <span className="slot__filename">{active.original_filename}</span>
            <span className="mono muted">
              {formatBytes(active.size_bytes)} · {active.media_type} · uploaded {formatDate(active.uploaded_at)}
            </span>
            <span className="slot__state">
              <Badge tone="success">Uploaded</Badge> <Badge tone="neutral">Contents not verified</Badge>
            </span>
          </div>
          <div className="slot__actions">
            <a className="btn btn--quiet btn--sm" href={applicationsApi.downloadUrl(applicationId, active.id)}>
              <Download size={16} aria-hidden="true" /> Download
            </a>
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
            <Upload size={16} aria-hidden="true" /> {active ? 'Replace file' : 'Choose file'}
          </Button>
          <span className="muted">PDF, PNG or JPEG. Checked again by the server.</span>
        </div>
      )}
      {earlier > 0 && <p className="muted">{earlier} earlier file{earlier > 1 ? 's' : ''} for this item kept on record.</p>}
      {error && (
        <p className="field__error" id={errId} role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
