import { useEffect, useMemo, useRef, useState } from 'react'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Alert } from '../../components/ui/feedback'
import { CheckboxField, TextAreaField, TextField } from '../../components/ui/Field'
import { ApiError } from '../../lib/api'
import { formatDate } from '../../lib/format'
import { Section } from '../applications/parts'
import { FindingStateBadge, NoticeStatusBadge, PolicyView, SourceRefView, StageTag } from './parts'
import { scrutinyApi } from './scrutinyApi'
import { issuesByPath } from './useRunner'
import type { CaseDetail, NoticePreview, ReviewFinding, StaffNotice } from './types'

const newKey = () => (typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `k-${Date.now()}-${Math.random().toString(36).slice(2)}`)

// ---- the draft editor (reviewer) ---------------------------------------------------------------------------------------

function DraftEditor({ notice, spare, busy, error, onSave, onClose }: {
  notice: StaffNotice
  spare: ReviewFinding[]
  busy: boolean
  error: ApiError | null
  onSave: (body: { summary: string; items: { id: string; title: string; description: string; requested_correction: string }[]; add_finding_ids: string[] }) => void
  onClose: () => void
}) {
  const [summary, setSummary] = useState(notice.summary)
  const [items, setItems] = useState(notice.items.map((i) => ({ id: i.id, title: i.title, description: i.description, requested_correction: i.requested_correction })))
  const [adding, setAdding] = useState<string[]>([])
  const set = (id: string, patch: Partial<(typeof items)[number]>) => setItems((cur) => cur.map((i) => (i.id === id ? { ...i, ...patch } : i)))
  const errs = issuesByPath(error)
  return (
    <form
      className="notice-editor"
      onSubmit={(e) => {
        e.preventDefault()
        onSave({ summary, items, add_finding_ids: adding })
      }}
    >
      <Alert tone="warning">This text is what the institution will read if the notice is issued. Internal remarks do not belong here.</Alert>
      <TextAreaField label="Explanation to the institution" rows={3} value={summary} onChange={(e) => setSummary(e.target.value)} error={errs.summary} />
      {items.map((it, n) => (
        <fieldset key={it.id} className="row-card">
          <legend>Item {n + 1}</legend>
          <TextField label="Title" value={it.title} onChange={(e) => set(it.id, { title: e.target.value })} />
          <TextAreaField label="What is deficient or requested" rows={3} value={it.description} onChange={(e) => set(it.id, { description: e.target.value })} />
          <TextAreaField label="Correction or evidence expected" rows={3} value={it.requested_correction} onChange={(e) => set(it.id, { requested_correction: e.target.value })} error={errs[it.id]} />
          <Button size="sm" variant="quiet" disabled={items.length <= 1} onClick={() => setItems((cur) => cur.filter((x) => x.id !== it.id))}>
            Remove this item
          </Button>
        </fieldset>
      ))}
      {spare.length > 0 && (
        <fieldset className="row-card">
          <legend>Add findings that may go into a notice</legend>
          {spare.map((f) => (
            <CheckboxField
              key={f.id}
              label={`Finding ${f.sequence}: ${f.institution_text ?? f.description}`}
              checked={adding.includes(f.id)}
              onChange={(e) => setAdding((a) => (e.target.checked ? [...a, f.id] : a.filter((x) => x !== f.id)))}
            />
          ))}
        </fieldset>
      )}
      <div className="action-bar">
        <Button type="submit" loading={busy}>
          Save draft
        </Button>
        <Button variant="quiet" onClick={onClose} disabled={busy}>
          Close
        </Button>
      </div>
    </form>
  )
}

// ---- the issuer's inspection (the only way a notice is issued) ------------------------------------------------------------

