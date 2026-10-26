import { useEffect, useMemo, useState } from 'react'
import { ApiError, type ApiIssue } from '../../lib/api'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { DemoBanner, VerificationNote } from '../../components/ui/Banners'
import { Alert } from '../../components/ui/feedback'
import { applicationsApi } from './applicationsApi'
import { DocumentSlot } from './DocumentSlot'
import { FormItem } from './FormFields'
import { ChecklistPanel, conditionMet, RouteList, Section, SourceList } from './parts'
import { SummaryPanel, DataSummary } from './summary'
import type { ApplicationDetail, ChecklistState } from './types'

type Step = 'details' | 'documents' | 'review'
const STEPS: { key: Step; label: string }[] = [
  { key: 'details', label: 'Details' },
  { key: 'documents', label: 'Documents' },
  { key: 'review', label: 'Review & submit' },
]

const isInput = (i: ChecklistState) => i.item_type === 'form_field' || i.item_type === 'declaration'
const same = (a: unknown, b: unknown) => (a ?? '') === (b ?? '') || (a === false && (b === undefined || b === '' || b === null))

/** What the server stores for a local value: numbers become numbers; cleared/unchecked values become '' (clear). */
function wire(item: ChecklistState, v: unknown): unknown {
  if (item.item_type === 'declaration') return v === true ? true : ''
  if (item.field_kind === 'number' && v !== '' && v !== undefined && v !== null) return Number(v)
  return v ?? ''
}

interface Props {
  detail: ApplicationDetail
  onUpdated: (d: ApplicationDetail) => void
  onSubmitted: (d: ApplicationDetail) => void
  onReload: () => void
}

