import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Alert, EmptyState, ErrorState, LoadingState } from '../../components/ui/feedback'
import { ApiError } from '../../lib/api'
import { useAction } from '../committee/useAction'
import { EvidenceTab } from '../scrutiny/EvidenceTab'
import { PageHeader } from '../shell/PageHeader'
import '../applications/applications.css'
import '../scrutiny/scrutiny.css'
import '../committee/committee.css'
import { decisionsApi } from './decisionsApi'
import { DecisionStateBadge } from './DecisionQueuePage'
import { AppealStaffPanel, ClosePanel, CouncilPanel, DecisionPanel, HistoryPanel, IssuePanel, ObservationsPanel, OverviewPanel, type Ctx } from './DecisionPanels'
import type { DecisionCaseDetail } from './types'

type Tab = 'overview' | 'documents' | 'observations' | 'decision' | 'issue' | 'council' | 'appeal' | 'history'

function View({ initial, reload }: { initial: DecisionCaseDetail; reload: () => Promise<DecisionCaseDetail | null> }) {
  const [detail, setDetail] = useState(initial)
  const refetch = useCallback(() => void reload().then((d) => d && setDetail(d)), [reload])
  const { busy, error, run } = useAction<DecisionCaseDetail>(setDetail, refetch)
  const [tab, setTab] = useState<Tab>('overview')
  const loadEvidence = useCallback(() => decisionsApi.evidence(detail.id), [detail.id])
  const ctx: Ctx = { detail, run, busy, error }
  const tabs: [Tab, string][] = [
    ['overview', 'Overview'],
    ['documents', 'Documents'],
    ['observations', 'Bureau observations'],
    ['decision', 'Decision'],
    ['issue', 'Issued outcome'],
    ['council', 'Council'],
    ['appeal', 'Appeal'],
    ['history', 'History'],
  ]
  const stale = error && ['stale_version', 'invalid_transition'].includes(error.code ?? '')
  const a = detail.application
  return (
    <>
      <PageHeader
        title={a.reference}
        description={`${a.institution_name} · ${a.category_name} · ${a.academic_year}`}
        crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Decision queue', to: '/app/executive' }, { label: a.reference }]}
        actions={<DecisionStateBadge state={detail.state} label={detail.state_label} />}
      />
      <p className="note">{detail.not_a_decision}</p>
      {stale && (
        <Alert tone="warning" title="The case changed">
          {error.message} The latest version has been loaded; review it and try again.
        </Alert>
      )}
      <div className="tabbar case-tabs" role="tablist" aria-label="Case sections">
        {tabs.map(([k, label]) => (
          <button key={k} type="button" role="tab" className="tabbar__btn" aria-selected={tab === k} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </div>
      {tab === 'overview' && (
        <>
          <OverviewPanel detail={detail} />
          <ClosePanel ctx={ctx} />
        </>
      )}
      {tab === 'documents' && <EvidenceTab caseId={detail.id} focusItem={null} refreshKey={detail.row_version} load={loadEvidence} />}
      {tab === 'observations' && <ObservationsPanel ctx={ctx} />}
      {tab === 'decision' && <DecisionPanel ctx={ctx} />}
      {tab === 'issue' && <IssuePanel ctx={ctx} />}
      {tab === 'council' && <CouncilPanel ctx={ctx} />}
      {tab === 'appeal' && <AppealStaffPanel detail={detail} />}
      {tab === 'history' && <HistoryPanel detail={detail} />}
    </>
  )
}

export default function DecisionCasePage() {
  const { id = '' } = useParams()
  const [detail, setDetail] = useState<DecisionCaseDetail | null>(null)
  const [error, setError] = useState<{ message: string; missing: boolean } | null>(null)
  const load = useCallback(async () => {
    try {
      const d = await decisionsApi.detail(id)
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
  const retry = useMemo(() => () => void load().then(setDetail), [load])
  if (error && !detail) {
    return error.missing ? <EmptyState title="Case not found">It may not exist, or it is not within your access.</EmptyState> : <ErrorState message={error.message} onRetry={retry} />
  }
  if (!detail) return <LoadingState label="Loading the case…" />
  return <View key={detail.id} initial={detail} reload={load} />
}
