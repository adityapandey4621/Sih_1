import { useCallback, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Download, Upload } from 'lucide-react'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Alert, Badge, EmptyState, ErrorState, LoadingState } from '../../components/ui/feedback'
import { DemoBanner } from '../../components/ui/Banners'
import { TextAreaField } from '../../components/ui/Field'
import { ApiError } from '../../lib/api'
import { formatDate } from '../../lib/format'
import { useResource } from '../../lib/useResource'
import { ResourceView } from '../admin/shared'
import { Section } from '../applications/parts'
import { PageHeader } from '../shell/PageHeader'
import '../applications/applications.css'
import '../scrutiny/scrutiny.css'
import './committee.css'
import { complianceApi, newKey } from './committeeApi'
import { MODE_WORDS, OBSERVATION_WORDS, localTime } from './parts'
import type { ComplianceRound, InstitutionSchedule } from './types'

function ScheduleLine({ s, what }: { s: InstitutionSchedule; what: string }) {
  return (
    <div className="schedule-row">
      <Badge tone={s.status === 'CONFIRMED' ? 'success' : s.status === 'PROPOSED' ? 'warning' : 'neutral'}>{s.status === 'CONFIRMED' ? 'Confirmed' : s.status === 'PROPOSED' ? 'Proposed — not yet confirmed' : s.status === 'COMPLETED' ? 'Took place' : s.status}</Badge>{' '}
      <strong>{localTime(s.start_local, s.timezone)}</strong> · {MODE_WORDS[s.mode] ?? s.mode}
      {s.location && <div>Place: {s.location}</div>}
      {s.access_details && <div>How to join: {s.access_details}</div>}
      <p className="note">This is the {what} schedule recorded for your application. If it changes, you are told here.</p>
    </div>
  )
}

/** The institution's view of the EVC / SHC stages of ONE application: schedules and what is asked of it, never internal notes or members. */
export function CommitteeStatusPanel({ applicationId, refreshKey = 0 }: { applicationId: string; refreshKey?: number }) {
  const [res, reload] = useResource(() => complianceApi.status(applicationId), [applicationId, refreshKey])
  return (
    <ResourceView resource={res} reload={reload}>
      {(s) => {
        if (!s.evc && !s.shc && !s.ready_for_decision_stage) return null
        return (
          <Section title="Expert visit and committee stages">
            {s.evc && (
              <>
                <h3 className="panel__sub">Expert visit (EVC)</h3>
                <p>
                  <strong>{s.evc.label}</strong>
                </p>
                {s.evc.schedule && <ScheduleLine s={s.evc.schedule} what="visit" />}
                {s.evc.report && (
                  <div className="response-block">
                    <p>
                      <strong>Visit report (version {s.evc.report.version_number})</strong> {s.evc.report.demo && <span className="demo-tag">DEMO ONLY</span>}
                    </p>
                    {s.evc.report.sections.map((x) => (
                      <div key={x.key}>
                        <p className="eyebrow">{x.label}</p>
                        <blockquote>{x.text}</blockquote>
                      </div>
                    ))}
                    {s.evc.report.observations.length > 0 && (
                      <ul className="plain-list">
                        {s.evc.report.observations.map((o) => (
                          <li key={o.item_key}>
                            <strong>{o.label}:</strong> {OBSERVATION_WORDS[o.status] ?? o.status} — {o.note}
                          </li>
                        ))}
                      </ul>
                    )}
                    <p className="note">The report is a record of the visit. It is not an approval or a decision.</p>
                  </div>
                )}
              </>
            )}
            {s.shc && (
              <>
                <h3 className="panel__sub">Standing Hearing Committee (SHC)</h3>
                <p>
                  <strong>{s.shc.label}</strong>
                </p>
                {s.shc.rounds.map((r) => (
                  <p key={r.id}>
                    <Link className="link" to={`/app/compliance/${r.id}`}>
                      Compliance request {r.number}
                    </Link>{' '}
                    — {r.status === 'OPEN' ? 'a response is needed' : r.status === 'RESPONSE_RECEIVED' ? 'your response was received' : 'closed'}
                  </p>
                ))}
                {s.shc.hearing && <ScheduleLine s={s.shc.hearing} what="hearing" />}
              </>
            )}
            {s.ready_for_decision_stage && <Alert tone="info" title="Ready for the decision stage">Every stage the approved route required is complete. The decision itself is made later by the competent authority; nothing has been decided.</Alert>}
            <p className="note">{s.not_a_decision}</p>
          </Section>
        )
      }}
    </ResourceView>
  )
}