export function DraftEditor({ detail, onUpdated, onSubmitted, onReload }: Props) {
  const [step, setStep] = useState<Step>('details')
  const [values, setValues] = useState<Record<string, unknown>>(detail.form_data)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved'>('idle')
  const [alert, setAlert] = useState<{ tone: 'error' | 'warning'; message: string; stale?: boolean; rule?: boolean } | null>(null)
  const [blockers, setBlockers] = useState<ApiIssue[]>([])
  const [confirm, setConfirm] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const inputs = detail.checklist_items.filter(isInput)
  const docItems = detail.checklist_items.filter((i) => i.item_type === 'document_upload')
  // Applicability is evaluated from configuration data (applies_when) against the current, possibly unsaved, answers.
  const visible = (i: ChecklistState) => conditionMet(i.applies_when, values)

  const changes = useMemo(() => {
    const out: Record<string, unknown> = {}
    for (const i of inputs) if (!same(values[i.item_key], detail.form_data[i.item_key])) out[i.item_key] = wire(i, values[i.item_key])
    return out
  }, [values, detail.form_data, inputs])
  const dirty = Object.keys(changes).length > 0

  useEffect(() => {
    if (!dirty) return
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  function fail(err: unknown, fallback: string) {
    if (err instanceof ApiError) {
      if (err.code === 'stale_version') return setAlert({ tone: 'warning', message: err.message, stale: true })
      if (err.code === 'rule_version_changed') return setAlert({ tone: 'warning', message: err.message, rule: true })
      if (err.code === 'already_submitted' || err.code === 'not_draft') return setAlert({ tone: 'warning', message: err.message, stale: true })
      return setAlert({ tone: 'error', message: err.message })
    }
    setAlert({ tone: 'error', message: fallback })
  }

  async function save(): Promise<ApplicationDetail | null> {
    if (!dirty) return detail
    setSaveState('saving')
    setAlert(null)
    setErrors({})
    try {
      const next = await applicationsApi.saveDraft(detail.id, changes, detail.row_version)
      onUpdated(next)
      setSaveState('saved')
      return next
    } catch (err) {
      setSaveState('idle')
      if (err instanceof ApiError && err.issues.length) {
        setErrors(Object.fromEntries(err.issues.map((i) => [i.path, i.message])))
        setAlert({ tone: 'error', message: 'Some answers were not accepted. Nothing was saved — please correct the marked fields.' })
      } else fail(err, 'Your changes could not be saved.')
      return null
    }
  }

  async function submit() {
    setSubmitting(true)
    setBlockers([])
    setAlert(null)
    try {
      onSubmitted(await applicationsApi.submit(detail.id, detail.row_version))
    } catch (err) {
      setConfirm(false)
      if (err instanceof ApiError && err.code === 'submission_blocked') {
        setBlockers(err.issues)
        setAlert({ tone: 'error', message: err.message })
      } else fail(err, 'The application could not be submitted. It has not been submitted.')
    } finally {
      setSubmitting(false)
    }
  }

  async function moveToCurrent() {
    try {
      onUpdated(await applicationsApi.rebind(detail.id))
      setAlert(null)
    } catch (err) {
      fail(err, 'The draft could not be updated.')
    }
  }

  const outstanding = detail.checklist_items.filter((i) => i.applicable && i.required && i.status !== 'supplied')

  return (
    <div className="editor">
      {detail.rule_version.is_demo && <DemoBanner text={detail.rule_version.demo_banner} />}
      {detail.current_rule_version_differs && (
        <Alert tone="warning" title="A newer approved configuration applies">
          This draft is bound to configuration version {detail.rule_version.number}, which is no longer the one in force for this category and year. It cannot be submitted until you move it to the current requirements.
          <div className="inline-actions">
            <Button size="sm" variant="secondary" onClick={moveToCurrent}>
              Update to current requirements
            </Button>
          </div>
        </Alert>
      )}
      {detail.orphaned_data_keys.length > 0 && (
        <Alert tone="info" title="Some earlier answers no longer match the requirements">
          These are kept on record but ignored: {detail.orphaned_data_keys.join(', ')}.
        </Alert>
      )}

      <nav aria-label="Application steps" className="steps">
        <ol>
          {STEPS.map((s, n) => (
            <li key={s.key}>
              <button type="button" className="steps__btn" aria-current={step === s.key ? 'step' : undefined} onClick={() => setStep(s.key)}>
                <span className="steps__num mono">{n + 1}</span> {s.label}
              </button>
            </li>
          ))}
        </ol>
        <p className="steps__save" role="status">
          {saveState === 'saving' ? 'Saving…' : dirty ? 'Unsaved changes' : saveState === 'saved' ? 'All changes saved' : `Draft · ${detail.checklist.required_supplied} of ${detail.checklist.required_total} required items supplied`}
        </p>
      </nav>

      {alert && (
        <Alert tone={alert.tone}>
          {alert.message}
          {alert.stale && (
            <div className="inline-actions">
              <Button size="sm" variant="secondary" onClick={onReload}>
                Reload latest version
              </Button>
            </div>
          )}
        </Alert>
      )}

      {step === 'details' && (
        <>
          <SummaryPanel detail={detail} />
          <Section title="Application details" aside={<VerificationNote verification={detail.rule_version.verification} />}>
            {inputs.filter(visible).length === 0 ? (
              <p className="note">This configuration asks for no further details. Continue to Documents.</p>
            ) : (
              <div className="form-grid">
                {inputs.filter(visible).map((i) => (
                  <FormItem key={i.item_key} item={i} value={values[i.item_key]} error={errors[i.item_key] ?? (i.status === 'invalid' ? i.error : null)} onChange={(v) => setValues((cur) => ({ ...cur, [i.item_key]: v }))} />
                ))}
              </div>
            )}
          </Section>
          <div className="sticky-actions">
            <Button onClick={save} loading={saveState === 'saving'} disabled={!dirty}>
              Save draft
            </Button>
            <Button variant="secondary" onClick={() => setStep('documents')}>
              Continue to documents
            </Button>
          </div>
        </>
      )}

      {step === 'documents' && (
        <>
          <Section title="Supporting documents">
            <p className="note">Each slot belongs to a requirement. Uploading records the file and its details; it does not verify the document. Files are stored privately and checked by the server.</p>
            {dirty && <Alert tone="info">You have unsaved answers on the Details step. File uploads are saved immediately; answers need “Save draft”.</Alert>}
            {docItems.length === 0 ? (
              <p className="note">This configuration asks for no documents.</p>
            ) : (
              <div className="slots">
                {docItems.filter(visible).map((i) => (
                  <DocumentSlot key={i.item_key} applicationId={detail.id} item={i} documents={detail.documents} editable onUpdated={onUpdated} />
                ))}
              </div>
            )}
          </Section>
          <div className="sticky-actions">
            <Button variant="secondary" onClick={() => setStep('details')}>
              Back
            </Button>
            <Button onClick={() => setStep('review')}>Continue to review</Button>
          </div>
        </>
      )}

      {step === 'review' && (
        <>
          <SummaryPanel detail={detail} />
          <Section title="What you are submitting">
            <DataSummary detail={detail} values={detail.form_data} />
          </Section>
          <Section title="Requirement checklist">
            <ChecklistPanel items={detail.checklist_items} />
          </Section>
          <Section title="Configuration applied">
            <p className="note">
              <span className="mono">
                {detail.rule_version.rule_set_key} · version {detail.rule_version.number}
              </span>{' '}
              will be recorded with your submission. Later changes to the rules will not alter it.
            </p>
            <SourceList sources={detail.rule_version.sources} />
            <RouteList stages={detail.rule_version.route} />
          </Section>

          {dirty && <Alert tone="warning">You have unsaved changes. Save the draft before submitting so that what you review is what is submitted.</Alert>}
          {outstanding.length > 0 ? (
            <Alert tone="warning" title={`${outstanding.length} required item${outstanding.length > 1 ? 's' : ''} still to supply`}>
              <ul className="issue-list">
                {outstanding.map((i) => (
                  <li key={i.item_key}>{i.label}</li>
                ))}
              </ul>
              You can submit only when every required item has been supplied.
            </Alert>
          ) : (
            !dirty && <Alert tone="success">Every required item has been supplied. Review the summary above, then submit.</Alert>
          )}
          {blockers.length > 0 && (
            <Alert tone="error" title="The server did not accept the submission">
              <ul className="issue-list">
                {blockers.map((b) => (
                  <li key={b.path + b.code}>{b.message}</li>
                ))}
              </ul>
            </Alert>
          )}
          <div className="sticky-actions">
            <Button variant="secondary" onClick={() => setStep('documents')}>
              Back
            </Button>
            {dirty && (
              <Button variant="secondary" onClick={save} loading={saveState === 'saving'}>
                Save draft
              </Button>
            )}
            <Button onClick={() => setConfirm(true)} disabled={dirty || outstanding.length > 0 || detail.current_rule_version_differs}>
              Submit application
            </Button>
          </div>
        </>
      )}

      <ConfirmDialog open={confirm} title="Submit this application?" confirmLabel="Submit application" busy={submitting} onConfirm={submit} onCancel={() => setConfirm(false)}>
        <p>
          You are about to submit <strong className="mono">{detail.reference}</strong> for {detail.category.name}, {detail.academic_year}.
        </p>
        <p>After submission you cannot edit the answers or change the documents. Configuration version {detail.rule_version.number} will be recorded with it.</p>
      </ConfirmDialog>
    </div>
  )
}
