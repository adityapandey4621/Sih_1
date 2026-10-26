import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Plus } from 'lucide-react'
import { ApiError } from '../../lib/api'
import { formatDate, formatDay } from '../../lib/format'
import { useResource } from '../../lib/useResource'
import { Button } from '../../components/ui/Button'
import { Alert, Badge, EmptyState } from '../../components/ui/feedback'
import { SelectField, TextAreaField, TextField } from '../../components/ui/Field'
import { ResourceView } from '../admin/shared'
import { useAuth } from '../auth/AuthContext'
import { SOURCE_BADGE } from '../applications/parts'
import type { Category, Source, SourceVerification } from '../applications/types'
import { PageHeader } from '../shell/PageHeader'
import '../applications/applications.css'
import { ClassificationBadge, VersionStatusBadge } from './parts'
import { rulesApi } from './rulesApi'
import type { RuleSetOut, SourceInput } from './types'

const TABS = [
  { key: 'sets', label: 'Rule sets' },
  { key: 'sources', label: 'Sources' },
  { key: 'categories', label: 'Categories' },
  { key: 'gaps', label: 'Configuration gaps' },
] as const
type Tab = (typeof TABS)[number]['key']

const fail = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message + (Object.keys(e.fieldErrors).length ? ' ' + Object.entries(e.fieldErrors).map(([k, m]) => `${k}: ${m}`).join(' · ') : '') : fallback)
const KEY_HINT = 'Lower-case letters, digits, - and _. Stable: it cannot be changed later.'

// ---- rule sets -----------------------------------------------------------------------------------

function NewRuleSetForm({ categories, onDone, onCancel }: { categories: Category[]; onDone: (versionId: string) => void; onCancel: () => void }) {
  const [f, setF] = useState({ key: '', title: '', category_id: '', classification: 'DEMO' as 'OFFICIAL' | 'DEMO' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onDone((await rulesApi.createSet(f)).id)
    } catch (err) {
      setError(fail(err, 'The rule set could not be created.'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <form className="invite-form" onSubmit={submit} noValidate>
      <h2 className="section-title">New rule set</h2>
      {error && <Alert tone="error">{error}</Alert>}
      <div className="invite-form__grid">
        <TextField label="Key" value={f.key} onChange={(e) => setF({ ...f, key: e.target.value })} hint={KEY_HINT} />
        <TextField label="Title" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} />
        <SelectField label="Category" value={f.category_id} onChange={(e) => setF({ ...f, category_id: e.target.value })} options={categories.filter((c) => c.is_active).map((c) => ({ value: c.id, label: c.name }))} />
        <SelectField
          label="Classification"
          value={f.classification}
          placeholder="Choose"
          onChange={(e) => setF({ ...f, classification: e.target.value as 'OFFICIAL' | 'DEMO' })}
          options={[{ value: 'OFFICIAL', label: 'Official — only publishable with verified sources' }, { value: 'DEMO', label: 'DEMO ONLY — non-production, never official' }]}
        />
      </div>
      <div className="inline-actions">
        <Button type="submit" loading={busy} disabled={!f.key || !f.title || !f.category_id}>Create draft</Button>
        <Button variant="quiet" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  )
}

function RuleSets() {
  const { can } = useAuth()
  const navigate = useNavigate()
  const [sets, reload] = useResource(rulesApi.sets, [])
  const [cats] = useResource(rulesApi.categories, [])
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const editor = can('rules:edit')

  async function newVersion(s: RuleSetOut) {
    setError(null)
    try {
      navigate(`/app/rules/versions/${(await rulesApi.newVersion(s.id)).id}/edit`)
    } catch (e) {
      setError(fail(e, 'The new version could not be created.'))
    }
  }

  return (
    <>
      {editor && !creating && (
        <div className="inline-actions" style={{ marginTop: 0, marginBottom: 'var(--s-4)' }}>
          <Button size="sm" onClick={() => setCreating(true)}><Plus size={16} aria-hidden="true" /> New rule set</Button>
        </div>
      )}
      {creating && cats.status === 'ready' && <NewRuleSetForm categories={cats.data} onCancel={() => setCreating(false)} onDone={(id) => navigate(`/app/rules/versions/${id}/edit`)} />}
      {error && <Alert tone="error">{error}</Alert>}
      <ResourceView resource={sets} reload={reload}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title="No rule sets yet">A rule set holds the versioned requirements and route for one application category. {editor ? 'Create one to begin drafting.' : 'A rules administrator creates them.'}</EmptyState>
          ) : (
            <div className="rule-sets">
              {items.map((s) => {
                const open = s.versions.some((v) => v.status === 'draft' || v.status === 'in_review')
                return (
                  <article key={s.id} className="rule-set">
                    <div className="rule-set__head">
                      <div>
                        <p className="rule-set__title">{s.title}</p>
                        <p className="mono muted">{s.key} · {s.category.name}</p>
                      </div>
                      <div className="checklist__tags"><ClassificationBadge classification={s.classification} /></div>
                    </div>
                    <div className="rule-set__versions">
                      {[...s.versions].reverse().map((v) => (
                        <div key={v.id} className="rule-set__version">
                          <span className="mono">v{v.version_number}</span>
                          <VersionStatusBadge status={v.status} />
                          <span className="muted">
                            {v.academic_years.join(', ') || 'No years set'} · {v.verification === 'verified' ? 'sources verified' : v.verification === 'demo' ? 'demo only' : 'sources NOT verified'} · updated {formatDate(v.updated_at)}
                          </span>
                          <Link to={`/app/rules/versions/${v.id}`} className="link">Open</Link>
                        </div>
                      ))}
                    </div>
                    {editor && !open && <div className="inline-actions"><Button size="sm" variant="secondary" onClick={() => newVersion(s)}>Create new version</Button></div>}
                  </article>
                )
              })}
            </div>
          )
        }
      </ResourceView>
    </>
  )
}

