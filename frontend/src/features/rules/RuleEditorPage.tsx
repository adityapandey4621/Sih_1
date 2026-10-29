import { useState, type Dispatch, type SetStateAction } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Plus, Trash2, X } from 'lucide-react'
import { ApiError } from '../../lib/api'
import { useResource } from '../../lib/useResource'
import { Button, ButtonLink } from '../../components/ui/Button'
import { Alert, Badge } from '../../components/ui/feedback'
import { AccessDenied } from '../../components/ui/feedback'
import { CheckboxField, SelectField, TextAreaField, TextField } from '../../components/ui/Field'
import { ResourceView } from '../admin/shared'
import { useAuth } from '../auth/AuthContext'
import { Section, SOURCE_BADGE } from '../applications/parts'
import { PageHeader } from '../shell/PageHeader'
import '../applications/applications.css'
import { blankExtraction, blankItem, blankPolicy, blankStage, fromVersion, isAcademicYear, toPayload, type EditorState, type ExtractionDraft, type ItemDraft, type PolicyDraft, type StageDraft } from './editorModel'
import { ReadinessPanel } from './parts'
import { rulesApi } from './rulesApi'
import type { VersionDetail } from './types'
import type { Source } from '../applications/types'

type SetState<T> = Dispatch<SetStateAction<T>>
const patchAt = <T extends { uid: string }>(set: SetState<EditorState>, field: 'items' | 'stages' | 'policies', uid: string, p: Partial<T>) =>
  set((e) => ({ ...e, [field]: (e[field] as unknown as T[]).map((x) => (x.uid === uid ? { ...x, ...p } : x)) }))
const removeAt = (set: SetState<EditorState>, field: 'items' | 'stages' | 'policies', uid: string) =>
  set((e) => ({ ...e, [field]: (e[field] as unknown as { uid: string }[]).filter((x) => x.uid !== uid) }))

function SourcePick({ value, onChange, sources, label = 'Source' }: { value: string; onChange: (v: string) => void; sources: Source[]; label?: string }) {
  return <SelectField label={label} optional value={value} placeholder="None" onChange={(e) => onChange(e.target.value)} options={sources.map((s) => ({ value: s.id, label: s.title.slice(0, 80) }))} />
}

