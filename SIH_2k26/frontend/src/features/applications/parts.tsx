import { Badge, type Tone } from '../../components/ui/feedback'
import { formatDate, formatDay, humanise } from '../../lib/format'
import type { ChecklistItem, ChecklistState, ItemStatus, RouteStage, Source, SourceVerification } from './types'

/** Generic evaluator for an item's `applies_when` data. The server re-evaluates authoritatively on save and submit. */
export function conditionMet(when: ChecklistItem['applies_when'], values: Record<string, unknown>): boolean {
  if (!when) return true
  const v = values[when.item_key]
  if (v === undefined || v === null || v === '') return false
  return when.in ? when.in.includes(v) : v === when.equals
}

const STATUS: Record<ItemStatus, { tone: Tone; label: string }> = {
  supplied: { tone: 'success', label: 'Supplied' },
  missing: { tone: 'warning', label: 'Not yet supplied' },
  invalid: { tone: 'error', label: 'Needs correction' },
  not_applicable: { tone: 'neutral', label: 'Not applicable' },
}

export const ItemStatusBadge = ({ status }: { status: ItemStatus }) => <Badge tone={STATUS[status].tone}>{STATUS[status].label}</Badge>

export const SOURCE_BADGE: Record<SourceVerification, { tone: Tone; label: string }> = {
  VERIFIED_OFFICIAL: { tone: 'success', label: 'Verified against official source' },
  SECONDARY_UNVERIFIED: { tone: 'warning', label: 'Secondary — not verified' },
  PROPOSED_POLICY: { tone: 'info', label: 'Proposed policy' },
  DEMO_ONLY: { tone: 'warning', label: 'Demo only' },
}

/** Requirement list with what each item represents and where it comes from. */
export function ChecklistPanel({ items, showStatus = true }: { items: (ChecklistState | (ChecklistItem & { status?: ItemStatus; applicable?: boolean }))[]; showStatus?: boolean }) {
  return (
    <>
      {showStatus && <p className="note">“Supplied” means the item has been provided in the expected form. It does not mean its contents have been checked or accepted against any requirement.</p>}
      <ol className="checklist">
        {items.map((i) => {
          const status = (i as ChecklistState).status
          const applicable = (i as ChecklistState).applicable !== false
          return (
            <li key={i.item_key} className="checklist__item" data-applicable={applicable}>
              <div className="checklist__head">
                <span className="checklist__label">{i.label}</span>
                <span className="checklist__tags">
                  <Badge tone={i.required ? 'info' : 'neutral'}>{i.required ? 'Required' : 'Optional'}</Badge>
                  {showStatus && status && <ItemStatusBadge status={status} />}
                </span>
              </div>
              {i.explanation && <p className="checklist__explain">{i.explanation}</p>}
              <p className="checklist__meta mono">
                {humanise(i.item_type)}
                {i.clause_ref ? ` · ${i.clause_ref}` : ''}
                {!applicable && ' · applies only if an earlier answer calls for it'}
              </p>
              {(i as ChecklistState).error && <p className="field__error">{(i as ChecklistState).error}</p>}
            </li>
          )
        })}
      </ol>
    </>
  )
}

export function SourceList({ sources }: { sources: Omit<Source, 'locked'>[] }) {
  if (!sources.length) return <p className="note">No sources are cited.</p>
  return (
    <ul className="sources">
      {sources.map((s) => (
        <li key={s.id} className="sources__item">
          <div className="sources__head">
            <span className="sources__title">{s.title}</span>
            <Badge tone={SOURCE_BADGE[s.verification_status].tone}>{SOURCE_BADGE[s.verification_status].label}</Badge>
          </div>
          <p className="sources__meta">
            {s.issuing_authority}
            {s.clause_ref ? ` · ${s.clause_ref}` : ''}
            {s.page_ref ? ` · ${s.page_ref}` : ''}
          </p>
          <p className="sources__meta mono">
            {s.publication_date ? `Published ${formatDay(s.publication_date)} · ` : ''}
            {s.retrieved_date ? `Retrieved ${formatDay(s.retrieved_date)}` : 'Retrieval date not recorded'}
          </p>
          {s.url && (
            <p className="sources__meta">
              <a href={s.url} target="_blank" rel="noreferrer noopener" className="link">
                {s.url}
              </a>
            </p>
          )}
          {s.document_ref && <p className="sources__meta mono">{s.document_ref}</p>}
          {s.applicability_notes && <p className="sources__notes">{s.applicability_notes}</p>}
        </li>
      ))}
    </ul>
  )
}

export function RouteList({ stages }: { stages: RouteStage[] }) {
  if (!stages.length) return <p className="note">No route is defined.</p>
  const label = (k: string) => stages.find((s) => s.stage_key === k)?.label ?? k
  return (
    <ol className="route-list">
      {stages.map((s, n) => (
        <li key={s.stage_key} className="route-list__item" data-conditional={s.is_conditional || undefined}>
          <span className="route-list__num mono">{String(n + 1).padStart(2, '0')}</span>
          <div>
            <p className="route-list__label">
              {s.label} {s.is_conditional && <Badge tone="warning">Conditional</Badge>}
            </p>
            {s.description && <p className="note">{s.description}</p>}
            {s.condition_description && <p className="route-list__cond">Applies: {s.condition_description}</p>}
            <p className="checklist__meta mono">
              {s.next_stage_keys.length ? `Then → ${s.next_stage_keys.map(label).join(', ')}` : 'Final stage in this route'}
              {s.clause_ref ? ` · ${s.clause_ref}` : ''}
            </p>
          </div>
        </li>
      ))}
    </ol>
  )
}

export function HistoryList({ items }: { items: { at: string; actor: string | null; action: string }[] }) {
  if (!items.length) return <p className="note">No history yet.</p>
  return (
    <ol className="history">
      {items.map((h, i) => (
        <li key={i} className="history__item">
          <span className="mono history__at">{formatDate(h.at)}</span>
          <span>
            <strong>{humanise(h.action)}</strong>
            {h.actor ? ` — ${h.actor}` : ''}
          </span>
        </li>
      ))}
    </ol>
  )
}

export function Section({ title, children, aside }: { title: string; children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <section className="panel">
      <div className="panel__head">
        <h2 className="panel__title">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  )
}