// ---- compliance list and response ------------------------------------------------------------------------------

export function ComplianceListPage() {
  const [res, reload] = useResource(() => complianceApi.list(), [])
  return (
    <>
      <PageHeader title="Compliance requests" description="Points the Standing Hearing Committee process asks your institution to address." crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Compliance' }]} />
      <ResourceView resource={res} reload={reload}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title="No compliance requests">If the committee process needs something from your institution, a request appears here. Nothing is needed from you now.</EmptyState>
          ) : (
            <>
              {items.some((i) => i.can_respond) && <Alert tone="warning" title="A response is needed">One or more requests are waiting for your response.</Alert>}
              <div className="table-wrap">
                <table className="table">
                  <caption className="sr-only">Compliance requests</caption>
                  <thead>
                    <tr>
                      <th scope="col">Request</th>
                      <th scope="col">Application</th>
                      <th scope="col">Requested</th>
                      <th scope="col">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((i) => (
                      <tr key={i.id}>
                        <td>
                          <Link className="mono link" to={`/app/compliance/${i.id}`}>
                            Round {i.number}
                          </Link>
                        </td>
                        <td className="mono">{i.application.reference}</td>
                        <td className="mono nowrap">{formatDate(i.requested_at)}</td>
                        <td>
                          <Badge tone={i.can_respond ? 'warning' : 'info'}>{i.can_respond ? 'Response needed' : i.status === 'RESPONSE_RECEIVED' ? 'Response received' : 'Closed'}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )
        }
      </ResourceView>
    </>
  )
}

