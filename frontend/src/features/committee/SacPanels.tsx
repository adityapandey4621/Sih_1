import { useEffect, useState } from 'react'
import { Download } from 'lucide-react'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Alert, Badge } from '../../components/ui/feedback'
import { SelectField, TextAreaField, TextField } from '../../components/ui/Field'
import { formatDate } from '../../lib/format'
import { Section } from '../applications/parts'
import { Fact, Facts } from '../scrutiny/parts'
import { committeeApi, newKey } from './committeeApi'
import { AmendBox } from './EvcPanels'
import { errorNote, type PanelCtx } from './SharedPanels'

export const OUTCOME_WORDS: Record<string, string> = { LOA: 'Letter of Approval (LoA)', LOD: 'Letter of Deficiency (LoD)', LOR: 'Letter of Rejection (LoR)' }

/** The appeal itself: what is appealed against, the institution's grounds, its evidence and the eligibility finding that admitted it. */
export function AppealPanel({ ctx }: { ctx: PanelCtx }) {
  const sac = ctx.detail.sac
  if (!sac) return null
  const { appeal, against_decision: dec } = sac
  return (
    <>
      <Section title="The decision under appeal">
        <Alert tone="info">An appeal is against the Executive Committee’s decision, not against an EVC or SHC record in isolation. The SAC recommends; the Council decides.</Alert>
        <Facts>
          <Fact label="Outcome">{OUTCOME_WORDS[dec.outcome] ?? dec.outcome}</Fact>
          <Fact label="Decision version">{dec.version}</Fact>
          <Fact label="Decided">{formatDate(dec.decided_at)}</Fact>
        </Facts>
        <p className="eyebrow">Reasons recorded</p>
        <blockquote>{dec.reasons}</blockquote>
        {dec.speaking_order && (
          <>
            <p className="eyebrow">Speaking order</p>
            <blockquote>{dec.speaking_order}</blockquote>
          </>
        )}
      </Section>
      <Section title="The appeal">
        <Facts>
          <Fact label="Submitted">
            {formatDate(appeal.submitted_at)} by {appeal.submitted_by}
          </Fact>
          <Fact label="Why it was admitted">
            {appeal.eligibility.message}
            {appeal.eligibility.clause_ref ? ` (${appeal.eligibility.clause_ref})` : ''}
            {appeal.eligibility.deadline_at ? ` — period ran until ${formatDate(appeal.eligibility.deadline_at)}` : ''}
          </Fact>
        </Facts>
        <p className="eyebrow">Grounds</p>
        <blockquote>{appeal.grounds}</blockquote>
        <h3 className="panel__sub">Supporting documents ({sac.documents.length})</h3>
        {sac.documents.length === 0 ? (
          <p className="note">The institution attached nothing.</p>
        ) : (
          <ul className="plain-list">
            {sac.documents.map((d) => (
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
                — {d.description} <span className="muted">· {formatDate(d.uploaded_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </>
  )
}

/** Hearing / sitting records and the SAC's recommendation to the Council. */
export function SacReviewPanel({ ctx }: { ctx: PanelCtx }) {
  const { detail, run, busy, error } = ctx
  const sac = detail.sac
  const pol = detail.policy?.sac
  const draft = sac?.draft ?? null
  const a = detail.allowed_actions
  const active = detail.schedule.active
  const [procOpen, setProcOpen] = useState(false)
  const [proc, setProc] = useState({ held_local: '', timezone: 'Asia/Kolkata', participants: '', text: '', schedule_id: '' })
  const [option, setOption] = useState<string>(draft?.content.option ?? '')
  const [text, setText] = useState<Record<string, string>>(draft?.content.sections ?? {})
  const [key, setKey] = useState(newKey)
  const [confirm, setConfirm] = useState(false)
  useEffect(() => {
    setOption(draft?.content.option ?? '')
    setText(draft?.content.sections ?? {})
  }, [draft?.row_version, draft?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!sac || !pol) return null
  const editable = a.includes('save_recommendation') && draft !== null
  const dirty = JSON.stringify({ option: option || null, text }) !== JSON.stringify({ option: draft?.content.option ?? null, text: draft?.content.sections ?? {} })
  const issues = error?.code === 'recommendation_incomplete' ? error.issues : []
  return (
    <>
      <Section title={`Hearing and sitting records (${sac.proceedings.length})`}>
        <p className="note">Records are append-only. Naming a confirmed hearing records that it took place.</p>
        {sac.proceedings.length === 0 ? (
          <p className="note">Nothing has been recorded.</p>
        ) : (
          <ul className="plain-list">
            {sac.proceedings.map((p) => (
              <li key={p.id}>
                <strong>{formatDate(p.held_at)}</strong> — {p.by}
                {p.participants && <div className="muted">Present: {p.participants}</div>}
                <blockquote>{p.text}</blockquote>
              </li>
            ))}
          </ul>
        )}
        {a.includes('record_proceeding') && !procOpen && (
          <Button variant="secondary" onClick={() => setProcOpen(true)}>
            Record a hearing or sitting…
          </Button>
        )}
        {a.includes('record_proceeding') && procOpen && (
          <form
            className="panel"
            aria-label="Record a hearing or sitting"
            onSubmit={(e) => {
              e.preventDefault()
              void run(() => committeeApi.sacProceeding(detail.id, { ...proc, schedule_id: proc.schedule_id || null })).then((ok) => {
                if (ok) {
                  setProcOpen(false)
                  setProc({ held_local: '', timezone: 'Asia/Kolkata', participants: '', text: '', schedule_id: '' })
                }
              })
            }}
          >
            <TextField label="When it took place (local)" type="datetime-local" value={proc.held_local} onChange={(e) => setProc((p) => ({ ...p, held_local: e.target.value }))} />
            <TextField label="Time zone" value={proc.timezone} onChange={(e) => setProc((p) => ({ ...p, timezone: e.target.value }))} />
            <TextAreaField label="Who took part" optional value={proc.participants} onChange={(e) => setProc((p) => ({ ...p, participants: e.target.value }))} rows={2} />
            <TextAreaField label="What happened" value={proc.text} onChange={(e) => setProc((p) => ({ ...p, text: e.target.value }))} rows={4} />
            {active?.status === 'CONFIRMED' && (
              <SelectField label="This was the confirmed hearing" optional value={proc.schedule_id} onChange={(e) => setProc((p) => ({ ...p, schedule_id: e.target.value }))} options={[{ value: active.id, label: `${active.start_local} (${active.timezone})` }]} placeholder="Not a scheduled hearing" />
            )}
            <div className="action-bar">
              <Button type="submit" size="sm" loading={busy} disabled={!proc.held_local || proc.text.trim().length < 5}>
                Record
              </Button>
              <Button variant="quiet" size="sm" onClick={() => setProcOpen(false)}>
                Close
              </Button>
            </div>
          </form>
        )}
      </Section>
      <Section title="Recommendation to the Council">
        <Alert tone="info">The SAC’s recommendation goes to the Council with the appeal. It is not the Council’s decision, and nothing here approves or rejects an application.</Alert>
        {editable && draft ? (
          <form
            aria-label="Recommendation draft"
            onSubmit={(e) => {
              e.preventDefault()
              void run(() => committeeApi.sacSaveRecommendation(detail.id, option || null, text, draft.row_version))
            }}
          >
            <p className="note">Draft of version {draft.version_number}{draft.amendment_reason ? ` (amendment: ${draft.amendment_reason})` : ''}.</p>
            <SelectField label="Recommendation" value={option} onChange={(e) => setOption(e.target.value)} options={pol.recommendation_options.map((o) => ({ value: o.key, label: o.label }))} />
            {pol.recommendation_sections.map((s) => (
              <TextAreaField key={s.key} label={s.label} optional={!s.required} value={text[s.key] ?? ''} onChange={(e) => setText((t) => ({ ...t, [s.key]: e.target.value }))} rows={4} />
            ))}
            {issues.length > 0 && (
              <Alert tone="warning" title="The recommendation cannot be submitted yet">
                <ul className="plain-list">
                  {issues.map((i) => (
                    <li key={`${i.code}-${i.path}`}>{i.message}</li>
                  ))}
                </ul>
              </Alert>
            )}
            {error && error.code !== 'recommendation_incomplete' && errorNote(error)}
            <div className="action-bar">
              <Button type="submit" variant="secondary" loading={busy} disabled={!dirty}>
                Save draft
              </Button>
              <Button loading={busy} disabled={dirty} onClick={() => setConfirm(true)}>
                Submit the recommendation…
              </Button>
            </div>
          </form>
        ) : draft ? (
          <p className="note">A draft (version {draft.version_number}) is being prepared by the assigned members.</p>
        ) : (
          sac.versions.length === 0 && <p className="note">The recommendation is prepared once members are assigned and the review starts.</p>
        )}
        {sac.versions.length > 0 && (
          <>
            <h3 className="panel__sub">Submitted versions (kept unchanged)</h3>
            <ul className="notice-list">
              {sac.versions.map((v) => (
                <li key={v.id} className="notice-card" data-status="CLOSED">
                  <p>
                    <strong>Version {v.version_number}</strong> · <Badge tone="info">{pol.recommendation_options.find((o) => o.key === v.content.option)?.label ?? v.content.option}</Badge> · submitted {formatDate(v.submitted_at)} by {v.submitted_by}
                    {v.amendment_reason && <span className="note"> · amendment: {v.amendment_reason}</span>}
                  </p>
                  {Object.entries(v.content.sections ?? {}).map(([k, t]) => (
                    <div key={k}>
                      <p className="mono muted">{k}</p>
                      <blockquote>{t}</blockquote>
                    </div>
                  ))}
                  <p className="mono muted">content hash {v.content_hash?.slice(0, 16)}…</p>
                </li>
              ))}
            </ul>
          </>
        )}
        <AmendBox ctx={ctx} />
        <ConfirmDialog
          open={confirm}
          title="Submit the recommendation"
          confirmLabel="Submit"
          busy={busy}
          onCancel={() => setConfirm(false)}
          onConfirm={async () => {
            if (draft && (await run(() => committeeApi.sacSubmitRecommendation(detail.id, key, draft.row_version)))) setKey(newKey())
            setConfirm(false)
          }}
        >
          <p>The submitted version is kept exactly as it is, with a record of the appeal it answers. A correction needs the SAC coordinator to open an amendment, which creates a new version.</p>
        </ConfirmDialog>
      </Section>
    </>
  )
}
