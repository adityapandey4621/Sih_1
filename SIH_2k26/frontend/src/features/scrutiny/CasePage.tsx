import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Button } from '../../components/ui/Button'
import { Alert, EmptyState, ErrorState, LoadingState } from '../../components/ui/feedback'
import { ApiError } from '../../lib/api'
import { usePolling } from '../../lib/usePolling'
import type { FindingContext } from '../evidence/DocumentViewer'
import { PageHeader } from '../shell/PageHeader'
import '../applications/applications.css'
import { AssignmentPanel, CompletionPanel, HandoffPanel, HistoryPanel, SummaryPanel } from './CasePanels'
import { EvidenceTab } from './EvidenceTab'
import { FindingForm, type FormTarget } from './FindingForm'
import { FindingsPanel } from './FindingsPanel'
import { NoticesPanel } from './NoticesPanel'
import { CaseStateBadge, StageTag } from './parts'
import { scrutinyApi, type FindingInput } from './scrutinyApi'
import type { CaseDetail } from './types'
import { useRunner } from './useRunner'
import './scrutiny.css'

type Tab = 'evidence' | 'findings' | 'notices' | 'case'

function View({ initial, reload }: { initial: CaseDetail; reload: () => Promise<CaseDetail | null> }) {
  const [detail, setDetail] = useState(initial)
  const staff = detail.scope.includes('reviewer') || detail.scope.includes('issuer')
  const [tab, setTab] = useState<Tab>(staff ? 'evidence' : 'case')
  const [form, setForm] = useState<FormTarget | null>(null)
  const [focusItem, setFocusItem] = useState<string | null>(null)
  const [evidenceKey, setEvidenceKey] = useState(0)
  const [openEditor, setOpenEditor] = useState<string | null>(null)

  const refetch = useCallback(() => void reload().then((d) => d && setDetail(d)), [reload])
  const { busy, error, run, clear } = useRunner(setDetail, refetch)

  // While files of this case are still being read, keep the case and the evidence fresh.
  const processing = detail.queue.processing.still_processing > 0
  usePolling(async () => {
    const d = await reload()
    if (d) {
      setDetail(d)
      setEvidenceKey((k) => k + 1)
    }
  }, processing)

  const a = detail.allowed_actions
  const q = detail.queue
  const cases: [Tab, string][] = []
  if (staff) cases.push(['evidence', 'Evidence'], ['findings', `Findings (${detail.findings.length})`], ['notices', `Notices (${detail.notices.length})`])
  cases.push(['case', 'Case, assignment and history'])

  function openForm(ctx?: FindingContext, finding?: FormTarget['finding']) {
    clear()
    setForm({ context: ctx, finding })
    setTab('findings')
  }
  async function saveFinding(input: FindingInput, rowVersion?: number) {
    const ok = await run(() => (form?.finding ? scrutinyApi.editFinding(q.id, form.finding.id, { ...input, row_version: rowVersion ?? form.finding.row_version }) : scrutinyApi.recordFinding(q.id, input)))
    if (ok) setForm(null)
  }

  return (
    <>
      <PageHeader
        title={q.reference}
        description={`${q.institution_name} · ${q.category_name} · ${q.academic_year}`}
        crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Scrutiny queue', to: '/app/scrutiny' }, { label: q.reference }]}
        actions={<CaseStateBadge state={q.state} label={q.state_label} />}
      />
      <div className="case-banner">
        {q.state === 'SCRUTINY_COMPLETED' && <StageTag kind={detail.handoff?.status === 'QUEUED' ? 'next-stage' : 'completed'} />}
        <p className="note">{detail.not_an_approval}</p>
        {a.includes('start') && (
          <Button loading={busy} onClick={() => void run(() => scrutinyApi.start(q.id, q.row_version))}>
            {q.state === 'AWAITING_RESCRUTINY' ? 'Start re-scrutiny' : 'Start scrutiny'}
          </Button>
        )}
      </div>
      {error && !['stale_version', 'invalid_transition'].includes(error.code ?? '') && !form && (
        <Alert tone="error" title="That did not go through">
          {error.message}
        </Alert>
      )}
      {error && ['stale_version', 'invalid_transition'].includes(error.code ?? '') && (
        <Alert tone="warning" title="The case changed">
          {error.message} The latest version has been loaded; review it and try again.
        </Alert>
      )}

      <div className="tabbar case-tabs" role="tablist" aria-label="Case sections">
        {cases.map(([k, label]) => (
          <button key={k} type="button" role="tab" className="tabbar__btn" aria-selected={tab === k} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'evidence' && staff && (
        <EvidenceTab caseId={q.id} focusItem={focusItem} refreshKey={evidenceKey} onRecordFinding={a.includes('record_finding') ? (ctx) => openForm(ctx) : undefined} />
      )}

      {tab === 'findings' && staff && (
        <>
          {form && <FindingForm detail={detail} target={form} busy={busy} error={error} onSubmit={(i, v) => void saveFinding(i, v)} onCancel={() => setForm(null)} />}
          <FindingsPanel
            findings={detail.findings}
            allowed={a}
            busy={busy}
            error={error}
            onRecord={() => openForm()}
            onEdit={(f) => openForm(undefined, f)}
            onConclude={(f, state, reason) => run(() => scrutinyApi.conclude(q.id, f.id, state, reason, f.row_version))}
            onPrepareNotice={async (ids) => {
              if (await run(() => scrutinyApi.createNotice(q.id, ids, ''))) {
                setTab('notices')
                setOpenEditor('latest')
              }
            }}
            onOpenEvidence={(f) => { setFocusItem(f.item_key); setTab('evidence') }}
          />
          <CompletionPanel detail={detail} run={run} busy={busy} error={error} />
        </>
      )}

      {tab === 'notices' && staff && (
        <NoticesPanel
          detail={detail}
          ctx={{
            busy, error, run, openEditor: openEditor === 'latest' ? detail.notices[detail.notices.length - 1]?.id ?? null : openEditor,
            openDocument: (key) => { setFocusItem(key); setTab('evidence') },
          }}
        />
      )}

      {tab === 'case' && (
        <>
          <SummaryPanel detail={detail} />
          {(detail.scope.includes('coordinator') || detail.assignments.length > 0) && <AssignmentPanel detail={detail} run={run} busy={busy} error={error} />}
          <HandoffPanel detail={detail} run={run} busy={busy} error={error} />
          <HistoryPanel detail={detail} run={run} busy={busy} />
        </>
      )}
    </>
  )
}

export default function ScrutinyCasePage() {
  const { id = '' } = useParams()
  const [detail, setDetail] = useState<CaseDetail | null>(null)
  const [error, setError] = useState<{ message: string; missing: boolean } | null>(null)

  const load = useCallback(async () => {
    try {
      const d = await scrutinyApi.detail(id)
      setError(null)
      return d
    } catch (e) {
      setError({ message: e instanceof ApiError ? e.message : 'The case could not be loaded.', missing: e instanceof ApiError && e.status === 404 })
      return null
    }
  }, [id])
  useEffect(() => {
    void load().then(setDetail)
  }, [load])

  if (error && !detail) {
    return error.missing ? (
      <EmptyState title="Case not found">It may not exist, or it is not within your scope. Cases outside your assignment are not shown to you.</EmptyState>
    ) : (
      <ErrorState message={error.message} onRetry={() => void load().then(setDetail)} />
    )
  }
  if (!detail) return <LoadingState label="Loading the case…" />
  return <View key={detail.queue.id} initial={detail} reload={load} />
}