function ItemCard({ i, set, sources, others }: { i: ItemDraft; set: SetState<EditorState>; sources: Source[]; others: ItemDraft[] }) {
  const up = (p: Partial<ItemDraft>) => patchAt<ItemDraft>(set, 'items', i.uid, p)
  const isField = i.item_type === 'form_field'
  const upExtraction = (uid: string, p: Partial<ExtractionDraft>) => up({ extraction: i.extraction.map((x) => (x.uid === uid ? { ...x, ...p } : x)) })
  return (
    <div className="row-card">
      <div className="row-card__head">
        <strong>{i.label || 'New checklist item'}</strong>
        <Button size="sm" variant="quiet" onClick={() => removeAt(set, 'items', i.uid)} aria-label={`Remove checklist item ${i.label || i.item_key}`}>
          <Trash2 size={16} aria-hidden="true" /> Remove
        </Button>
      </div>
      <div className="row-card__grid">
        <TextField label="Stable key" value={i.item_key} onChange={(e) => up({ item_key: e.target.value })} hint="Lower-case letters, digits, - and _. Never changes between versions." />
        <TextField label="Label" value={i.label} onChange={(e) => up({ label: e.target.value })} />
        <SelectField label="Item type" value={i.item_type} placeholder="Type" onChange={(e) => up({ item_type: e.target.value as ItemDraft['item_type'] })} options={[{ value: 'document_upload', label: 'Document upload' }, { value: 'form_field', label: 'Form field' }, { value: 'declaration', label: 'Declaration' }]} />
        {isField && <SelectField label="Field kind" value={i.field_kind} placeholder="Kind" onChange={(e) => up({ field_kind: e.target.value as ItemDraft['field_kind'] })} options={['text', 'long_text', 'number', 'select', 'date'].map((k) => ({ value: k, label: k.replace('_', ' ') }))} />}
      </div>
      <TextAreaField label="What this item represents" value={i.explanation} onChange={(e) => up({ explanation: e.target.value })} hint="Required for required items: shown to the institution." />
      <CheckboxField label="Required" checked={i.required} onChange={(e) => up({ required: e.target.checked })} />
      {isField && i.field_kind === 'select' && <TextAreaField label="Options (one per line)" value={i.optionsText} onChange={(e) => up({ optionsText: e.target.value })} />}
      {isField && (
        <div className="row-card__grid">
          {(i.field_kind === 'text' || i.field_kind === 'long_text') && (
            <>
              <TextField label="Minimum length" optional type="number" min={0} value={i.min_length} onChange={(e) => up({ min_length: e.target.value })} />
              <TextField label="Maximum length" optional type="number" min={1} value={i.max_length} onChange={(e) => up({ max_length: e.target.value })} />
            </>
          )}
          {i.field_kind === 'number' && (
            <>
              <TextField label="Minimum value" optional type="number" value={i.min} onChange={(e) => up({ min: e.target.value })} />
              <TextField label="Maximum value" optional type="number" value={i.max} onChange={(e) => up({ max: e.target.value })} />
            </>
          )}
        </div>
      )}
      {i.item_type === 'document_upload' && (
        <fieldset className="field extraction-config">
          <legend className="field__label">Values to look for in this document</legend>
          <p className="field__hint">Each is found by the label that precedes it on the page. Nothing is looked for unless listed here, and a value that cannot be found is reported as “unable to extract”, never guessed.</p>
          {i.extraction.map((x) => (
            <div className="row-card" key={x.uid}>
              <div className="row-card__grid">
                <TextField label="Value key" value={x.field_key} onChange={(e) => upExtraction(x.uid, { field_key: e.target.value })} />
                <TextField label="Label" value={x.label} onChange={(e) => upExtraction(x.uid, { label: e.target.value })} />
                <SelectField label="Kind" value={x.kind} placeholder="Kind" onChange={(e) => upExtraction(x.uid, { kind: e.target.value as ExtractionDraft['kind'] })} options={[{ value: 'text', label: 'Text' }, { value: 'date', label: 'Date' }, { value: 'identifier', label: 'Identifier / reference' }]} />
                <TextField label="Compare group" optional value={x.compare_group} onChange={(e) => upExtraction(x.uid, { compare_group: e.target.value })} hint="Values in the same group are compared across documents." />
              </div>
              <TextAreaField label="Labels that precede the value (one per line)" value={x.anchorsText} onChange={(e) => upExtraction(x.uid, { anchorsText: e.target.value })} />
              <CheckboxField label="Expected: report a preliminary issue if it is not found" checked={x.required} onChange={(e) => upExtraction(x.uid, { required: e.target.checked })} />
              <CheckboxField label="Compare with the institution name on the application record" checked={x.compare_to_application} onChange={(e) => upExtraction(x.uid, { compare_to_application: e.target.checked })} />
              <div className="inline-actions">
                <Button size="sm" variant="quiet" onClick={() => up({ extraction: i.extraction.filter((y) => y.uid !== x.uid) })} aria-label={`Remove value ${x.label || x.field_key}`}>
                  <Trash2 size={16} aria-hidden="true" /> Remove value
                </Button>
              </div>
            </div>
          ))}
          <div className="inline-actions">
            <Button size="sm" variant="secondary" onClick={() => up({ extraction: [...i.extraction, blankExtraction()] })}>
              <Plus size={16} aria-hidden="true" /> Add value to look for
            </Button>
          </div>
        </fieldset>
      )}      <div className="row-card__grid">
        <SelectField label="Applies only when…" optional placeholder="Always applies" value={i.when_item} onChange={(e) => up({ when_item: e.target.value })} options={others.filter((o) => o.item_type === 'form_field' && o.item_key && o.uid !== i.uid).map((o) => ({ value: o.item_key, label: o.label || o.item_key }))} />
        {i.when_item && <TextField label="…equals" value={i.when_value} onChange={(e) => up({ when_value: e.target.value })} hint="The exact answer that makes this item apply." />}
        <SourcePick value={i.source_id} onChange={(v) => up({ source_id: v })} sources={sources} />
        <TextField label="Clause / table reference" optional value={i.clause_ref} onChange={(e) => up({ clause_ref: e.target.value })} />
      </div>
    </div>
  )
}