// ---- sources -------------------------------------------------------------------------------------

const EMPTY_SOURCE: SourceInput = { title: '', issuing_authority: '', url: null, document_ref: null, clause_ref: null, page_ref: null, publication_date: null, effective_date: null, retrieved_date: null, verification_status: 'SECONDARY_UNVERIFIED', applicability_notes: null }
const VERIFICATION_OPTIONS = (Object.keys(SOURCE_BADGE) as SourceVerification[]).map((k) => ({ value: k, label: SOURCE_BADGE[k].label }))
const nil = (s: string) => (s.trim() === '' ? null : s.trim())

function SourceForm({ initial, onSaved, onCancel }: { initial?: Source; onSaved: () => void; onCancel: () => void }) {
  const [f, setF] = useState<SourceInput>(initial ? { ...initial } : EMPTY_SOURCE)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = (k: keyof SourceInput) => (e: { target: { value: string } }) => setF((cur) => ({ ...cur, [k]: k === 'title' || k === 'issuing_authority' || k === 'verification_status' ? e.target.value : nil(e.target.value) }) as SourceInput)
  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await (initial ? rulesApi.updateSource(initial.id, f) : rulesApi.createSource(f))
      onSaved()
    } catch (err) {
      setError(fail(err, 'The source could not be saved.'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <form className="invite-form" onSubmit={submit} noValidate>
      <h2 className="section-title">{initial ? 'Edit source' : 'Add source'}</h2>
      {error && <Alert tone="error">{error}</Alert>}
      <div className="invite-form__grid">
        <TextField label="Title" value={f.title} onChange={set('title')} />
        <TextField label="Issuing authority" value={f.issuing_authority} onChange={set('issuing_authority')} />
        <TextField label="Official URL" optional value={f.url ?? ''} onChange={set('url')} />
        <TextField label="Stored document reference" optional value={f.document_ref ?? ''} onChange={set('document_ref')} />
        <TextField label="Clause / table" optional value={f.clause_ref ?? ''} onChange={set('clause_ref')} />
        <TextField label="Page" optional value={f.page_ref ?? ''} onChange={set('page_ref')} />
        <TextField label="Publication date" optional type="date" value={f.publication_date ?? ''} onChange={set('publication_date')} />
        <TextField label="Effective date" optional type="date" value={f.effective_date ?? ''} onChange={set('effective_date')} />
        <TextField label="Retrieval date" optional type="date" value={f.retrieved_date ?? ''} onChange={set('retrieved_date')} />
        <SelectField label="Verification status" value={f.verification_status} placeholder="Choose" onChange={set('verification_status')} options={VERIFICATION_OPTIONS} hint="“Verified” only after reading the issuing authority's own publication." />
      </div>
      <TextAreaField label="Applicability notes" optional value={f.applicability_notes ?? ''} onChange={set('applicability_notes')} hint="Limits, gaps, and anything that stops this being treated as current or complete." />
      <div className="inline-actions">
        <Button type="submit" loading={busy} disabled={!f.title || !f.issuing_authority}>Save source</Button>
        <Button variant="quiet" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  )
}

function Sources() {
  const { can } = useAuth()
  const [res, reload] = useResource(rulesApi.sources, [])
  const [editing, setEditing] = useState<Source | 'new' | null>(null)
  const editor = can('rules:edit')
  const done = () => { setEditing(null); reload() }
  return (
    <>
      {editor && !editing && <div className="inline-actions" style={{ marginTop: 0, marginBottom: 'var(--s-4)' }}><Button size="sm" onClick={() => setEditing('new')}><Plus size={16} aria-hidden="true" /> Add source</Button></div>}
      {editing && <SourceForm initial={editing === 'new' ? undefined : editing} onSaved={done} onCancel={() => setEditing(null)} />}
      <ResourceView resource={res} reload={reload}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title="No sources recorded">Every rule must cite where it comes from. Add the official publication, clause and verification status.</EmptyState>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <caption className="sr-only">Rule sources</caption>
                <thead><tr><th scope="col">Source</th><th scope="col">Reference</th><th scope="col">Verification</th><th scope="col">Retrieved</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
                <tbody>
                  {items.map((s) => (
                    <tr key={s.id}>
                      <td>{s.title}<br /><span className="muted">{s.issuing_authority}</span></td>
                      <td className="mono">{[s.clause_ref, s.page_ref].filter(Boolean).join(' · ') || '—'}</td>
                      <td><Badge tone={SOURCE_BADGE[s.verification_status].tone}>{SOURCE_BADGE[s.verification_status].label}</Badge>{s.applicability_notes && <p className="note">{s.applicability_notes}</p>}</td>
                      <td className="mono nowrap">{formatDay(s.retrieved_date)}</td>
                      <td>{editor && (s.locked ? <span className="muted">Locked — cited by a reviewed version</span> : <Button size="sm" variant="secondary" onClick={() => setEditing(s)}>Edit</Button>)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </ResourceView>
    </>
  )
}

// ---- categories ----------------------------------------------------------------------------------

function Categories() {
  const { can } = useAuth()
  const [res, reload] = useResource(rulesApi.categories, [])
  const [adding, setAdding] = useState(false)
  const [f, setF] = useState({ key: '', name: '', description: '', applicability_note: '' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await rulesApi.createCategory({ ...f, applicability_note: nil(f.applicability_note), source_id: null, is_active: true })
      setAdding(false)
      setF({ key: '', name: '', description: '', applicability_note: '' })
      reload()
    } catch (err) {
      setError(fail(err, 'The category could not be created.'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      {can('rules:edit') && !adding && <div className="inline-actions" style={{ marginTop: 0, marginBottom: 'var(--s-4)' }}><Button size="sm" onClick={() => setAdding(true)}><Plus size={16} aria-hidden="true" /> Add category</Button></div>}
      {adding && (
        <form className="invite-form" onSubmit={submit} noValidate>
          <h2 className="section-title">Add category</h2>
          {error && <Alert tone="error">{error}</Alert>}
          <div className="invite-form__grid">
            <TextField label="Key" value={f.key} onChange={(e) => setF({ ...f, key: e.target.value })} hint={KEY_HINT} />
            <TextField label="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          </div>
          <TextAreaField label="Description" optional value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
          <TextAreaField label="Applicability note" optional value={f.applicability_note} onChange={(e) => setF({ ...f, applicability_note: e.target.value })} />
          <div className="inline-actions"><Button type="submit" loading={busy} disabled={!f.key || !f.name}>Add category</Button><Button variant="quiet" onClick={() => setAdding(false)}>Cancel</Button></div>
        </form>
      )}
      <ResourceView resource={res} reload={reload}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title="No categories yet">Application categories are what institutions choose from. Add the first one.</EmptyState>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <caption className="sr-only">Application categories</caption>
                <thead><tr><th scope="col">Category</th><th scope="col">Key</th><th scope="col">State</th></tr></thead>
                <tbody>
                  {items.map((c) => (
                    <tr key={c.id}>
                      <td>{c.name}{c.applicability_note && <p className="note">{c.applicability_note}</p>}</td>
                      <td className="mono">{c.key}</td>
                      <td><Badge tone={c.is_active ? 'success' : 'neutral'}>{c.is_active ? 'Active' : 'Inactive'}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </ResourceView>
    </>
  )
}

// ---- gaps ----------------------------------------------------------------------------------------

const GAP_REASON = { no_approved_version: 'No approved configuration', ambiguous: 'More than one configuration applies', incomplete: 'Configuration incomplete' } as const

function Gaps() {
  const { can } = useAuth()
  const [res, reload] = useResource(rulesApi.gaps, [])
  const [resolving, setResolving] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  async function resolve(id: string) {
    setError(null)
    try {
      await rulesApi.resolveGap(id, note.trim())
      setResolving(null)
      setNote('')
      reload()
    } catch (e) {
      setError(fail(e, 'The gap could not be resolved.'))
    }
  }
  return (
    <>
      <p className="note" style={{ marginBottom: 'var(--s-4)' }}>Recorded when an institution tried to start an application for a category and year with no single approved configuration. Publishing an applicable version closes the gap automatically.</p>
      {error && <Alert tone="error">{error}</Alert>}
      <ResourceView resource={res} reload={reload}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState title="No configuration gaps reported">Nothing has been requested that lacks an approved configuration.</EmptyState>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <caption className="sr-only">Configuration gaps</caption>
                <thead><tr><th scope="col">Category / year</th><th scope="col">Reason</th><th scope="col">Reports</th><th scope="col">Last reported</th><th scope="col">State</th></tr></thead>
                <tbody>
                  {items.map((g) => (
                    <tr key={g.id}>
                      <td>{g.category.name}<br /><span className="mono muted">{g.academic_year}</span></td>
                      <td>{GAP_REASON[g.reason]}</td>
                      <td className="mono">{g.report_count}</td>
                      <td className="mono nowrap">{formatDate(g.last_reported_at)}</td>
                      <td>
                        <Badge tone={g.status === 'open' ? 'warning' : 'success'}>{g.status === 'open' ? 'Open' : 'Resolved'}</Badge>
                        {g.resolution_note && <p className="note">{g.resolution_note}</p>}
                        {g.status === 'open' && can('rules:edit') && (resolving === g.id ? (
                          <form className="reject-form" onSubmit={(e) => { e.preventDefault(); void resolve(g.id) }}>
                            <TextField label="Resolution note" value={note} onChange={(e) => setNote(e.target.value)} />
                            <div className="inline-actions"><Button type="submit" size="sm" disabled={note.trim().length < 3}>Mark resolved</Button><Button size="sm" variant="quiet" onClick={() => setResolving(null)}>Cancel</Button></div>
                          </form>
                        ) : <div className="inline-actions"><Button size="sm" variant="secondary" onClick={() => setResolving(g.id)}>Resolve…</Button></div>)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </ResourceView>
    </>
  )
}

export default function RulesCatalogPage() {
  const [params, setParams] = useSearchParams()
  const tab = (TABS.find((t) => t.key === params.get('tab'))?.key ?? 'sets') as Tab
  return (
    <>
      <PageHeader
        title="Rule catalogue"
        description="Versioned, source-referenced configuration that drives application requirements and routes. Nothing here is hard-coded in the interface."
        crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Rule catalogue' }]}
      />
      <div className="tabbar" role="tablist" aria-label="Catalogue sections">
        {TABS.map((t) => (
          <button key={t.key} type="button" role="tab" className="tabbar__btn" aria-selected={tab === t.key} onClick={() => setParams({ tab: t.key }, { replace: true })}>
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel">
        {tab === 'sets' && <RuleSets />}
        {tab === 'sources' && <Sources />}
        {tab === 'categories' && <Categories />}
        {tab === 'gaps' && <Gaps />}
      </div>
    </>
  )
}
