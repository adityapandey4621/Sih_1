import { useEffect, useMemo, useState } from 'react'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Alert, Badge } from '../../components/ui/feedback'
import { CheckboxField, SelectField, TextAreaField, TextField } from '../../components/ui/Field'
import { formatBytes, formatDate } from '../../lib/format'
import { Section } from '../applications/parts'
import { committeeApi, newKey } from './committeeApi'
import { OBSERVATION_WORDS } from './parts'
import { errorNote, type PanelCtx } from './SharedPanels'
import type { ChecklistRow, Observation, ReportVersion } from './types'

// ---- one checklist item's observation --------------------------------------------------------------------------------

function ObservationForm({ ctx, item, obs, docs, editable }: { ctx: PanelCtx; item: ChecklistRow; obs: Observation | undefined; docs: { id: string; label: string }[]; editable: boolean }) {
  const [status, setStatus] = useState(obs?.status ?? '')
  const [note, setNote] = useState(obs?.note ?? '')
  const [internal, setInternal] = useState(obs?.internal_note ?? '')
  const [shortcoming, setShortcoming] = useState(obs?.shortcoming ?? false)
  const [refs, setRefs] = useState<string[]>(obs?.document_refs.map((r) => r.document_id) ?? [])
  useEffect(() => {
    setStatus(obs?.status ?? '')
    setNote(obs?.note ?? '')
    setInternal(obs?.internal_note ?? '')
    setShortcoming(obs?.shortcoming ?? false)
    setRefs(obs?.document_refs.map((r) => r.document_id) ?? [])
  }, [obs?.row_version, obs?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  const { detail, run, busy } = ctx
  return (
    <li className="observation" data-recorded={obs ? 'true' : 'false'}>
      <div className="checklist__head">
        <span className="checklist__label">{item.label}</span>
        <span className="checklist__tags">
          <Badge tone={item.required ? 'info' : 'neutral'}>{item.required ? 'Required' : 'Optional'}</Badge>
          {item.evidence_required && <Badge tone="warning">Evidence needed</Badge>}
          {obs ? <Badge tone="success">Recorded: {OBSERVATION_WORDS[obs.status]}</Badge> : <Badge tone="neutral">Nothing recorded yet</Badge>}
        </span>
      </div>
      <p className="checklist__explain">{item.description}</p>
      <p className="checklist__meta mono">{item.clause_ref}</p>
      {editable ? (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            void run(() => committeeApi.observe(detail.id, item.item_key, { status, note: note.trim(), internal_note: internal.trim() || null, shortcoming, document_refs: refs.map((id) => ({ document_id: id })), row_version: obs?.row_version ?? null }))
          }}
          aria-label={`Observation for ${item.label}`}
        >
          <SelectField label="What you observed" value={status} onChange={(e) => setStatus(e.target.value as Observation['status'])} options={Object.entries(OBSERVATION_WORDS).map(([value, label]) => ({ value, label }))} required />
          <TextAreaField label="Observation (goes into the report)" value={note} onChange={(e) => setNote(e.target.value)} rows={3} hint="Say what you saw, or why it could not be seen. Do not state a conclusion about compliance." />
          <TextAreaField label="Internal note (never in the report, never shown to the institution)" value={internal} onChange={(e) => setInternal(e.target.value)} rows={2} optional />
          <CheckboxField label="Flag for the next stage’s determination" checked={shortcoming} onChange={(e) => setShortcoming(e.target.checked)} hint="This is a pointer for the coordinator, not a finding of non-compliance." />
          {docs.length > 0 && (
            <fieldset className="member-pick">
              <legend>Documents this observation relies on (current versions)</legend>
              {docs.map((d) => (
                <CheckboxField key={d.id} label={d.label} checked={refs.includes(d.id)} onChange={(e) => setRefs((r) => (e.target.checked ? [...r, d.id] : r.filter((x) => x !== d.id)))} />
              ))}
            </fieldset>
          )}
          <Button type="submit" size="sm" loading={busy} disabled={!status || note.trim().length < 3}>
            {obs ? 'Update observation' : 'Record observation'}
          </Button>
        </form>
      ) : (
        obs && (
          <div className="response-block">
            <p>
              <strong>{OBSERVATION_WORDS[obs.status]}</strong> — {obs.note}
            </p>
            {obs.shortcoming && <p className="note">Flagged for the next stage’s determination.</p>}
            {obs.internal_note && <p className="note">Internal: {obs.internal_note}</p>}
            <p className="mono muted">
              {obs.recorded_by} · {formatDate(obs.updated_at)}
              {obs.document_refs.length > 0 && ` · documents: ${obs.document_refs.map((r) => `${r.item_key} v${r.version_number}`).join(', ')}`}
            </p>
          </div>
        )
      )}
    </li>
  )
}

