import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Download, Upload } from 'lucide-react'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Alert, Badge, EmptyState, ErrorState, LoadingState, type Tone } from '../../components/ui/feedback'
import { DemoBanner } from '../../components/ui/Banners'
import { TextAreaField } from '../../components/ui/Field'
import { ApiError } from '../../lib/api'
import { formatDate } from '../../lib/format'
import { Section } from '../applications/parts'
import { PageHeader } from '../shell/PageHeader'
import '../applications/applications.css'
import { Fact, Facts, StageTag } from './parts'
import { noticesApi } from './scrutinyApi'
import type { InstItem, InstNotice } from './types'
import './scrutiny.css'

const ITEM_STATE: Record<InstItem['response_state'], { tone: Tone; label: string }> = {
  NOT_RESPONDED: { tone: 'warning', label: 'Not responded' },
  DRAFT: { tone: 'info', label: 'Draft response — not submitted' },
  SUBMITTED: { tone: 'info', label: 'Submitted — awaiting re-scrutiny' },
  REVIEWED: { tone: 'neutral', label: 'Re-scrutiny recorded' },
}

const newKey = () => (typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `k-${Date.now()}-${Math.random().toString(36).slice(2)}`)

function ItemCard({ item, editable, text, onText, onUpload, uploading }: {
  item: InstItem
  editable: boolean
  text: string
  onText: (v: string) => void
  onUpload: (file: File) => void
  uploading: boolean
}) {
  const st = ITEM_STATE[item.response_state]
  const ref = useRef<HTMLInputElement>(null)
  return (
    <li className="notice-item" data-state={item.response_state}>
      <div className="finding__head">
        <h3 className="panel__sub">
          {item.position}. {item.title}
        </h3>
        <Badge tone={st.tone}>{st.label}</Badge>
      </div>
      <p>{item.description}</p>
      <p>
        <strong>What is requested:</strong> {item.requested_correction}
      </p>
      <p className="checklist__meta">
        {item.item_label ? `Concerns: ${item.item_label}` : 'Concerns the application generally'}
        {item.document_version ? (
          <span className="mono">
            {' '}
            · the version you submitted earlier (version {item.document_version}
            {item.page_number ? `, page ${item.page_number}` : ''})
            {item.document_current_version && item.document_current_version !== item.document_version ? `; the current version is now ${item.document_current_version}` : ''}
          </span>
        ) : null}
      </p>
      <p className="checklist__meta">
        <strong>Source:</strong> {item.source_label ?? '—'} <span className="muted">{item.source_note}</span>
      </p>

      {item.response_documents.length > 0 && (
        <div>
          <p className="eyebrow">Files you supplied for this item</p>
          <ul className="plain-list">
            {item.response_documents.map((d) => (
              <li key={d.document_id}>
                <span className="mono">
                  {d.filename} · version {d.version_number}
                </span>{' '}
                {d.current ? '' : '(since replaced) '}— {d.state_label}{' '}
                <a className="link" href={noticesApi.downloadUrl(d.document_id)}>
                  <Download size={14} aria-hidden="true" /> Download
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}

      {editable ? (
        <>
          <TextAreaField label={`Your response to item ${item.position}`} rows={3} value={text} onChange={(e) => onText(e.target.value)} hint="Explain what you are supplying or correcting. This is read by the reviewer; it is not an acceptance." />
          {item.accepts_files ? (
            <div className="inline-actions">
              <input
                ref={ref}
                type="file"
                className="sr-only"
                accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
                aria-label={`Choose a corrected file for item ${item.position}`}
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) onUpload(f)
                  e.target.value = ''
                }}
              />
              <Button variant="secondary" size="sm" loading={uploading} onClick={() => ref.current?.click()}>
                <Upload size={16} aria-hidden="true" /> {item.response_documents.length ? 'Upload another corrected file' : 'Upload a corrected file'}
              </Button>
              <span className="muted">PDF, PNG or JPEG. Your earlier versions are kept; this adds a new version.</span>
            </div>
          ) : (
            <p className="note">This item has no file to upload; answer it in writing above.</p>
          )}
        </>
      ) : (
        item.response_explanation && (
          <div className="response-block">
            <StageTag kind="response" />
            <blockquote>{item.response_explanation}</blockquote>
          </div>
        )
      )}
    </li>
  )
}

function NoticeView({ initial }: { initial: InstNotice }) {
  const [notice, setNotice] = useState(initial)
  const [drafts, setDrafts] = useState<Record<string, string>>(() => Object.fromEntries(initial.items.map((i) => [i.id, i.response_explanation])))
  const [general, setGeneral] = useState(initial.response?.explanation ?? '')
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [uploading, setUploading] = useState<string | null>(null)
  const [confirm, setConfirm] = useState(false)
  // One identifier per page visit: a repeated submit (double click, retry after a dropped connection) is recognised by the server.
  const key = useRef(newKey())

  const fail = (e: unknown) => setError(e instanceof ApiError ? e.message : 'That did not go through. Nothing was changed.')

  const save = useCallback(async (): Promise<InstNotice> => {
    const saved = await noticesApi.saveDraft(notice.id, { explanation: general, items: Object.entries(drafts).map(([notice_item_id, explanation]) => ({ notice_item_id, explanation })), row_version: notice.response?.row_version ?? null })
    setNotice(saved)
    return saved
  }, [notice.id, notice.response?.row_version, general, drafts])

  async function saveDraft() {
    setBusy(true)
    setError(null)
    try {
      await save()
      setInfo('Draft saved. It has not been submitted.')
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }
  async function submit() {
    setBusy(true)
    setError(null)
    try {
      const saved = await save()
      const done = await noticesApi.submit(notice.id, key.current, saved.response!.row_version)
      setNotice(done)
      setConfirm(false)
      setInfo(null)
      window.scrollTo(0, 0)
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }
  async function upload(item: InstItem, file: File) {
    setUploading(item.id)
    setError(null)
    try {
      setNotice(await noticesApi.upload(notice.id, item.id, file))
      setInfo(`“${file.name}” was added to item ${item.position} as a new version.`)
    } catch (e) {
      fail(e)
    } finally {
      setUploading(null)
    }
  }

  const editable = notice.can_respond
  const submitted = notice.response?.status === 'SUBMITTED'
  const nothingYet = !general.trim() && notice.items.every((i) => !(drafts[i.id] ?? '').trim() && i.response_documents.length === 0)

  return (
    <>
      <PageHeader
        title={`Deficiency notice ${notice.reference}`}
        description={
          <>
            For application <Link className="mono link" to={`/app/applications/${notice.application_id}`}>{notice.application_reference}</Link>
          </>
        }
        crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Notices', to: '/app/notices' }, { label: notice.reference }]}
        actions={<Badge tone={notice.status === 'ISSUED' ? 'warning' : 'info'}>{notice.status_label}</Badge>}
      />
      {notice.demo_notice && <DemoBanner text={notice.demo_notice} />}
      <div className="case-banner">
        <StageTag kind="notice" />
        <p className="note">{notice.not_an_approval}</p>
      </div>
      {submitted && (
        <Alert tone="success" title="Your response was submitted">
          Received {formatDate(notice.response?.submitted_at)}. It has <strong>not</strong> been accepted: re-scrutiny will examine it. You will be told if anything further is needed.
        </Alert>
      )}
      {notice.status === 'ISSUED' && !submitted && (
        <Alert tone="warning" title="Your response is needed">
          {notice.response_due_at ? <>Respond by <strong>{formatDate(notice.response_due_at)}</strong>. </> : <>{notice.due_note} </>}
          Answer each item, add corrected files where asked, then submit. You can save a draft and return.
        </Alert>
      )}
      <Section title="The notice">
        <Facts>
          <Fact label="Reference">
            <span className="mono">{notice.reference}</span> · notice no. {notice.sequence}
            {notice.previous_reference ? ` (follows ${notice.previous_reference})` : ''}
          </Fact>
          <Fact label="Issued">{formatDate(notice.issued_at)}</Fact>
          <Fact label="Response deadline">{notice.response_due_at ? <span className="mono">{formatDate(notice.response_due_at)}</span> : notice.due_note}</Fact>
          {notice.template_label && <Fact label="Template">{notice.template_label}</Fact>}
        </Facts>
        {notice.response_due_at && <p className="note">{notice.due_note}</p>}
        <p>{notice.summary}</p>
      </Section>

      {error && <Alert tone="error">{error}</Alert>}
      {info && !error && <Alert tone="success">{info}</Alert>}

      <Section title={`Items (${notice.items.length})`}>
        <ol className="notice-items notice-items--cards">
          {notice.items.map((i) => (
            <ItemCard key={i.id} item={i} editable={editable} text={drafts[i.id] ?? ''} onText={(v) => setDrafts((d) => ({ ...d, [i.id]: v }))} onUpload={(f) => void upload(i, f)} uploading={uploading === i.id} />
          ))}
        </ol>
      </Section>

      {editable && (
        <Section title="Your response">
          <TextAreaField label="General note (optional)" optional rows={3} value={general} onChange={(e) => setGeneral(e.target.value)} />
          <div className="action-bar">
            <Button variant="secondary" loading={busy} onClick={() => void saveDraft()}>
              Save draft
            </Button>
            <Button disabled={nothingYet || busy} onClick={() => setConfirm(true)}>
              Submit response…
            </Button>
            {nothingYet && <span className="muted">Answer at least one item, or upload a corrected file, to submit.</span>}
          </div>
        </Section>
      )}
      {!editable && notice.response?.explanation && (
        <Section title="Your general note">
          <blockquote>{notice.response.explanation}</blockquote>
        </Section>
      )}

      <ConfirmDialog open={confirm} title="Submit your response?" confirmLabel="Submit response" busy={busy} onCancel={() => setConfirm(false)} onConfirm={() => void submit()}>
        <p>
          Once submitted, this response cannot be changed, and the notice will no longer take new files. Your response is then <strong>received for re-scrutiny — not accepted</strong>.
        </p>
        <p className="note">Items you have not answered stay marked as not responded.</p>
        {error && <Alert tone="error">{error}</Alert>}
      </ConfirmDialog>
    </>
  )
}

export default function NoticePage() {
  const { id = '' } = useParams()
  const [notice, setNotice] = useState<InstNotice | null>(null)
  const [error, setError] = useState<{ message: string; missing: boolean } | null>(null)
  const load = useCallback(() => {
    noticesApi.get(id).then((n) => { setNotice(n); setError(null) }).catch((e) => setError({ message: e instanceof ApiError ? e.message : 'The notice could not be loaded.', missing: e instanceof ApiError && e.status === 404 }))
  }, [id])
  useEffect(() => load(), [load])
  if (error && !notice) return error.missing ? <EmptyState title="Notice not found">It may not exist, or it is not addressed to your institution.</EmptyState> : <ErrorState message={error.message} onRetry={load} />
  if (!notice) return <LoadingState label="Loading the notice…" />
  return <NoticeView key={notice.id} initial={notice} />
}
