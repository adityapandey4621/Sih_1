import { useEffect, useState } from 'react'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Alert, Badge } from '../../components/ui/feedback'
import { SelectField, TextAreaField, TextField } from '../../components/ui/Field'
import { formatDate, humanise } from '../../lib/format'
import { Section } from '../applications/parts'
import { committeeApi, newKey } from './committeeApi'
import { AmendBox } from './EvcPanels'
import { ASSESSMENT_WORDS } from './parts'
import { errorNote, type PanelCtx } from './SharedPanels'
import type { Requirement, Round, ShcFinding } from './types'

// ---- requirements and compliance rounds ----------------------------------------------------------------------------

function RequirementForm({ ctx, initial, origin, onDone }: { ctx: PanelCtx; initial?: Requirement; origin?: { type: string; id?: string }; onDone: () => void }) {
  const { detail, run, busy } = ctx
  const [title, setTitle] = useState(initial?.title ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [evidence, setEvidence] = useState(initial?.requested_evidence ?? '')
  const [itemKey, setItemKey] = useState(initial?.item_key ?? '')
  const [slots, setSlots] = useState<{ value: string; label: string }[]>([])
  // The application's own document requirements: a corrected file can only be filed under one of them.
  useEffect(() => {
    void committeeApi.evidence('SHC', detail.id).then((ev) => setSlots(ev.items.map((i) => ({ value: i.item_key, label: i.label })))).catch(() => setSlots([]))
  }, [detail.id])
  return (
    <form
      className="panel"
      aria-label={initial ? 'Edit requirement' : 'Add a requirement'}
      onSubmit={(e) => {
        e.preventDefault()
        const go = initial
          ? () => committeeApi.editRequirement(detail.id, initial.id, { title, description, requested_evidence: evidence, row_version: initial.row_version })
          : () => committeeApi.addRequirement(detail.id, { title, description, requested_evidence: evidence, item_key: itemKey || null, origin: origin ?? { type: 'manual' } })
        void run(go).then((ok) => ok && onDone())
      }}
    >
      <Alert tone="info">Write this in your own words for the institution. Internal notes from scrutiny or the visit are not copied into it.</Alert>
      <TextField label="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
      <TextAreaField label="What the institution is asked to address" value={description} onChange={(e) => setDescription(e.target.value)} rows={3} />
      <TextAreaField label="Evidence that would answer it" optional value={evidence} onChange={(e) => setEvidence(e.target.value)} rows={2} />
      {!initial && (
        <SelectField label="File corrected documents under" optional value={itemKey} onChange={(e) => setItemKey(e.target.value)} options={slots} placeholder="No document upload" hint="Only a document requirement of this application’s rule version can take a file." />
      )}
      <div className="action-bar">
        <Button type="submit" size="sm" loading={busy} disabled={title.trim().length < 3 || description.trim().length < 5}>
          {initial ? 'Save changes' : 'Add requirement'}
        </Button>
        <Button variant="quiet" size="sm" onClick={onDone}>
          Close
        </Button>
      </div>
    </form>
  )
}

function RoundView({ round }: { round: Round }) {
  const answered = new Map((round.response?.items ?? []).map((i) => [i.requirement_id, i]))
  return (
    <li className="notice-card" data-status={round.status === 'OPEN' ? 'ISSUED' : 'CLOSED'}>
      <p>
        <strong>Round {round.number}</strong> · {humanise(round.status)} · requested {formatDate(round.requested_at)}
      </p>
      <blockquote>{round.instructions}</blockquote>
      {round.policy_resolution && <p className="note">Response period: {round.policy_resolution.message}</p>}
      <ol className="notice-items">
        {round.requirements.map((r) => {
          const a = answered.get(r.id)
          return (
            <li key={r.id}>
              <strong>{r.title}</strong>
              <div>{r.description}</div>
              {a ? (
                <div className="response-block">
                  <p>{a.explanation || <span className="muted">No explanation.</span>}</p>
                  {a.documents.length > 0 && <p className="mono muted">{a.documents.map((d) => d && `${d.item_key} v${d.version_number} (${d.filename})`).join(', ')}</p>}
                </div>
              ) : (
                <p className="note">Not answered.</p>
              )}
            </li>
          )
        })}
      </ol>
      {round.response?.status === 'SUBMITTED' && <p className="note">Received {formatDate(round.response.submitted_at)}. Received is not accepted: the committee assesses it.</p>}
    </li>
  )
}

export function RequirementsPanel({ ctx }: { ctx: PanelCtx }) {
  const { detail, run, busy, error } = ctx
  const shc = detail.shc
  const [form, setForm] = useState<null | { initial?: Requirement; origin?: { type: string; id?: string } }>(null)
  const [dialog, setDialog] = useState(false)
  const [instructions, setInstructions] = useState('')
  if (!shc) return null
  const a = detail.allowed_actions
  const coordinator = detail.scope.includes('coordinator')
  const permitted = detail.policy?.shc?.compliance_submission
  return (
    <Section title="Requirements and compliance">
      <Alert tone="info">
        {permitted === 'PERMITTED' && <>The institution has a chance to submit compliance before the committee reviews (APH Ch. II cl. 2.23.3). </>}
        {permitted === 'NOT_PERMITTED' && <>The configuration does not permit institution compliance in this process. </>}
        {!permitted && <>The configuration does not establish whether the institution may submit compliance, so nothing can be requested. </>}
        A submitted response is received, never accepted automatically. No response deadline is set unless a verified policy gives one.
      </Alert>
      {coordinator && shc.shortcomings.length > 0 && (
        <details className="panel panel--collapsible" open>
          <summary>Shortcomings recorded when this stage was activated ({shc.shortcomings.length})</summary>
          <p className="note">These are internal records from earlier stages. Turn each into a requirement in your own words if it should be put to the institution.</p>
          <ul className="plain-list">
            {shc.shortcomings.map((s) => (
              <li key={s.id}>
                <Badge tone="neutral">{s.origin === 'scrutiny_finding' ? `Scrutiny finding ${s.sequence ?? ''}` : 'EVC observation'}</Badge> {s.summary}
                {a.includes('add_requirement') && (
                  <Button variant="quiet" size="sm" onClick={() => setForm({ origin: { type: s.origin, id: s.id } })}>
                    Write a requirement from this
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
      {shc.prepared.length > 0 && (
        <>
          <h3 className="panel__sub">Being prepared (the institution cannot see these yet)</h3>
          <ol className="notice-items">
            {shc.prepared.map((r) => (
              <li key={r.id}>
                <strong>{r.title}</strong>
                <div>{r.description}</div>
                {r.source_ref_status !== 'ESTABLISHED' && <p className="note">No source reference established.</p>}
                {a.includes('edit_requirement') && (
                  <Button variant="quiet" size="sm" onClick={() => setForm({ initial: r })}>
                    Edit
                  </Button>
                )}
              </li>
            ))}
          </ol>
        </>
      )}
      {errorNote(error)}
      {form && <RequirementForm ctx={ctx} initial={form.initial} origin={form.origin} onDone={() => setForm(null)} />}
      {coordinator && (
        <div className="action-bar">
          {a.includes('add_requirement') && !form && (
            <Button variant="secondary" onClick={() => setForm({})}>
              Add a requirement…
            </Button>
          )}
          {a.includes('request_compliance') && (
            <Button disabled={shc.prepared.length === 0} onClick={() => { setInstructions(''); setDialog(true) }}>
              {shc.rounds.length ? 'Request a further round…' : 'Request compliance…'}
            </Button>
          )}
          {a.includes('start_review') && detail.state === 'PREPARATION' && (
            <Button variant="secondary" loading={busy} onClick={() => void run(() => committeeApi.startReview(detail.id, detail.row_version))}>
              Start review (compliance is not permitted here)
            </Button>
          )}
        </div>
      )}
      {shc.rounds.length > 0 && (
        <>
          <h3 className="panel__sub">Compliance rounds</h3>
          <ul className="notice-list">
            {shc.rounds.map((r) => (
              <RoundView key={r.id} round={r} />
            ))}
          </ul>
        </>
      )}
      <ConfirmDialog
        open={dialog}
        title="Request compliance from the institution"
        confirmLabel="Send the request"
        busy={busy}
        confirmDisabled={!instructions.trim()}
        onCancel={() => setDialog(false)}
        onConfirm={async () => {
          if (await run(() => committeeApi.requestCompliance(detail.id, instructions.trim(), detail.row_version))) setDialog(false)
        }}
      >
        <Alert tone="warning">The prepared requirements are put to the institution and can no longer be changed. A notification is stored in the application; no email or SMS is sent.</Alert>
        <TextAreaField label="Instructions the institution will see" value={instructions} onChange={(e) => setInstructions(e.target.value)} rows={4} />
      </ConfirmDialog>
    </Section>
  )
}

// ---- review: start, findings, hearing records ------------------------------------------------------------------

function FindingForm({ ctx, onDone, initial }: { ctx: PanelCtx; onDone: () => void; initial?: ShcFinding }) {
  const { detail, run, busy } = ctx
  const reqs = detail.shc?.rounds.flatMap((r) => r.requirements.map((q) => ({ value: q.id, label: `Round ${r.number}: ${q.title}` }))) ?? []
  const [req, setReq] = useState(initial?.requirement_id ?? '')
  const [assessment, setAssessment] = useState(initial?.assessment ?? '')
  const [text, setText] = useState(initial?.text ?? '')
  return (
    <form
      className="panel"
      aria-label={initial ? 'Edit finding' : 'Record a finding'}
      onSubmit={(e) => {
        e.preventDefault()
        const go = initial ? () => committeeApi.editFinding(detail.id, initial.id, { assessment, text, row_version: initial.row_version }) : () => committeeApi.recordFinding(detail.id, { requirement_id: req || null, assessment, text })
        void run(go).then((ok) => ok && onDone())
      }}
    >
      {!initial && <SelectField label="Requirement" value={req} onChange={(e) => setReq(e.target.value)} options={reqs} placeholder="Not about one requirement" />}
      <SelectField label="What the evidence shows" value={assessment} onChange={(e) => setAssessment(e.target.value as ShcFinding['assessment'])} options={Object.entries(ASSESSMENT_WORDS).map(([value, label]) => ({ value, label }))} required />
      <TextAreaField label="Reasons" value={text} onChange={(e) => setText(e.target.value)} rows={3} hint="This is an assessment of evidence. It is not a decision on the application." />
      <div className="action-bar">
        <Button type="submit" size="sm" loading={busy} disabled={!assessment || text.trim().length < 5}>
          {initial ? 'Save changes' : 'Record finding'}
        </Button>
        <Button variant="quiet" size="sm" onClick={onDone}>
          Close
        </Button>
      </div>
    </form>
  )
}

export function ReviewPanel({ ctx }: { ctx: PanelCtx }) {
  const { detail, run, busy, error } = ctx
  const shc = detail.shc
  const [form, setForm] = useState<null | { initial?: ShcFinding }>(null)
  const [proc, setProc] = useState({ held_local: '', timezone: 'Asia/Kolkata', participants: '', text: '', schedule_id: '' })
  const [procOpen, setProcOpen] = useState(false)
  if (!shc) return null
  const a = detail.allowed_actions
  const active = detail.schedule.active
  const reqTitle = new Map(shc.rounds.flatMap((r) => r.requirements.map((q) => [q.id, `Round ${r.number}: ${q.title}`] as const)))
  return (
    <>
      {a.includes('start_review') && detail.state === 'COMPLIANCE_RECEIVED' && (
        <Section title="Start the review">
          <p className="note">Members must be assigned before the review starts. Starting it opens the recommendation draft.</p>
          {errorNote(error)}
          <Button loading={busy} onClick={() => void run(() => committeeApi.startReview(detail.id, detail.row_version))}>
            Start review
          </Button>
        </Section>
      )}
      <Section title={`Findings (${shc.findings.length})`}>
        <Alert tone="info">A finding assesses the evidence for a requirement. It is kept separate from the recommendation and from any decision. Uploading evidence does not mark it accepted.</Alert>
        {shc.findings.length === 0 ? (
          <p className="note">No findings have been recorded.</p>
        ) : (
          <ul className="finding-list">
            {shc.findings.map((f) => (
              <li key={f.id} className="finding-card">
                <p>
                  <Badge tone={f.assessment === 'EVIDENCE_SUPPORTS' ? 'success' : f.assessment === 'EVIDENCE_DOES_NOT_SUPPORT' ? 'error' : 'warning'}>{ASSESSMENT_WORDS[f.assessment]}</Badge> {f.requirement_id ? reqTitle.get(f.requirement_id) : 'General'}
                </p>
                <blockquote>{f.text}</blockquote>
                <p className="mono muted">
                  Finding {f.sequence} · {f.by} · {formatDate(f.at)}
                </p>
                {a.includes('edit_finding') && (
                  <Button variant="quiet" size="sm" onClick={() => setForm({ initial: f })}>
                    Edit
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        {form && <FindingForm ctx={ctx} initial={form.initial} onDone={() => setForm(null)} />}
        {a.includes('record_finding') && !form && (
          <Button variant="secondary" onClick={() => setForm({})}>
            Record a finding…
          </Button>
        )}
      </Section>
      <Section title={`Hearing and sitting records (${shc.proceedings.length})`}>
        <p className="note">Records are append-only. Naming a confirmed hearing records that it took place.</p>
        {shc.proceedings.length === 0 ? (
          <p className="note">Nothing has been recorded.</p>
        ) : (
          <ul className="plain-list">
            {shc.proceedings.map((p) => (
              <li key={p.id}>
                <strong>{formatDate(p.held_at)}</strong> — {p.by}
                {p.participants && <div className="muted">Present: {p.participants}</div>}
                <blockquote>{p.text}</blockquote>
              </li>
            ))}
          </ul>
        )}
        {a.includes('record_proceeding') && (
          <>
            {!procOpen && (
              <Button variant="secondary" onClick={() => setProcOpen(true)}>
                Record a hearing or sitting…
              </Button>
            )}
            {procOpen && (
              <form
                className="panel"
                aria-label="Record a hearing or sitting"
                onSubmit={(e) => {
                  e.preventDefault()
                  void run(() => committeeApi.recordProceeding(detail.id, { ...proc, schedule_id: proc.schedule_id || null })).then((ok) => { if (ok) { setProcOpen(false); setProc({ held_local: '', timezone: 'Asia/Kolkata', participants: '', text: '', schedule_id: '' }) } })
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
          </>
        )}
      </Section>
    </>
  )
}

// ---- the recommendation ---------------------------------------------------------------------------------------

export function RecommendationPanel({ ctx }: { ctx: PanelCtx }) {
  const { detail, run, busy, error } = ctx
  const shc = detail.shc
  const draft = shc?.draft ?? null
  const pol = detail.policy?.shc
  const [option, setOption] = useState<string>(draft?.content.option ?? '')
  const [text, setText] = useState<Record<string, string>>(draft?.content.sections ?? {})
  const [key, setKey] = useState(newKey)
  const [confirm, setConfirm] = useState(false)
  useEffect(() => {
    setOption(draft?.content.option ?? '')
    setText(draft?.content.sections ?? {})
  }, [draft?.row_version, draft?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!shc || !pol) return null
  const editable = detail.scope.includes('member') && detail.state === 'UNDER_REVIEW' && draft !== null
  const dirty = JSON.stringify({ option: option || null, text }) !== JSON.stringify({ option: draft?.content.option ?? null, text: draft?.content.sections ?? {} })
  const issues = error?.code === 'recommendation_incomplete' ? error.issues : []
  return (
    <Section title="Recommendation">
      <Alert tone="info">A recommendation is a record for the Executive Committee’s later decision process. It is not that decision, and nothing here approves or rejects an application.</Alert>
      {editable && draft ? (
        <form
          aria-label="Recommendation draft"
          onSubmit={(e) => {
            e.preventDefault()
            void run(() => committeeApi.saveRecommendation(detail.id, option || null, text, draft.row_version))
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
        shc.versions.length === 0 && <p className="note">The recommendation is prepared during review.</p>
      )}
      {shc.versions.length > 0 && (
        <>
          <h3 className="panel__sub">Submitted versions (kept unchanged)</h3>
          <ul className="notice-list">
            {shc.versions.map((v) => (
              <li key={v.id} className="notice-card" data-status="CLOSED">
                <p>
                  <strong>Version {v.version_number}</strong> · {pol.recommendation_options.find((o) => o.key === v.content.option)?.label ?? v.content.option} · submitted {formatDate(v.submitted_at)} by {v.submitted_by}
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
          if (draft && (await run(() => committeeApi.submitRecommendation(detail.id, key, draft.row_version)))) setKey(newKey())
          setConfirm(false)
        }}
      >
        <p>The submitted version is kept exactly as it is, with a record of the evidence and assessments it rests on. A correction needs a coordinator to open an amendment, which creates a new version.</p>
      </ConfirmDialog>
    </Section>
  )
}
