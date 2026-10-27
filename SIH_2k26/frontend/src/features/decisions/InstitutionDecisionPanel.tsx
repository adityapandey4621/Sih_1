import { useRef, useState } from 'react'
import { Download } from 'lucide-react'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Alert, Badge } from '../../components/ui/feedback'
import { TextAreaField, TextField } from '../../components/ui/Field'
import { ApiError } from '../../lib/api'
import { formatDate, humanise } from '../../lib/format'
import { useResource } from '../../lib/useResource'
import { ResourceView } from '../admin/shared'
import { Section } from '../applications/parts'
import { newKey } from '../committee/committeeApi'
import { institutionDecisionApi } from './decisionsApi'
import { SyncNote } from './DecisionPanels'
import type { InstitutionDecision } from './types'

const fileSize = (n: number | null) => (n === null ? '' : n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`)

type Appeal = NonNullable<InstitutionDecision['appeal']>

function AppealSection({ applicationId, appeal, onChange }: { applicationId: string; appeal: Appeal; onChange: () => void }) {
  const e = appeal.eligibility
  const sub = appeal.submitted
  const [grounds, setGrounds] = useState('')
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<ApiError | null>(null)
  const key = useRef(newKey())
  const [link, setLink] = useState({ url: '', description: '' })
  const [desc, setDesc] = useState('')
  const guard = async (fn: () => Promise<InstitutionDecision>): Promise<boolean> => {
    setBusy(true)
    try {
      await fn()
      onChange()
      setErr(null)
      return true
    } catch (x) {
      setErr(x instanceof ApiError ? x : new ApiError(0, 'Something unexpected went wrong. Nothing was changed.'))
      return false
    } finally {
      setBusy(false)
    }
  }
  return (
    <Section title="Appeal">
      {sub ? (
        <>
          <p>
            <Badge tone="info">{sub.status_label}</Badge> submitted {formatDate(sub.submitted_at)}
          </p>
          <p className="eyebrow">Your grounds</p>
          <blockquote>{sub.grounds}</blockquote>
          <p className="note">{sub.note}</p>
          <h3 className="panel__sub">Supporting documents ({sub.documents.length})</h3>
          {sub.documents.length === 0 ? (
            <p className="note">None attached.</p>
          ) : (
            <ul className="plain-list">
              {sub.documents.map((d) => (
                <li key={d.id}>
                  {d.kind === 'FILE' && d.download ? (
                    <a className="link" href={d.download}>
                      <Download size={14} aria-hidden="true" /> {d.filename}
                    </a>
                  ) : (
                    <a className="link" href={d.url ?? '#'} rel="noreferrer noopener" target="_blank">
                      {d.url}
                    </a>
                  )}{' '}
                  — {d.description} <span className="muted">({d.kind === 'FILE' ? fileSize(d.size_bytes) : 'link'})</span>
                </li>
              ))}
            </ul>
          )}
          {sub.can_add_documents && (
            <div className="panel">
              <div className="field">
                <label className="field__label" htmlFor="appeal-file">
                  Add a PDF
                </label>
                <input
                  id="appeal-file"
                  type="file"
                  accept="application/pdf"
                  className="field__control"
                  onChange={(ev) => {
                    const f = ev.target.files?.[0]
                    if (f) void guard(() => institutionDecisionApi.addFile(sub.id, f, desc.trim() || f.name))
                    ev.target.value = ''
                  }}
                />
              </div>
              <TextField label="Describe the file you add" optional value={desc} onChange={(ev) => setDesc(ev.target.value)} />
              <TextField label="Or add a link" optional value={link.url} onChange={(ev) => setLink((l) => ({ ...l, url: ev.target.value }))} />
              <TextField label="What the link shows" optional value={link.description} onChange={(ev) => setLink((l) => ({ ...l, description: ev.target.value }))} />
              <Button
                variant="secondary"
                loading={busy}
                disabled={!link.url.trim() || !link.description.trim()}
                onClick={() => void guard(() => institutionDecisionApi.addLink(sub.id, link.url.trim(), link.description.trim())).then((ok) => ok && setLink({ url: '', description: '' }))}
              >
                Add the link
              </Button>
            </div>
          )}
          {sub.council && (
            <Alert tone="success" title={`The Council decided: ${sub.council.outcome_label}`}>
              Recorded {formatDate(sub.council.recorded_at)}
              {sub.council.reference ? ` · ${sub.council.reference}` : ''}. Under the handbook the Council’s decision on an appeal is final.
              <blockquote>{sub.council.reasons}</blockquote>
              {sub.council.speaking_order && <blockquote>{sub.council.speaking_order}</blockquote>}
              <SyncNote sync={sub.council.sync} what="the Council’s decision" />
            </Alert>
          )}
        </>
      ) : (
        <>
          <Alert tone={e.state === 'ELIGIBLE' ? 'info' : 'warning'} title={e.state === 'ELIGIBLE' ? 'You may appeal' : 'Appeal'}>
            {e.message}
            {e.clause_ref ? ` (${e.clause_ref})` : ''}
            {e.deadline_at && e.state === 'ELIGIBLE' && (
              <>
                {' '}
                The period ends <strong>{formatDate(e.deadline_at)}</strong>.
              </>
            )}
          </Alert>
          {appeal.window?.note && <p className="note">{appeal.window.note}</p>}
          {e.state === 'ELIGIBLE' && (
            <>
            <form
              aria-label="Submit an appeal"
              onSubmit={(ev) => {
                ev.preventDefault()
                setConfirm(true)
              }}
            >
              <TextAreaField label="Grounds of the appeal" value={grounds} onChange={(ev) => setGrounds(ev.target.value)} rows={6} hint="State why you are aggrieved by the decision. You can add supporting documents after submitting. Submitting does not promise any outcome." />
              {err && <Alert tone="error">{err.message}</Alert>}
              <Button type="submit" loading={busy} disabled={grounds.trim().length < 20}>
                Submit the appeal…
              </Button>
            </form>
              <ConfirmDialog
                open={confirm}
                title="Submit the appeal"
                confirmLabel="Submit"
                busy={busy}
                onCancel={() => setConfirm(false)}
                onConfirm={async () => {
                  const ok = await guard(() => institutionDecisionApi.appeal(applicationId, grounds.trim(), key.current))
                  if (ok) key.current = newKey()
                  setConfirm(false)
                }}
              >
                <p>An appeal can be made once. It goes to the Standing Appellate Committee for a recommendation and then to the Council, which decides. The result is not predicted or promised.</p>
              </ConfirmDialog>
            </>
          )}
        </>
      )}
      {sub && err && <Alert tone="error">{err.message}</Alert>}
    </Section>
  )
}

/** The institution's view of the decision stage of ONE application. Shows only what was officially issued and recorded, never internal notes. */
export function DecisionStatusPanel({ applicationId, refreshKey = 0 }: { applicationId: string; refreshKey?: number }) {
  const [res, reload] = useResource(() => institutionDecisionApi.status(applicationId), [applicationId, refreshKey])
  return (
    <ResourceView resource={res} reload={reload}>
      {(s) => {
        if (s.official_status.state === 'NO_DECISION_CASE') return null
        const d = s.decision
        return (
          <Section title="Decision and appeal">
            <p>
              <strong>{s.official_status.label}</strong>
            </p>
            {s.correction_pending && <Alert tone="warning">{s.correction_pending}</Alert>}
            {d && (
              <div className="response-block">
                <p>
                  <Badge tone="info">{d.outcome_label}</Badge> issued {formatDate(d.issued_at)}
                  {d.document_reference && (
                    <>
                      {' '}
                      · reference <span className="mono">{d.document_reference}</span>
                    </>
                  )}
                  {d.corrected && <Badge tone="warning"> Corrected (version {d.version_number})</Badge>}
                </p>
                {d.corrected && d.correction_reason && <p className="note">Why it was corrected: {d.correction_reason}</p>}
                <p className="eyebrow">Reasons</p>
                <blockquote>{d.reasons}</blockquote>
                {d.speaking_order && (
                  <>
                    <p className="eyebrow">Speaking order</p>
                    <blockquote>{d.speaking_order}</blockquote>
                  </>
                )}
                {d.file && (
                  <p>
                    <a className="link" href={d.file.download}>
                      <Download size={14} aria-hidden="true" /> {d.file.filename}
                    </a>{' '}
                    <span className="muted">({fileSize(d.file.size_bytes)})</span>
                  </p>
                )}
                {d.ratification.required && (
                  <p className="note">
                    Council step: {d.ratification.recorded ? `${humanise(d.ratification.outcome ?? '')} (${formatDate(d.ratification.recorded_at ?? '')})` : 'not yet recorded'}
                    {d.ratification.clause_ref ? ` · ${d.ratification.clause_ref}` : ''}
                  </p>
                )}
                <SyncNote sync={d.sync} what="the issued outcome" />
              </div>
            )}
            {s.appeal && <AppealSection applicationId={applicationId} appeal={s.appeal} onChange={reload} />}
            <p className="note">{s.not_a_decision}</p>
          </Section>
        )
      }}
    </ResourceView>
  )
}