// ---- evidence -----------------------------------------------------------------------------------------------------

function EvidenceSection({ ctx, editable }: { ctx: PanelCtx; editable: boolean }) {
  const { detail, run, busy } = ctx
  const evidence = detail.evc?.evidence ?? []
  const [file, setFile] = useState<File | null>(null)
  const [item, setItem] = useState('')
  const [desc, setDesc] = useState('')
  const [url, setUrl] = useState('')
  const [linkDesc, setLinkDesc] = useState('')
  const items = detail.policy?.checklist ?? []
  return (
    <Section title={`Visit evidence (${evidence.length})`}>
      <Alert tone="info">
        Files are stored privately and are visible only to this case’s committee. They are <strong>not</strong> scanned for malware or read by text recognition here. A link records where material is held; the system never opens or vouches for it.
      </Alert>
      {evidence.length === 0 ? (
        <p className="note">No evidence has been added.</p>
      ) : (
        <ul className="plain-list">
          {evidence.map((e) => (
            <li key={e.id}>
              <strong>{e.kind === 'FILE' ? e.filename : 'Link'}</strong> {e.item_key && <span className="mono muted">· {e.item_key}</span>} — {e.description}
              {e.kind === 'FILE' && (
                <div className="mono muted">
                  {e.size_bytes ? formatBytes(e.size_bytes) : ''} · sha256 {e.sha256?.slice(0, 12)}… · <a href={e.download ?? '#'}>Download</a>
                </div>
              )}
              {e.kind === 'LINK' && <div className="mono muted">{e.url} (recorded, not verified)</div>}
              {editable && (
                <Button variant="quiet" size="sm" onClick={() => void run(() => committeeApi.removeEvidence(detail.id, e.id))}>
                  Remove (stays on record)
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {editable && (
        <div className="two-col">
          <form
            className="panel"
            aria-label="Add an evidence file"
            onSubmit={(e) => {
              e.preventDefault()
              if (file) void run(() => committeeApi.addEvidenceFile(detail.id, file, item || null, desc.trim())).then((ok) => { if (ok) { setFile(null); setDesc('') } })
            }}
          >
            <h3 className="panel__sub">Add a file</h3>
            <div className="field">
              <label className="field__label" htmlFor="evfile">
                File (PDF, PNG or JPEG)
              </label>
              <input id="evfile" type="file" accept="application/pdf,image/png,image/jpeg" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </div>
            <SelectField label="Checklist item" optional value={item} onChange={(e) => setItem(e.target.value)} options={items.map((i) => ({ value: i.item_key, label: i.label }))} placeholder="Not tied to one item" />
            <TextField label="What it shows" value={desc} onChange={(e) => setDesc(e.target.value)} />
            <Button type="submit" size="sm" loading={busy} disabled={!file}>
              Store file
            </Button>
          </form>
          <form
            className="panel"
            aria-label="Add a link"
            onSubmit={(e) => {
              e.preventDefault()
              void run(() => committeeApi.addEvidenceLink(detail.id, url.trim(), linkDesc.trim(), item || null)).then((ok) => { if (ok) { setUrl(''); setLinkDesc('') } })
            }}
          >
            <h3 className="panel__sub">Record a link</h3>
            <TextField label="Web address" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" />
            <TextField label="What it is" value={linkDesc} onChange={(e) => setLinkDesc(e.target.value)} />
            <Button type="submit" size="sm" variant="secondary" loading={busy} disabled={!url.trim() || !linkDesc.trim()}>
              Record link
            </Button>
          </form>
        </div>
      )}
    </Section>
  )
}

// ---- the report ---------------------------------------------------------------------------------------------------

function VersionView({ v }: { v: ReportVersion }) {
  const snap = v.source_snapshot as { documents?: { item_key: string; version_number: number }[]; observations?: unknown[]; evidence?: unknown[]; visit?: { mode: string } | null } | null | undefined
  return (
    <li className="notice-card" data-status="CLOSED">
      <p>
        <strong>Version {v.version_number}</strong> · submitted {formatDate(v.submitted_at)} by {v.submitted_by} {v.amendment_reason && <span className="note">· amendment: {v.amendment_reason}</span>}
      </p>
      {Object.entries(v.content.sections ?? {}).map(([k, text]) => (
        <div key={k}>
          <p className="mono muted">{k}</p>
          <blockquote>{text}</blockquote>
        </div>
      ))}
      {snap && (
        <p className="mono muted">
          Rests on {snap.documents?.map((d) => `${d.item_key} v${d.version_number}`).join(', ') || 'no documents'} · {snap.observations?.length ?? 0} observations · {snap.evidence?.length ?? 0} evidence items{snap.visit ? ` · ${snap.visit.mode.toLowerCase()} visit` : ''}
        </p>
      )}
      <p className="mono muted">content hash {v.content_hash?.slice(0, 16)}…</p>
    </li>
  )
}

function ReportSection({ ctx, editable }: { ctx: PanelCtx; editable: boolean }) {
  const { detail, run, busy, error } = ctx
  const draft = detail.evc?.draft ?? null
  const versions = detail.evc?.versions ?? []
  const sections = detail.policy?.report_sections ?? []
  const [text, setText] = useState<Record<string, string>>(draft?.content.sections ?? {})
  const [key, setKey] = useState(newKey)
  const [confirm, setConfirm] = useState(false)
  useEffect(() => setText(draft?.content.sections ?? {}), [draft?.row_version, draft?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = useMemo(() => JSON.stringify(text) !== JSON.stringify(draft?.content.sections ?? {}), [text, draft])
  const issues = error?.issues ?? []
  return (
    <Section title="Report">
      <Alert tone="info">A report is a record of the visit for the next stage. It is not an approval, a rejection or a decision, and submitting it does not complete the stage.</Alert>
      {draft && editable ? (
        <form
          aria-label="Report draft"
          onSubmit={(e) => {
            e.preventDefault()
            void run(() => committeeApi.saveReport(detail.id, text, draft.row_version))
          }}
        >
          <p className="note">
            Draft of version {draft.version_number}
            {draft.amendment_reason ? ` (amendment: ${draft.amendment_reason})` : ''}.
          </p>
          {sections.map((s) => (
            <TextAreaField key={s.key} label={s.label} optional={!s.required} value={text[s.key] ?? ''} onChange={(e) => setText((t) => ({ ...t, [s.key]: e.target.value }))} rows={4} />
          ))}
          {issues.length > 0 && error?.code === 'report_incomplete' && (
            <Alert tone="warning" title="The report cannot be submitted yet">
              <ul className="plain-list">
                {issues.map((i) => (
                  <li key={`${i.code}-${i.path}`}>{i.message}</li>
                ))}
              </ul>
            </Alert>
          )}
          {error && error.code !== 'report_incomplete' && errorNote(error)}
          <div className="action-bar">
            <Button type="submit" variant="secondary" loading={busy} disabled={!dirty}>
              Save draft
            </Button>
            <Button loading={busy} disabled={dirty} onClick={() => setConfirm(true)}>
              Submit the report…
            </Button>
          </div>
          {dirty && <p className="note">Save the draft before submitting.</p>}
        </form>
      ) : draft ? (
        <p className="note">A draft (version {draft.version_number}) is being prepared by the assigned members.</p>
      ) : versions.length === 0 ? (
        <p className="note">The report is prepared during the visit.</p>
      ) : null}
      {versions.length > 0 && (
        <>
          <h3 className="panel__sub">Submitted versions (kept unchanged)</h3>
          <ul className="notice-list">
            {versions.map((v) => (
              <VersionView key={v.id} v={v} />
            ))}
          </ul>
        </>
      )}
      <ConfirmDialog
        open={confirm}
        title="Submit the report"
        confirmLabel="Submit the report"
        busy={busy}
        onCancel={() => setConfirm(false)}
        onConfirm={async () => {
          if (draft && (await run(() => committeeApi.submitReport(detail.id, key, draft.row_version)))) {
            setConfirm(false)
            setKey(newKey())
          } else setConfirm(false)
        }}
      >
        <p>The submitted version is kept exactly as it is, with a record of the documents, observations and evidence it rests on. A correction later needs a coordinator to open an amendment, which creates a new version.</p>
      </ConfirmDialog>
    </Section>
  )
}

function AmendBox({ ctx }: { ctx: PanelCtx }) {
  const { detail, run, busy } = ctx
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  if (!detail.allowed_actions.includes('open_amendment')) return null
  return (
    <div className="action-bar">
      <Button variant="secondary" onClick={() => { setReason(''); setOpen(true) }}>
        Open an amendment…
      </Button>
      <ConfirmDialog
        open={open}
        title="Open an amendment"
        confirmLabel="Open amendment"
        busy={busy}
        confirmDisabled={reason.trim().length < 5}
        onCancel={() => setOpen(false)}
        onConfirm={async () => {
          const ok = await run(() => (detail.stage === 'EVC' ? committeeApi.amendReport(detail.id, reason.trim(), detail.row_version) : detail.stage === 'SAC' ? committeeApi.sacAmendRecommendation(detail.id, reason.trim(), detail.row_version) : committeeApi.amendRecommendation(detail.id, reason.trim(), detail.row_version)))
          if (ok) setOpen(false)
        }}
      >
        <Alert tone="warning">The submitted version stays unchanged. A new draft version is opened for the members, with this reason on record.</Alert>
        <TextAreaField label="Why an amendment is needed" value={reason} onChange={(e) => setReason(e.target.value)} rows={3} />
      </ConfirmDialog>
    </div>
  )
}

export { AmendBox }

// ---- the visit workspace ------------------------------------------------------------------------------------------

export function VisitPanel({ ctx, documents }: { ctx: PanelCtx; documents: { id: string; label: string }[] }) {
  const { detail, run, busy, error } = ctx
  const checklist = detail.policy?.checklist ?? []
  const obsByKey = new Map((detail.evc?.observations ?? []).map((o) => [o.item_key, o]))
  const member = detail.scope.includes('member')
  const working = detail.state === 'VISIT_IN_PROGRESS'
  const editable = member && working
  return (
    <>
      {detail.allowed_actions.includes('start_visit') && (
        <Section title="Start the visit">
          <p className="note">Start the visit when it takes place. Observations and evidence can only be recorded once it has started.</p>
          {errorNote(error)}
          <Button loading={busy} onClick={() => void run(() => committeeApi.startVisit(detail.id, detail.row_version))}>
            Start the visit
          </Button>
        </Section>
      )}
      {detail.state !== 'SCHEDULED' || !member ? null : <Alert tone="info">The visit has a confirmed schedule. Start it when it takes place.</Alert>}
      <Section title="Visit checklist">
        {detail.policy?.banner && <p className="note">{detail.policy.banner}</p>}
        {checklist.length === 0 ? (
          <p className="note">No checklist is configured for this case.</p>
        ) : (
          <ol className="checklist observations">
            {checklist.map((item) => (
              <ObservationForm key={item.item_key} ctx={ctx} item={item} obs={obsByKey.get(item.item_key)} docs={documents} editable={editable} />
            ))}
          </ol>
        )}
        {errorNote(error && error.code !== 'report_incomplete' ? error : null)}
      </Section>
      <EvidenceSection ctx={ctx} editable={editable} />
      <ReportSection ctx={ctx} editable={editable} />
      <AmendBox ctx={ctx} />
    </>
  )
}