function StageCard({ s, set, sources, all }: { s: StageDraft; set: SetState<EditorState>; sources: Source[]; all: StageDraft[] }) {
  const up = (p: Partial<StageDraft>) => patchAt<StageDraft>(set, 'stages', s.uid, p)
  const others = all.filter((o) => o.uid !== s.uid && o.stage_key)
  return (
    <div className="row-card">
      <div className="row-card__head">
        <strong>{s.label || 'New stage'}</strong>
        <Button size="sm" variant="quiet" onClick={() => removeAt(set, 'stages', s.uid)} aria-label={`Remove stage ${s.label || s.stage_key}`}>
          <Trash2 size={16} aria-hidden="true" /> Remove
        </Button>
      </div>
      <div className="row-card__grid">
        <TextField label="Stage key" value={s.stage_key} onChange={(e) => up({ stage_key: e.target.value })} />
        <TextField label="Label" value={s.label} onChange={(e) => up({ label: e.target.value })} />
      </div>
      <TextAreaField label="Description" optional value={s.description} onChange={(e) => up({ description: e.target.value })} />
      <CheckboxField label="Conditional stage (does not apply to every case)" checked={s.is_conditional} onChange={(e) => up({ is_conditional: e.target.checked })} />
      {s.is_conditional && <TextAreaField label="When does it apply?" value={s.condition_description} onChange={(e) => up({ condition_description: e.target.value })} hint="Required: describe the condition in words." />}
      <fieldset className="field">
        <legend className="field__label">Next stage(s)</legend>
        {others.length === 0 ? <p className="field__hint">Add another stage with a key to link to it. A stage with no next stage ends the route.</p> : (
          <div className="chip-list">
            {others.map((o) => (
              <CheckboxField key={o.uid} label={o.label || o.stage_key} checked={s.next.includes(o.stage_key)} onChange={(e) => up({ next: e.target.checked ? [...s.next, o.stage_key] : s.next.filter((k) => k !== o.stage_key) })} />
            ))}
          </div>
        )}
      </fieldset>
      <div className="row-card__grid">
        <SourcePick value={s.source_id} onChange={(v) => up({ source_id: v })} sources={sources} />
        <TextField label="Clause / table reference" optional value={s.clause_ref} onChange={(e) => up({ clause_ref: e.target.value })} />
      </div>
    </div>
  )
}

function PolicyCard({ p, set, sources }: { p: PolicyDraft; set: SetState<EditorState>; sources: Source[] }) {
  const up = (x: Partial<PolicyDraft>) => patchAt<PolicyDraft>(set, 'policies', p.uid, x)
  return (
    <div className="row-card">
      <div className="row-card__head">
        <strong>{p.label || 'New deadline policy'}</strong>
        <Button size="sm" variant="quiet" onClick={() => removeAt(set, 'policies', p.uid)} aria-label={`Remove policy ${p.label || p.policy_key}`}>
          <Trash2 size={16} aria-hidden="true" /> Remove
        </Button>
      </div>
      <div className="row-card__grid">
        <TextField label="Policy key" value={p.policy_key} onChange={(e) => up({ policy_key: e.target.value })} />
        <TextField label="Label" value={p.label} onChange={(e) => up({ label: e.target.value })} />
        <SelectField label="Type" value={p.deadline_type} placeholder="Type" onChange={(e) => up({ deadline_type: e.target.value as PolicyDraft['deadline_type'] })} options={[{ value: 'STATUTORY', label: 'Statutory' }, { value: 'INTERNAL_SLA', label: 'Internal SLA' }]} />
        <TextField label="Trigger event" value={p.trigger_event} onChange={(e) => up({ trigger_event: e.target.value })} hint="e.g. a named workflow event; not a date." />
      </div>
      <div className="row-card__grid">
        <TextField label="Duration" optional type="number" min={1} value={p.duration_value} onChange={(e) => up({ duration_value: e.target.value })} hint="Leave empty unless a verified source states it." />
        <SelectField label="Unit" optional placeholder="—" value={p.duration_unit} onChange={(e) => up({ duration_unit: e.target.value })} options={[{ value: 'days', label: 'days' }, { value: 'hours', label: 'hours' }]} />
        <SelectField label="Calendar convention" optional placeholder="Unspecified" value={p.calendar_convention} onChange={(e) => up({ calendar_convention: e.target.value })} options={[{ value: 'CALENDAR', label: 'Calendar days' }, { value: 'WORKING', label: 'Working days' }]} />
      </div>
      <TextAreaField label="Pause / resume conditions" optional value={p.pause_resume_note} onChange={(e) => up({ pause_resume_note: e.target.value })} hint="Only where established by the source or policy." />
      <div className="row-card__grid">
        <SourcePick value={p.source_id} onChange={(v) => up({ source_id: v })} sources={sources} label="Source (required if a duration is given)" />
        <TextField label="Clause / table reference" optional value={p.clause_ref} onChange={(e) => up({ clause_ref: e.target.value })} />
      </div>
    </div>
  )
}

