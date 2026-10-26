import { Badge } from '../../components/ui/feedback'
import { formatDate } from '../../lib/format'
import { Section } from './parts'
import type { ApplicationDetail } from './types'

/** Institution, classification and status. The institution comes from the signed-in user's own association. */
export function SummaryPanel({ detail }: { detail: ApplicationDetail }) {
  return (
    <Section title="Application" aside={<Badge tone={detail.status === 'SUBMITTED' ? 'success' : 'warning'}>{detail.status === 'SUBMITTED' ? 'Submitted' : 'Draft'}</Badge>}>
      <dl className="facts">
        <div>
          <dt>Internal reference</dt>
          <dd className="mono">{detail.reference}</dd>
        </div>
        <div>
          <dt>Institution</dt>
          <dd>
            {detail.institution.name}
            {detail.institution.external_identifier && <span className="mono muted"> · {detail.institution.external_identifier}</span>}
          </dd>
        </div>
        <div>
          <dt>Category</dt>
          <dd>{detail.category.name}</dd>
        </div>
        <div>
          <dt>Academic year</dt>
          <dd className="mono">{detail.academic_year}</dd>
        </div>
        <div>
          <dt>Created</dt>
          <dd className="mono">
            {formatDate(detail.created_at)}
            {detail.created_by ? ` · ${detail.created_by}` : ''}
          </dd>
        </div>
        {detail.submitted_at && (
          <div>
            <dt>Submitted</dt>
            <dd className="mono">
              {formatDate(detail.submitted_at)}
              {detail.submitted_by ? ` · ${detail.submitted_by}` : ''}
            </dd>
          </div>
        )}
      </dl>
    </Section>
  )
}

/** The answers given, labelled from the configuration. */
export function DataSummary({ detail, values }: { detail: ApplicationDetail; values: Record<string, unknown> }) {
  const rows = detail.checklist_items.filter((i) => (i.item_type === 'form_field' || i.item_type === 'declaration') && i.applicable)
  if (!rows.length) return <p className="note">No details were requested.</p>
  return (
    <dl className="facts facts--wide">
      {rows.map((i) => {
        const v = values[i.item_key]
        const shown = v === undefined || v === null || v === '' ? null : i.item_type === 'declaration' ? (v === true ? 'Confirmed' : '—') : String(v)
        return (
          <div key={i.item_key}>
            <dt>{i.label}</dt>
            <dd>{shown ?? <span className="muted">Not provided</span>}</dd>
          </div>
        )
      })}
    </dl>
  )
}
