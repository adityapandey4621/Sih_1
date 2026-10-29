import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Alert, EmptyState, ErrorState, LoadingState } from '../../components/ui/feedback'
import { ApiError } from '../../lib/api'
import { PageHeader } from '../shell/PageHeader'
import { EvidenceTab } from '../scrutiny/EvidenceTab'
import '../applications/applications.css'
import '../scrutiny/scrutiny.css'
import './committee.css'
import { committeeApi } from './committeeApi'
import { VisitPanel } from './EvcPanels'
import { CommitteeStateBadge } from './parts'
import { AppealPanel, SacReviewPanel } from './SacPanels'
import { ReviewPanel, RecommendationPanel, RequirementsPanel } from './ShcPanels'
import { CompletePanel, HistoryPanel, OverviewPanel, RoutePanel, SchedulePanel, SelectionPanel, type PanelCtx } from './SharedPanels'
import type { CommitteeCaseDetail, Stage } from './types'
import { useAction } from './useAction'

type Tab = 'overview' | 'documents' | 'arrange' | 'visit' | 'requirements' | 'review' | 'recommendation' | 'appeal' | 'history'

function View({ stage, initial, reload }: { stage: Stage; initial: CommitteeCaseDetail; reload: () => Promise<CommitteeCaseDetail | null> }) {
  const [detail, setDetail] = useState(initial)
  const refetch = useCallback(() => void reload().then((d) => d && setDetail(d)), [reload])
  const { busy, error, run } = useAction<CommitteeCaseDetail>(setDetail, refetch)
  const member = detail.scope.includes('member')
  const coordinator = detail.scope.includes('coordinator')
  const loadEvidence = useCallback(() => committeeApi.evidence(stage, detail.id), [stage, detail.id])

  const tabs: [Tab, string][] = [['overview', 'Overview'], ...(stage === 'SAC' ? [] : ([['documents', 'Documents']] as [Tab, string][]))]
  if (stage === 'EVC') tabs.push(['arrange', coordinator ? 'Selection and schedule' : 'Members and schedule'], ['visit', 'Visit and report'])
  else if (stage === 'SAC') tabs.push(['appeal', 'The appeal'], ['arrange', 'Members and hearing'], ['recommendation', 'Hearing and recommendation'])
  else tabs.push(['requirements', 'Requirements and compliance'], ['arrange', 'Members and hearing'], ['review', 'Review'], ['recommendation', 'Recommendation'])
  tabs.push(['history', 'History'])
  const [tab, setTab] = useState<Tab>('overview')

  const ctx: PanelCtx = { detail, run, busy, error }
  const sorted = detail.application
  // Documents a visit observation may cite: the current version of each submitted file. Loaded only when needed.
  const [docs, setDocs] = useState<{ id: string; label: string }[]>([])
  useEffect(() => {
    if (stage === 'EVC' && member && detail.state === 'VISIT_IN_PROGRESS') {
      void committeeApi.evidence('EVC', detail.id).then((ev) => setDocs(ev.items.filter((i) => i.current).map((i) => ({ id: i.current!.id, label: `${i.label} — version ${i.current!.version_number}` })))).catch(() => setDocs([]))
    }
  }, [stage, member, detail.state, detail.id, detail.row_version])

  const stale = error && ['stale_version', 'invalid_transition'].includes(error.code ?? '')
  const ownedByPanel = error && ['report_incomplete', 'recommendation_incomplete'].includes(error.code ?? '')
  return (
    <>
      <PageHeader
        title={sorted.reference}
        description={`${sorted.institution_name} · ${sorted.category_name} · ${sorted.academic_year}`}
        crumbs={[{ label: 'Workspace', to: '/app' }, { label: stage === 'EVC' ? 'EVC' : stage === 'SAC' ? 'Appeals' : 'SHC', to: `/app/${stage === 'EVC' ? (coordinator ? 'evc-coordination' : 'evc-member') : stage === 'SAC' ? 'appeals' : 'shc'}` }, { label: sorted.reference }]}
        actions={<CommitteeStateBadge state={detail.state} label={detail.state_label} />}
      />
      <p className="note">{detail.not_an_approval}</p>
      {error && !stale && !ownedByPanel && (
        <Alert tone="error" title="That did not go through">
          {error.message}
        </Alert>
      )}
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
          {stage !== 'SAC' && <RoutePanel {...ctx} />}
          {stage === 'EVC' && <CompletePanel {...ctx} label="Completing the stage records that the visit and its report are finished. It starts whatever the approved route says comes next; it does not approve anything." />}
          {stage === 'SAC' && <CompletePanel {...ctx} label="Completing the stage hands the SAC’s recommendation to the Council. The SAC does not decide the appeal; the Council does." />}
          {stage === 'SHC' && <CompletePanel {...ctx} label="Completing the stage records that the SHC process and its recommendation are finished. It starts whatever the approved route says comes next; it decides nothing." />}
        </>
      )}
      {tab === 'appeal' && stage === 'SAC' && <AppealPanel ctx={ctx} />}
      {tab === 'documents' && <EvidenceTab caseId={detail.id} focusItem={null} refreshKey={detail.row_version} load={loadEvidence} />}
      {tab === 'arrange' && (
        <>
          <SelectionPanel {...ctx} />
          <SchedulePanel {...ctx} />
        </>
      )}
      {tab === 'visit' && stage === 'EVC' && <VisitPanel ctx={ctx} documents={docs} />}
      {tab === 'requirements' && stage === 'SHC' && <RequirementsPanel ctx={ctx} />}
      {tab === 'review' && stage === 'SHC' && <ReviewPanel ctx={ctx} />}
      {tab === 'recommendation' && stage === 'SHC' && <RecommendationPanel ctx={ctx} />}
      {tab === 'recommendation' && stage === 'SAC' && <SacReviewPanel ctx={ctx} />}
      {tab === 'history' && <HistoryPanel detail={detail} />}
    </>
  )
}

function CasePage({ stage }: { stage: Stage }) {
  const { id = '' } = useParams()
  const [detail, setDetail] = useState<CommitteeCaseDetail | null>(null)
  const [error, setError] = useState<{ message: string; missing: boolean } | null>(null)
  const load = useCallback(async () => {
    try {
      const d = await committeeApi.detail(stage, id)
      setError(null)
      return d
    } catch (e) {
      setError({ message: e instanceof ApiError ? e.message : 'The case could not be loaded.', missing: e instanceof ApiError && e.status === 404 })
      return null
    }
  }, [stage, id])
  useEffect(() => {
    void load().then(setDetail)
  }, [load])
  const retry = useMemo(() => () => void load().then(setDetail), [load])
  if (error && !detail) {
    return error.missing ? (
      <EmptyState title="Case not found">It may not exist, or it is not within your scope. Cases you are not assigned to are not shown to you.</EmptyState>
    ) : (
      <ErrorState message={error.message} onRetry={retry} />
    )
  }
  if (!detail) return <LoadingState label="Loading the case…" />
  return <View key={detail.id} stage={stage} initial={detail} reload={load} />
}

export const EvcCasePage = () => <CasePage stage="EVC" />
export const ShcCasePage = () => <CasePage stage="SHC" />
export const SacCasePage = () => <CasePage stage="SAC" />
