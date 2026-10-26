import { useState } from 'react'
import { Button } from '../../components/ui/Button'
import { Alert } from '../../components/ui/feedback'
import { SelectField, TextAreaField, TextField } from '../../components/ui/Field'
import type { ApiError } from '../../lib/api'
import type { FindingContext } from '../evidence/DocumentViewer'
import { issuesByPath } from './useRunner'
import type { FindingInput } from './scrutinyApi'
import type { CaseDetail, ReviewFinding } from './types'

export interface FormTarget {
  context?: FindingContext
  finding?: ReviewFinding
}

/** Records or edits a reviewer finding. The wording for the institution is a separate, deliberate field: nothing internal is copied into it. */
export function FindingForm({
  detail, target, busy, error, onSubmit, onCancel,
}: {
  detail: CaseDetail
  target: FormTarget
  busy: boolean
  error: ApiError | null
  onSubmit: (input: FindingInput, rowVersion?: number) => void
  onCancel: () => void
}) {
  const f = target.finding
  const ctx = target.context
  const [issueType, setIssueType] = useState(f?.issue_type ?? 'incomplete_information')
  const [description, setDescription] = useState(f?.description ?? '')
  const [itemKey, setItemKey] = useState(f?.item_key ?? ctx?.item_key ?? '')
  const [linkDoc, setLinkDoc] = useState<boolean>(f ? !!f.document_id : !!ctx?.document_id || !ctx)
  const [documentId, setDocumentId] = useState<string>(f?.document_id ?? ctx?.document_id ?? '')
  const [page, setPage] = useState<string>(f?.page_number ? String(f.page_number) : ctx?.page ? String(ctx.page) : '')
  const [eligible, setEligible] = useState((f?.visibility ?? 'INTERNAL_ONLY') === 'ELIGIBLE_FOR_NOTICE')
  const [institutionText, setInstitutionText] = useState(f?.institution_text ?? '')
  const [correction, setCorrection] = useState(f?.requested_correction ?? '')
  const [sourceKey, setSourceKey] = useState(f?.source_ref?.key ?? '')
  const errs = issuesByPath(error)

  const row = detail.checklist.find((c) => c.item_key === itemKey)
  const effectiveDoc = linkDoc && itemKey ? documentId || row?.document_id || '' : ''
  const docVersion = effectiveDoc === (row?.document_id ?? '') ? row?.document_version : effectiveDoc === ctx?.document_id ? ctx?.version_number : f?.document_version

  function submit(e: React.FormEvent) {
    e.preventDefault()
    onSubmit(
      {
        issue_type: issueType,
        description,
        item_key: itemKey || null,
        document_id: effectiveDoc || null,
        page_number: effectiveDoc && page ? Number(page) : null,
        institution_text: eligible ? institutionText : institutionText || null,
        requested_correction: eligible ? correction : correction || null,
        source_ref_key: sourceKey || null,
        visibility: eligible ? 'ELIGIBLE_FOR_NOTICE' : 'INTERNAL_ONLY',
        preliminary_finding_id: f?.preliminary_finding_id ?? ctx?.preliminary_finding_id ?? null,
      },
      f?.row_version,
    )
  }

  return (
    <form className="panel finding-form" onSubmit={submit} aria-label={f ? `Edit finding ${f.sequence}` : 'Record a finding'}>
      <div className="panel__head">
        <h2 className="panel__title">{f ? `Edit finding ${f.sequence}` : 'Record a finding'}</h2>
      </div>
      <p className="note">
        A finding is your own conclusion. It is not a notice and is not shown to the institution unless you make it eligible and a notice that includes it is issued by an authorised person.
      </p>
      {error && !Object.keys(errs).length && <Alert tone="error">{error.message}</Alert>}
      <div className="form-grid">
        <SelectField label="Type of issue" value={issueType} onChange={(e) => setIssueType(e.target.value)} options={Object.entries(detail.issue_types).map(([value, label]) => ({ value, label }))} error={errs.issue_type} />
        <SelectField
          label="Requirement concerned"
          optional
          value={itemKey}
          onChange={(e) => { setItemKey(e.target.value); setDocumentId('') }}
          options={detail.checklist.map((c) => ({ value: c.item_key, label: c.label }))}
          placeholder="Not tied to one requirement"
          error={errs.item_key}
        />
      </div>
      {itemKey && row?.item_type === 'document_upload' && (
        <fieldset className="evidence-link">
          <legend>Evidence this finding refers to</legend>
          {row.document_id || ctx?.document_id || f?.document_id ? (
            <>
              <label className="check-inline">
                <input type="checkbox" checked={linkDoc} onChange={(e) => setLinkDoc(e.target.checked)} /> Link to the document version{' '}
                <strong className="mono">v{docVersion ?? row.document_version}</strong>
                {f?.document_superseded ? ' (since replaced)' : ''}
              </label>
              {linkDoc && (
                <TextField label="Page" optional type="number" min={1} value={page} onChange={(e) => setPage(e.target.value)} error={errs.page_number} hint="Leave empty if the issue concerns the whole document." />
              )}
              {errs.document_id && <p className="field__error">{errs.document_id}</p>}
            </>
          ) : (
            <p className="note">No file was submitted for this requirement, so there is no version to link.</p>
          )}
        </fieldset>
      )}
      <TextAreaField label="Your description (internal — never shown to the institution)" rows={4} value={description} onChange={(e) => setDescription(e.target.value)} error={errs.description} />

      <fieldset className="visibility">
        <legend>Can this go into a notice?</legend>
        <label className="check-inline">
          <input type="radio" name="vis" checked={!eligible} onChange={() => setEligible(false)} /> Internal only
        </label>
        <label className="check-inline">
          <input type="radio" name="vis" checked={eligible} onChange={() => setEligible(true)} /> May be included in a notice to the institution
        </label>
      </fieldset>
      {eligible && (
        <div className="institution-wording">
          <p className="note">Write what the institution will read. Do not paste internal remarks.</p>
          <TextAreaField label="What the institution is told" rows={3} value={institutionText} onChange={(e) => setInstitutionText(e.target.value)} error={errs.institution_text} />
          <TextAreaField label="What the institution is asked to supply or correct" rows={3} value={correction} onChange={(e) => setCorrection(e.target.value)} error={errs.requested_correction} />
        </div>
      )}
      <SelectField
        label="Source reference"
        optional
        value={sourceKey}
        onChange={(e) => setSourceKey(e.target.value)}
        placeholder="None established — flag for source verification"
        options={detail.source_options.map((o) => ({ value: o.key, label: `${o.label}${o.clause_ref ? ` — ${o.clause_ref}` : ''}${o.verification_status === 'VERIFIED_OFFICIAL' ? '' : ' (not verified as official)'}` }))}
        hint="Chosen from the rule version this application was submitted under. A reference is never typed in or guessed; if none applies, leave it empty."
        error={errs.source_ref_key}
      />
      <div className="action-bar">
        <Button type="submit" loading={busy}>
          {f ? 'Save changes' : 'Record finding'}
        </Button>
        <Button variant="quiet" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