function RoundView({ initial }: { initial: ComplianceRound }) {
  const [round, setRound] = useState(initial)
  const [drafts, setDrafts] = useState<Record<string, string>>(() => Object.fromEntries(initial.requirements.map((r) => [r.id, r.explanation])))
  const [general, setGeneral] = useState(initial.response?.explanation ?? '')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [uploading, setUploading] = useState<string | null>(null)
  const [confirm, setConfirm] = useState(false)
  const key = useRef(newKey()) // one identifier per visit: a repeated submit is recognised by the server
  const refs = useRef<Record<string, HTMLInputElement | null>>({})
  const fail = (e: unknown) => setError(e instanceof ApiError ? e.message : 'That did not go through. Nothing was changed.')

  const save = useCallback(async () => {
    const saved = await complianceApi.save(round.id, { explanation: general, items: Object.entries(drafts).map(([requirement_id, explanation]) => ({ requirement_id, explanation })), row_version: round.response?.row_version ?? null })
    setRound(saved)
    return saved
  }, [round.id, round.response?.row_version, general, drafts])

  async function onSave() {
    setBusy(true)
    setError(null)
    try {
      await save()
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }
  async function onUpload(reqId: string, file: File) {
    setUploading(reqId)
    setError(null)
    try {
      setRound(await complianceApi.upload(round.id, reqId, file))
    } catch (e) {
      fail(e)
    } finally {
      setUploading(null)
    }
  }
  async function onSubmit() {
    setBusy(true)
    setError(null)
    try {
      const saved = await save()
      setRound(await complianceApi.submit(round.id, key.current, saved.response?.row_version ?? 0))
      setConfirm(false)
    } catch (e) {
      fail(e)
      setConfirm(false)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader title={`Compliance request ${round.number}`} description={`Application ${round.application.reference}`} crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Compliance', to: '/app/compliance' }, { label: `Request ${round.number}` }]} />
      {round.demo_notice && <DemoBanner text={round.demo_notice} />}
      <Alert tone={round.can_respond ? 'warning' : 'info'} title={round.can_respond ? 'A response is needed' : round.response?.status === 'SUBMITTED' ? 'Response received' : 'This request is closed'}>
        {round.due_note ?? ''} {round.received_note ?? ''}
      </Alert>
      <Section title="What the committee asks">
        <blockquote>{round.instructions}</blockquote>
        <p className="mono muted">Requested {formatDate(round.requested_at)}</p>
      </Section>
      {error && <Alert tone="error" title="That did not go through">{error}</Alert>}
      <ol className="notice-items notice-items--cards">
        {round.requirements.map((r) => {
          const editable = round.can_respond
          return (
            <li key={r.id} className="notice-item">
              <h3 className="panel__sub">
                {r.position}. {r.title}
              </h3>
              <p>{r.description}</p>
              {r.requested_evidence && (
                <p>
                  <strong>Evidence that would answer it:</strong> {r.requested_evidence}
                </p>
              )}
              {r.documents.length > 0 && (
                <ul className="plain-list">
                  {r.documents.map((d) =>
                    d ? (
                      <li key={d.id}>
                        <span className="mono">
                          {d.filename} · version {d.version_number}
                        </span>{' '}
                        <a className="link" href={complianceApi.downloadUrl(d.id)}>
                          <Download size={14} aria-hidden="true" /> Download
                        </a>
                      </li>
                    ) : null,
                  )}
                </ul>
              )}
              {editable ? (
                <>
                  <TextAreaField label={`Your response to point ${r.position}`} rows={3} value={drafts[r.id] ?? ''} onChange={(e) => setDrafts((d) => ({ ...d, [r.id]: e.target.value }))} hint="It is read by the committee; submitting it is not an acceptance." />
                  {r.item_key ? (
                    <div className="inline-actions">
                      <input ref={(el) => { refs.current[r.id] = el }} type="file" className="sr-only" accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg" aria-label={`Choose a file for point ${r.position}`} onChange={(e) => { const f = e.target.files?.[0]; if (f) void onUpload(r.id, f); e.target.value = '' }} />
                      <Button variant="secondary" size="sm" loading={uploading === r.id} onClick={() => refs.current[r.id]?.click()}>
                        <Upload size={16} aria-hidden="true" /> {r.documents.length ? 'Upload another file' : 'Upload a file'}
                      </Button>
                      <span className="muted">PDF, PNG or JPEG. Earlier versions are kept; this adds a new version.</span>
                    </div>
                  ) : (
                    <p className="note">This point takes no file; answer it in writing.</p>
                  )}
                </>
              ) : (
                r.explanation && <blockquote>{r.explanation}</blockquote>
              )}
            </li>
          )
        })}
      </ol>
      {round.can_respond && (
        <Section title="Your response">
          <TextAreaField label="Anything else you want the committee to know" optional rows={3} value={general} onChange={(e) => setGeneral(e.target.value)} />
          <div className="action-bar">
            <Button variant="secondary" loading={busy} onClick={() => void onSave()}>
              Save draft
            </Button>
            <Button loading={busy} onClick={() => setConfirm(true)}>
              Submit response…
            </Button>
          </div>
        </Section>
      )}
      <ConfirmDialog open={confirm} title="Submit your response" confirmLabel="Submit response" busy={busy} onCancel={() => setConfirm(false)} onConfirm={() => void onSubmit()}>
        <p>Once submitted, your response cannot be changed. It goes to the committee for review; it is received, not accepted.</p>
      </ConfirmDialog>
    </>
  )
}

export default function CompliancePage() {
  const { id = '' } = useParams()
  const [res, reload] = useResource(() => complianceApi.get(id), [id])
  if (res.status === 'loading') return <LoadingState label="Loading the request…" />
  if (res.status === 'error') return res.forbidden ? <EmptyState title="Not found">This request does not exist or is not addressed to your institution.</EmptyState> : <ErrorState message={res.message} onRetry={reload} />
  return <RoundView key={res.data.id} initial={res.data} />
}
