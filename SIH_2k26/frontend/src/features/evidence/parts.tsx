import { Badge } from '../../components/ui/feedback'
import { formatBytes, formatDate } from '../../lib/format'
import type { DocumentState, ExtractedFieldOut, Finding, Version } from './types'

/** Technical state in the server's words. Colour is never the only signal: the label always accompanies it. */
export function StatePill({ state, withExplanation = false }: { state: DocumentState; withExplanation?: boolean }) {
  return (
    <span className="state-pill">
      <Badge tone={state.tone}>{state.label}</Badge>
      {withExplanation && <span className="state-pill__text">{state.explanation}</span>}
    </span>
  )
}

const FIELD_STATUS = {
  EXTRACTED: { tone: 'success', label: 'Read' },
  UNCERTAIN: { tone: 'warning', label: 'Uncertain' },
  NOT_FOUND: { tone: 'neutral', label: 'Unable to extract' },
} as const

/** Extracted values with provenance. Uncertainty and absence are shown as plainly as success. */
export function FieldList({ fields, onPage, configured }: { fields: ExtractedFieldOut[]; onPage?: (page: number) => void; configured?: string[] }) {
  if (!fields.length) {
    return <p className="note">{configured && configured.length ? `No values have been read yet. This requirement looks for: ${configured.join(', ')}.` : 'No values are configured for this document.'}</p>
  }
  return (
    <div className="table-wrap">
      <table className="table fields-table">
        <caption className="sr-only">Values read from the document</caption>
        <thead>
          <tr>
            <th scope="col">Value</th>
            <th scope="col">Read as</th>
            <th scope="col">Where</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((f) => (
            <tr key={f.field_key}>
              <td>{f.label}</td>
              <td>
                {f.value ? <strong>{f.value}</strong> : <span className="muted">—</span>}
                {f.normalised_value && f.normalised_value !== f.value && <div className="mono muted">{f.normalised_value}</div>}
                {f.evidence && <div className="mono muted fields-table__evidence">“{f.evidence}”</div>}
                {f.note && <div className="note">{f.note}</div>}
              </td>
              <td className="mono nowrap">
                {f.page_number ? (
                  onPage ? (
                    <button type="button" className="linklike" onClick={() => onPage(f.page_number!)} aria-label={`Show page ${f.page_number}`}>
                      p. {f.page_number}
                    </button>
                  ) : (
                    <>p. {f.page_number}</>
                  )
                ) : (
                  '—'
                )}
                <div className="muted">{f.method === 'OCR' ? 'text recognition' : f.method === 'PDF_TEXT' ? 'PDF text' : ''}</div>
                {f.confidence !== null && <div className="muted">{f.confidence.toFixed(0)}% confidence</div>}
              </td>
              <td>
                <Badge tone={FIELD_STATUS[f.status].tone}>{FIELD_STATUS[f.status].label}</Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const PRIORITY: Record<string, string> = { high: 'High review priority', medium: 'Medium review priority', low: 'Low review priority', info: 'For information' }

/** Preliminary findings. Always labelled as awaiting review and never as a deficiency. */
export function FindingList({ findings, reviewer }: { findings: Finding[]; reviewer: boolean }) {
  if (!findings.length) return <p className="note">No preliminary issues were found by the automated checks. This is not a statement that the evidence is complete or correct.</p>
  return (
    <ul className="findings">
      {findings.map((f) => (
        <li key={f.id} className="finding" data-priority={f.priority ?? 'none'}>
          <div className="finding__head">
            <strong>{f.label}</strong>
            <span className="finding__tags">
              {f.priority === 'info' ? <Badge tone="neutral">Note — not an issue with the document</Badge> : <Badge tone="warning">Potential issue — awaiting scrutiny review</Badge>}
              {reviewer && f.priority && f.priority !== 'info' && <Badge tone={f.priority === 'high' ? 'error' : 'neutral'}>{PRIORITY[f.priority]}</Badge>}
            </span>
          </div>
          <p>{reviewer ? f.explanation : f.plain_text}</p>
          {!reviewer && f.explanation !== f.plain_text && <p className="note">{f.explanation}</p>}
          {f.values.length > 0 && (
            <ul className="finding__values mono">
              {f.values.map((v, i) => (
                <li key={i}>
                  {String(v.source ?? v.field ?? 'value')}
                  {v.version ? ` v${String(v.version)}` : ''}
                  {v.page ? ` p.${String(v.page)}` : ''}: {v.value !== undefined ? `“${String(v.value)}”` : v.confidence !== undefined ? `${String(v.confidence)}% confidence` : ''}
                </li>
              ))}
            </ul>
          )}
          <p className="checklist__meta mono">
            {f.rule_ref ?? 'Application-level'}
            {f.version_number ? ` · version ${f.version_number}` : ''}
            {f.pages.length ? ` · page${f.pages.length > 1 ? 's' : ''} ${f.pages.join(', ')}` : ''}
            {reviewer && f.check_key ? ` · check: ${f.check_key}` : ''}
          </p>
          <p className="finding__disclaimer">{f.disclaimer}</p>
        </li>
      ))}
    </ul>
  )
}

export function VersionList({ versions, onSelect, selectedId, downloadUrl }: { versions: Version[]; onSelect?: (v: Version) => void; selectedId?: string; downloadUrl: (id: string) => string }) {
  if (!versions.length) return <p className="note">No file has been uploaded.</p>
  return (
    <ol className="versions">
      {[...versions].reverse().map((v) => (
        <li key={v.id} className="versions__item" data-current={v.is_current || undefined}>
          <div>
            <strong>Version {v.version_number}</strong> {v.is_current && <Badge tone="info">Current</Badge>}
            <div>{v.original_filename}</div>
            <div className="mono muted">
              {formatBytes(v.size_bytes)} · {formatDate(v.uploaded_at)}
              {v.uploaded_by ? ` · ${v.uploaded_by}` : ''}
            </div>
            <div className="mono muted hash" title="SHA-256 of the stored file">
              {v.sha256.slice(0, 16)}…
            </div>
            <StatePill state={v.state} />
          </div>
          <div className="versions__actions">
            {onSelect && (
              <button type="button" className="linklike" aria-pressed={selectedId === v.id} onClick={() => onSelect(v)}>
                {selectedId === v.id ? 'Viewing' : 'View'}
              </button>
            )}
            <a href={downloadUrl(v.id)} className="link">
              Download
            </a>
          </div>
        </li>
      ))}
    </ol>
  )
}