function Editor({ initial, sources }: { initial: VersionDetail; sources: Source[] }) {
  const navigate = useNavigate()
  const [v, setV] = useState(initial)
  const [state, setState] = useState<EditorState>(() => fromVersion(initial))
  const [yearInput, setYearInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [alert, setAlert] = useState<{ tone: 'error' | 'warning'; message: string; stale?: boolean } | null>(null)

  const patch = (p: Partial<EditorState>) => setState((s) => ({ ...s, ...p }))
  const yearError = yearInput && !isAcademicYear(yearInput) ? 'Use the form 2026-27.' : null

  async function save() {
    setBusy(true)
    setAlert(null)
    setSaved(false)
    try {
      const next = await rulesApi.saveContent(v.id, toPayload(state, v.row_version))
      setV(next)
      setSaved(true)
    } catch (e) {
      if (e instanceof ApiError) {
        const fields = Object.entries(e.fieldErrors)
        setAlert({ tone: e.code === 'stale_version' ? 'warning' : 'error', stale: e.code === 'stale_version', message: fields.length ? `${e.message} ${fields.map(([k, m]) => `${k}: ${m}`).join(' · ')}` : e.message })
      } else setAlert({ tone: 'error', message: 'The draft could not be saved.' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        title={`Edit draft: ${v.rule_set.title} v${v.version_number}`}
        description="Changes are saved only when you press Save. Published versions can never be edited; a new version is created instead."
        crumbs={[{ label: 'Rule catalogue', to: '/app/rules' }, { label: `v${v.version_number}`, to: `/app/rules/versions/${v.id}` }, { label: 'Edit' }]}
      />
      {v.review_note && <Alert tone="warning" title="Changes were requested by the reviewer">{v.review_note}</Alert>}
      {alert && (
        <Alert tone={alert.tone}>
          {alert.message}
          {alert.stale && (
            <div className="inline-actions">
              <Button size="sm" variant="secondary" onClick={() => window.location.reload()}>Reload latest</Button>
            </div>
          )}
        </Alert>
      )}
      {saved && <Alert tone="success">Draft saved.</Alert>}
      <ReadinessPanel issues={v.publication_issues} status={v.status} />

      <Section title="Applicability">
        <div className="field">
          <span className="field__label">Academic years</span>
          <div className="chip-list">
            {state.academic_years.map((y) => (
              <span key={y} className="chip mono">
                {y}
                <button type="button" aria-label={`Remove ${y}`} onClick={() => patch({ academic_years: state.academic_years.filter((x) => x !== y) })}><X size={12} aria-hidden="true" /></button>
              </span>
            ))}
          </div>
        </div>
        <form className="row-card__grid" onSubmit={(e) => { e.preventDefault(); if (isAcademicYear(yearInput) && !state.academic_years.includes(yearInput)) { patch({ academic_years: [...state.academic_years, yearInput].sort() }); setYearInput('') } }}>
          <TextField label="Add an academic year" placeholder="2026-27" value={yearInput} onChange={(e) => setYearInput(e.target.value)} error={yearError} />
          <div className="filters__actions"><Button type="submit" size="sm" variant="secondary" disabled={!isAcademicYear(yearInput)}><Plus size={16} aria-hidden="true" /> Add year</Button></div>
        </form>
        <div className="row-card__grid">
          <TextField label="Effective from" optional type="date" value={state.effective_from} onChange={(e) => patch({ effective_from: e.target.value })} />
          <TextField label="Effective to" optional type="date" value={state.effective_to} onChange={(e) => patch({ effective_to: e.target.value })} />
        </div>
        <TextAreaField label="Change summary" value={state.change_summary} onChange={(e) => patch({ change_summary: e.target.value })} hint="What this version is, or what changed from the previous one." />
      </Section>

      <Section title="Sources">
        <p className="note">Cite every source this version relies on. An official-classified version can only be published when each source has been verified against the official publication.</p>
        {sources.length === 0 ? <p className="note">No sources exist yet. Add them in the catalogue's Sources tab first.</p> : (
          <div className="form-grid">
            {sources.map((s) => (
              <CheckboxField
                key={s.id}
                label={s.title}
                hint={<><Badge tone={SOURCE_BADGE[s.verification_status].tone}>{SOURCE_BADGE[s.verification_status].label}</Badge> {s.clause_ref ?? ''}</>}
                checked={state.source_ids.includes(s.id)}
                onChange={(e) => patch({ source_ids: e.target.checked ? [...state.source_ids, s.id] : state.source_ids.filter((x) => x !== s.id) })}
              />
            ))}
          </div>
        )}
      </Section>

      <Section title={`Requirement checklist (${state.items.length})`}>
        <div className="editor-rows">
          {state.items.map((i) => <ItemCard key={i.uid} i={i} set={setState} sources={sources.filter((s) => state.source_ids.includes(s.id))} others={state.items} />)}
        </div>
        <div className="inline-actions"><Button variant="secondary" size="sm" onClick={() => setState((s) => ({ ...s, items: [...s.items, blankItem()] }))}><Plus size={16} aria-hidden="true" /> Add checklist item</Button></div>
      </Section>

      <Section title={`Processing route (${state.stages.length} stage${state.stages.length === 1 ? '' : 's'})`}>
        <p className="note">Stages are listed in order; the first stage is the start. Mark a stage conditional if it does not apply to every case. Never add a stage the source does not support.</p>
        <div className="editor-rows">
          {state.stages.map((st) => <StageCard key={st.uid} s={st} set={setState} sources={sources.filter((x) => state.source_ids.includes(x.id))} all={state.stages} />)}
        </div>
        <div className="inline-actions"><Button variant="secondary" size="sm" onClick={() => setState((s) => ({ ...s, stages: [...s.stages, blankStage()] }))}><Plus size={16} aria-hidden="true" /> Add stage</Button></div>
      </Section>

      <Section title={`Deadline policy definitions (${state.policies.length})`}>
        <p className="note">Definitions are stored for later phases; nothing is scheduled or enforced yet. Leave the duration empty unless a verified source states it.</p>
        <div className="editor-rows">
          {state.policies.map((p) => <PolicyCard key={p.uid} p={p} set={setState} sources={sources.filter((x) => state.source_ids.includes(x.id))} />)}
        </div>
        <div className="inline-actions"><Button variant="secondary" size="sm" onClick={() => setState((s) => ({ ...s, policies: [...s.policies, blankPolicy()] }))}><Plus size={16} aria-hidden="true" /> Add policy</Button></div>
      </Section>

      <div className="sticky-actions">
        <Button onClick={save} loading={busy}>Save draft</Button>
        <ButtonLink to={`/app/rules/versions/${v.id}`} variant="secondary">Back to version</ButtonLink>
        <Button variant="quiet" onClick={() => navigate('/app/rules')}>Catalogue</Button>
      </div>
    </>
  )
}

export default function RuleEditorPage() {
  const { id = '' } = useParams()
  const { can } = useAuth()
  const [res, reload] = useResource(() => Promise.all([rulesApi.version(id), rulesApi.sources()]), [id])
  if (!can('rules:edit')) return <AccessDenied message="Only a rules administrator can edit rule drafts." />
  return (
    <ResourceView resource={res} reload={reload}>
      {([v, sources]) =>
        v.status !== 'draft' ? (
          <>
            <PageHeader title="This version cannot be edited" crumbs={[{ label: 'Rule catalogue', to: '/app/rules' }, { label: `v${v.version_number}`, to: `/app/rules/versions/${v.id}` }]} />
            <Alert tone="warning" title={`Version ${v.version_number} is ${v.status.replace('_', ' ')}`}>
              Only drafts are editable. To change published content, create a new version from the version page.
            </Alert>
            <ButtonLink to={`/app/rules/versions/${v.id}`} variant="secondary">Open the version</ButtonLink>
          </>
        ) : (
          <Editor initial={v} sources={sources} />
        )
      }
    </ResourceView>
  )
}