function IssuePanel({ notice, busy, error, onIssue, onReturn }: {
  notice: StaffNotice
  busy: boolean
  error: ApiError | null
  onIssue: (body: { content_hash: string; issue_reason: string; acknowledge_unsourced: boolean; idempotency_key: string; row_version: number }) => Promise<boolean>
  onReturn: (reason: string) => Promise<boolean>
}) {
  const [pv, setPv] = useState<NoticePreview | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const [ack, setAck] = useState(false)
  const [read, setRead] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [returning, setReturning] = useState(false)
  const [returnReason, setReturnReason] = useState('')
  // One identifier per inspection: if the request is repeated (a double click, a retry after a dropped connection) the server
  // recognises it and returns the same notice instead of issuing a second one.
  const key = useRef(newKey())

  useEffect(() => {
    let live = true
    scrutinyApi.previewNotice(notice.id).then((p) => live && setPv(p)).catch((e) => live && setLoadError(e instanceof ApiError ? e.message : 'The notice could not be loaded.'))
    return () => { live = false }
  }, [notice.id, notice.row_version])

  if (loadError) return <Alert tone="error">{loadError}</Alert>
  if (!pv) return <p role="status">Loading the notice as it would be issued…</p>
  const c = pv.content
  const needsAck = pv.unsourced_positions.length > 0
  const ready = pv.can_issue && reason.trim().length >= 5 && (!needsAck || ack) && read

  return (
    <div className="issue-panel">
      <Alert tone="warning" title="Inspect before you decide">
        Below is exactly what the institution would receive. Issuing is a formal act: it is recorded under your name and role, the institution sees it at once, and it cannot be edited or recalled.
      </Alert>
      {pv.blockers.length > 0 && (
        <Alert tone="error" title="This notice cannot be issued yet">
          <ul>{pv.blockers.map((b) => <li key={b.code}>{b.message}</li>)}</ul>
        </Alert>
      )}
      <article className="notice-preview" aria-label="Notice as it would be issued">
        {c.demo_notice && <p className="demo-banner">{c.demo_notice}</p>}
        <p className="eyebrow">{c.template ? c.template.label : 'No approved template is configured'}</p>
        <h3 className="panel__sub">Deficiency notice {c.reference}</h3>
        <p className="mono muted">
          {c.application.reference} · {c.application.institution} · {c.application.category} · {c.application.academic_year}
          {c.previous_notice_reference ? ` · follows ${c.previous_notice_reference}` : ''}
        </p>
        <p>{c.summary}</p>
        <ol className="notice-items">
          {c.items.map((i) => (
            <li key={i.position}>
              <p>
                <strong>{i.title}</strong>
              </p>
              <p>{i.description}</p>
              <p>
                <strong>Expected:</strong> {i.requested_correction}
              </p>
              <p className="checklist__meta">
                <SourceRefView source={i.source_ref} status={i.source_ref_status} />
                {i.document_version ? <span className="mono"> · concerns version {i.document_version}{i.page_number ? `, page ${i.page_number}` : ''}</span> : ''}
              </p>
            </li>
          ))}
        </ol>
        <PolicyView policy={pv.policy} />
        <p className="checklist__meta mono">Content fingerprint {pv.content_hash.slice(0, 16)}… · you confirm exactly this content</p>
      </article>

      {notice.status === 'IN_REVIEW' && (
        <div className="issue-decision">
          <TextAreaField label="Your reason for issuing (recorded in the audit trail; not shown to the institution)" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
          {needsAck && (
            <CheckboxField
              label={`Item${pv.unsourced_positions.length > 1 ? 's' : ''} ${pv.unsourced_positions.join(', ')} ${pv.unsourced_positions.length > 1 ? 'have' : 'has'} no established source reference. I am issuing without one.`}
              checked={ack}
              onChange={(e) => setAck(e.target.checked)}
            />
          )}
          <CheckboxField label="I have read this notice and inspected the evidence behind it." checked={read} onChange={(e) => setRead(e.target.checked)} />
          <div className="action-bar">
            <Button disabled={!ready} onClick={() => setConfirm(true)}>
              Issue this notice…
            </Button>
            <Button variant="secondary" onClick={() => setReturning(true)}>
              Return to the reviewer…
            </Button>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={confirm}
        title="Issue this formal deficiency notice?"
        confirmLabel="Issue notice"
        busy={busy}
        onCancel={() => setConfirm(false)}
        onConfirm={async () => {
          const ok = await onIssue({ content_hash: pv.content_hash, issue_reason: reason.trim(), acknowledge_unsourced: ack, idempotency_key: key.current, row_version: pv.row_version })
          if (ok) setConfirm(false)
        }}
      >
        <p>
          You are about to issue notice <strong className="mono">{c.reference}</strong> to <strong>{c.application.institution}</strong>.
        </p>
        <p className="note">It becomes visible to the institution immediately. The issue is recorded with your name, role, the time and the exact content you inspected. If this request is repeated, the notice is still issued only once.</p>
        {error && <Alert tone="error">{error.message}</Alert>}
      </ConfirmDialog>
      <ConfirmDialog
        open={returning}
        title="Return this notice to the reviewer"
        confirmLabel="Return notice"
        busy={busy}
        confirmDisabled={returnReason.trim().length < 5}
        onCancel={() => setReturning(false)}
        onConfirm={async () => {
          if (await onReturn(returnReason.trim())) setReturning(false)
        }}
      >
        <TextAreaField label="What needs to change?" rows={3} value={returnReason} onChange={(e) => setReturnReason(e.target.value)} />
        {error && <Alert tone="error">{error.message}</Alert>}
      </ConfirmDialog>
    </div>
  )
}

// ---- one notice ---------------------------------------------------------------------------------------------------

function kindOf(n: StaffNotice) {
  return n.status === 'DRAFT' ? 'draft' : n.status === 'IN_REVIEW' ? 'in-review' : n.status === 'WITHDRAWN' ? 'draft' : 'notice'
}

function NoticeCard({ n, detail, ctx }: { n: StaffNotice; detail: CaseDetail; ctx: Ctx }) {
  const [editing, setEditing] = useState(n.status === 'DRAFT' && ctx.openEditor === n.id)
  const [inspect, setInspect] = useState(false)
  const [dialog, setDialog] = useState<'submit' | 'withdraw' | null>(null)
  const [reason, setReason] = useState('')
  const allowed = detail.allowed_actions
  const findingByItem = useMemo(() => new Map(detail.findings.map((f) => [f.id, f])), [detail.findings])
  const spare = detail.findings.filter((f) => (f.state === 'OPEN' || f.state === 'UNRESOLVED') && f.visibility === 'ELIGIBLE_FOR_NOTICE' && !n.items.some((i) => i.finding_id === f.id))

  return (
    <article className="notice-card" data-status={n.status}>
      <div className="finding__head">
        <h3 className="panel__sub">
          Notice {n.reference} <span className="muted">· no. {n.sequence}{n.previous_reference ? `, follows ${n.previous_reference}` : ''}</span>
        </h3>
        <span className="finding__tags">
          <StageTag kind={kindOf(n)} />
          <NoticeStatusBadge status={n.status} />
        </span>
      </div>
      <p className="checklist__meta mono">
        Prepared by {n.prepared_by} in round {n.round_number}
        {n.issued_at ? ` · issued ${formatDate(n.issued_at)} by ${n.issued_by} (${n.issuer_role?.replace(/_/g, ' ')})` : ''}
      </p>
      {n.summary && <p>{n.summary}</p>}
      {n.returned_reason && n.status === 'DRAFT' && <Alert tone="warning" title="Returned by the issuer">{n.returned_reason}</Alert>}
      {n.withdrawn_reason && <p className="note">Withdrawn: {n.withdrawn_reason}</p>}
      {n.status === 'ISSUED' || n.status === 'RESPONSE_RECEIVED' || n.status === 'CLOSED' ? (
        <>
          <p className="checklist__meta">
            {n.response_due_at ? <>Response due <strong className="mono">{formatDate(n.response_due_at)}</strong></> : <strong>No response deadline was established.</strong>}
            {n.template?.demo ? ' · DEMO ONLY template' : ''}
          </p>
          {n.policy_resolution && <PolicyView policy={n.policy_resolution} />}
          {n.issue_reason && <p className="note"><strong>Issuer’s recorded reason (internal):</strong> {n.issue_reason}</p>}
        </>
      ) : null}

      <ol className="notice-items">
        {n.items.map((i) => {
          const f = findingByItem.get(i.finding_id)
          return (
            <li key={i.id}>
              <p>
                <strong>{i.title}</strong> <FindingStateBadge state={(f?.state ?? 'OPEN') as ReviewFinding['state']} />
              </p>
              <p>{i.description}</p>
              <p>
                <strong>Expected:</strong> {i.requested_correction}
              </p>
              <p className="checklist__meta">
                <SourceRefView source={i.source_ref} status={i.source_ref_status} />
                {i.document_version ? (
                  <span className="mono">
                    {' '}
                    · concerns version {i.document_version}
                    {i.document_current_version && i.document_current_version !== i.document_version ? ` (current is ${i.document_current_version})` : ''}
                    {i.page_number ? `, page ${i.page_number}` : ''}
                  </span>
                ) : null}
              </p>
              {(n.status === 'RESPONSE_RECEIVED' || n.status === 'CLOSED' || n.status === 'ISSUED') && (
                <div className="response-block">
                  <StageTag kind="response" />
                  <p className="checklist__meta">
                    {i.response_state === 'NOT_RESPONDED' ? 'No response to this item.' : i.response_state === 'DRAFT' ? 'A response is being drafted (not yet submitted).' : 'Response submitted.'}
                  </p>
                  {i.response_explanation && <blockquote>{i.response_explanation}</blockquote>}
                  {i.response_documents.length > 0 && (
                    <ul className="plain-list">
                      {i.response_documents.map((d) => (
                        <li key={d.document_id}>
                          <span className="mono">{d.filename} · version {d.version_number}</span> {d.current ? '' : '(since replaced)'} — {d.state_label}{' '}
                          <button type="button" className="linklike" onClick={() => ctx.openDocument(i.item_key)}>
                            Inspect in the evidence
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                  {f && (f.state === 'RESOLVED' || f.state === 'UNRESOLVED' || f.state === 'NOT_SUPPORTED') && (
                    <div className="rescrutiny-result">
                      <StageTag kind="rescrutiny" />
                      <FindingStateBadge state={f.state} />
                      <p>{f.state_reason}</p>
                    </div>
                  )}
                </div>
              )}
            </li>
          )
        })}
      </ol>

      {n.status === 'DRAFT' && allowed.includes('submit_notice') && !editing && (
        <div className="action-bar">
          <Button variant="secondary" onClick={() => setEditing(true)}>
            Edit the wording
          </Button>
          <Button onClick={() => setDialog('submit')}>Submit for an issuing decision…</Button>
          <Button variant="quiet" onClick={() => { setReason(''); setDialog('withdraw') }}>
            Withdraw this draft…
          </Button>
        </div>
      )}
      {n.status === 'DRAFT' && editing && (
        <DraftEditor
          notice={n}
          spare={spare}
          busy={ctx.busy}
          error={ctx.error}
          onClose={() => setEditing(false)}
          onSave={async (body) => {
            if (await ctx.run(() => scrutinyApi.updateNotice(n.id, { row_version: n.row_version, ...body }))) setEditing(false)
          }}
        />
      )}
      {n.status === 'IN_REVIEW' && allowed.includes('withdraw_notice') && (
        <div className="action-bar">
          <Button variant="quiet" onClick={() => { setReason(''); setDialog('withdraw') }}>
            Withdraw this prepared notice…
          </Button>
        </div>
      )}
      {n.status === 'IN_REVIEW' && allowed.includes('issue_notice') && (
        <>
          <div className="action-bar">
            <Button onClick={() => setInspect((v) => !v)} aria-expanded={inspect}>
              {inspect ? 'Close the inspection' : 'Inspect and decide…'}
            </Button>
          </div>
          {inspect && (
            <IssuePanel
              notice={n}
              busy={ctx.busy}
              error={ctx.error}
              onIssue={(body) => ctx.run(() => scrutinyApi.issueNotice(n.id, body))}
              onReturn={(r) => ctx.run(() => scrutinyApi.returnNotice(n.id, r))}
            />
          )}
        </>
      )}

      <ConfirmDialog
        open={dialog === 'submit'}
        title="Submit this notice for an issuing decision?"
        confirmLabel="Submit for decision"
        busy={ctx.busy}
        onCancel={() => setDialog(null)}
        onConfirm={async () => {
          if (await ctx.run(() => scrutinyApi.submitNotice(n.id, n.row_version))) setDialog(null)
        }}
      >
        <p>An authorised issuer will inspect it and decide. This does not issue the notice, and the institution cannot see it.</p>
        {ctx.error && <Alert tone="error">{ctx.error.message}</Alert>}
      </ConfirmDialog>
      <ConfirmDialog
        open={dialog === 'withdraw'}
        title="Withdraw this notice?"
        confirmLabel="Withdraw notice"
        danger
        busy={ctx.busy}
        confirmDisabled={reason.trim().length < 5}
        onCancel={() => setDialog(null)}
        onConfirm={async () => {
          if (await ctx.run(() => scrutinyApi.withdrawNotice(n.id, reason.trim()))) setDialog(null)
        }}
      >
        <p className="note">The notice was never issued, so the institution is not told. Its findings stay open.</p>
        <TextAreaField label="Why is it being withdrawn?" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
        {ctx.error && <Alert tone="error">{ctx.error.message}</Alert>}
      </ConfirmDialog>
    </article>
  )
}

export interface Ctx {
  busy: boolean
  error: ApiError | null
  run: (fn: () => Promise<CaseDetail>) => Promise<boolean>
  openDocument: (itemKey: string | null) => void
  openEditor: string | null
}

export function NoticesPanel({ detail, ctx }: { detail: CaseDetail; ctx: Ctx }) {
  const notices = [...detail.notices].reverse()
  return (
    <Section title={`Notices (${detail.notices.length})`}>
      <Alert tone="info">
        A <strong>draft</strong> or <strong>prepared</strong> notice is internal. Only a notice issued by an authorised issuer is a <strong>formal deficiency notice</strong>, and only then does the institution see it.
      </Alert>
      {detail.notices.length === 0 && (
        <p className="note">
          No notice has been prepared.
          {detail.allowed_actions.includes('prepare_notice') && ' Select findings that may go into a notice on the Findings tab to prepare one.'}
        </p>
      )}
      <div className="notice-list">
        {notices.map((n) => (
          <NoticeCard key={n.id} n={n} detail={detail} ctx={ctx} />
        ))}
      </div>
    </Section>
  )
}
