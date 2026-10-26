import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { Alert } from '../../components/ui/feedback'
import { DemoBanner, VerificationNote } from '../../components/ui/Banners'
import { useResource } from '../../lib/useResource'
import { ResourceView } from '../admin/shared'
import { PageHeader } from '../shell/PageHeader'
import { applicationsApi } from './applicationsApi'
import { DocumentSlot } from './DocumentSlot'
import { DraftEditor } from './DraftEditor'
import { ChecklistPanel, HistoryList, RouteList, Section, SourceList } from './parts'
import { DataSummary, SummaryPanel } from './summary'
import type { ApplicationDetail } from './types'
import './applications.css'

function SubmittedView({ detail, justSubmitted }: { detail: ApplicationDetail; justSubmitted: boolean }) {
  const docItems = detail.checklist_items.filter((i) => i.item_type === 'document_upload' && i.applicable)
  return (
    <div className="editor">
      {justSubmitted && (
        <Alert tone="success" title="Application submitted">
          Your application has been recorded. Keep this internal reference: <strong className="mono">{detail.reference}</strong>. It is not an AICTE application number.
        </Alert>
      )}
      <Alert tone="info">
        This application is read-only. Corrections or new evidence can be added only through an authorised correction process, which is not available in this phase. Later processing stages are not yet implemented.
      </Alert>
      {detail.rule_version.is_demo && <DemoBanner text={detail.rule_version.demo_banner} />}
      <SummaryPanel detail={detail} />
      <Section title="Submitted details">
        <DataSummary detail={detail} values={detail.form_data} />
      </Section>
      <Section title="Requirement checklist">
        <ChecklistPanel items={detail.checklist_items} />
      </Section>
      <Section title="Submitted documents">
        {docItems.length === 0 ? (
          <p className="note">No documents were requested.</p>
        ) : (
          <div className="slots">
            {docItems.map((i) => (
              <DocumentSlot key={i.item_key} applicationId={detail.id} item={i} documents={detail.documents} editable={false} onUpdated={() => undefined} />
            ))}
          </div>
        )}
      </Section>
      <Section title="Configuration this application was submitted under" aside={<VerificationNote verification={detail.rule_version.verification} />}>
        <dl className="facts">
          <div>
            <dt>Rule set</dt>
            <dd>{detail.rule_version.rule_set_title}</dd>
          </div>
          <div>
            <dt>Version</dt>
            <dd className="mono">
              {detail.rule_version.rule_set_key} · v{detail.rule_version.number}
            </dd>
          </div>
          <div>
            <dt>Content hash</dt>
            <dd className="mono hash">{detail.rule_version.content_hash ?? '—'}</dd>
          </div>
        </dl>
        <h3 className="panel__sub">Sources</h3>
        <SourceList sources={detail.rule_version.sources} />
        <h3 className="panel__sub">Processing route</h3>
        <RouteList stages={detail.rule_version.route} />
      </Section>
      <Section title="History">
        <HistoryList items={detail.history} />
      </Section>
    </div>
  )
}

function View({ initial, onReload }: { initial: ApplicationDetail; onReload: () => void }) {
  const [detail, setDetail] = useState(initial)
  const [justSubmitted, setJustSubmitted] = useState(false)
  return (
    <>
      <PageHeader
        title={detail.reference}
        description={`${detail.category.name} · ${detail.academic_year}`}
        crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Applications', to: '/app/applications' }, { label: detail.reference }]}
      />
      {detail.status === 'DRAFT' ? (
        <DraftEditor detail={detail} onUpdated={setDetail} onSubmitted={(d) => { setDetail(d); setJustSubmitted(true); window.scrollTo(0, 0) }} onReload={onReload} />
      ) : (
        <SubmittedView detail={detail} justSubmitted={justSubmitted} />
      )}
      {detail.status === 'DRAFT' && (
        <details className="panel panel--collapsible">
          <summary>History</summary>
          <HistoryList items={detail.history} />
        </details>
      )}
    </>
  )
}

export default function ApplicationDetailPage() {
  const { id = '' } = useParams()
  const [epoch, setEpoch] = useState(0)
  const [res, retry] = useResource(() => applicationsApi.get(id), [id, epoch])
  return (
    <ResourceView resource={res} reload={retry}>
      {(d) => <View key={`${d.id}:${epoch}`} initial={d} onReload={() => setEpoch((e) => e + 1)} />}
    </ResourceView>
  )
}
