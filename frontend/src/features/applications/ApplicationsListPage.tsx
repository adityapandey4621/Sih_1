import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Plus, Search } from 'lucide-react'
import { useResource } from '../../lib/useResource'
import { formatDate } from '../../lib/format'
import { ButtonLink, Button } from '../../components/ui/Button'
import { Badge, EmptyState } from '../../components/ui/feedback'
import { SelectField, TextField } from '../../components/ui/Field'
import { ResourceView } from '../admin/shared'
import { useAuth } from '../auth/AuthContext'
import { PageHeader } from '../shell/PageHeader'
import { applicationsApi } from './applicationsApi'
import './applications.css'

const PAGE_SIZE = 10

export default function ApplicationsListPage() {
  const { can } = useAuth()
  const [params, setParams] = useSearchParams()
  const status = params.get('status') ?? ''
  const category = params.get('category') ?? ''
  const year = params.get('year') ?? ''
  const q = params.get('q') ?? ''
  const page = Math.max(1, Number(params.get('page') ?? '1') || 1)
  const [search, setSearch] = useState(q)
  useEffect(() => setSearch(q), [q])

  const [options] = useResource(applicationsApi.options, [])
  const [list, reload] = useResource(
    () => applicationsApi.list({ status, category_id: category, academic_year: year, q, page, page_size: PAGE_SIZE }),
    [status, category, year, q, page],
  )

  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries({ page: '1', ...patch })) v ? next.set(k, v) : next.delete(k)
    setParams(next, { replace: true })
  }
  const filtered = Boolean(status || category || year || q)

  return (
    <>
      <PageHeader
        title="Applications"
        description="Your institution's applications, their requirements and what needs doing next."
        crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Applications' }]}
        actions={
          can('application:create') && (
            <ButtonLink to="/app/applications/new">
              <Plus size={16} aria-hidden="true" /> New application
            </ButtonLink>
          )
        }
      />

      <form
        className="filters"
        role="search"
        onSubmit={(e) => {
          e.preventDefault()
          set({ q: search.trim() })
        }}
      >
        <TextField label="Reference" placeholder="e.g. APP-7K3Q" value={search} onChange={(e) => setSearch(e.target.value)} />
        <SelectField label="Status" value={status} onChange={(e) => set({ status: e.target.value })} placeholder="All" options={[{ value: 'DRAFT', label: 'Draft' }, { value: 'SUBMITTED', label: 'Submitted' }]} />
        <SelectField
          label="Category"
          value={category}
          onChange={(e) => set({ category: e.target.value })}
          placeholder="All"
          options={options.status === 'ready' ? options.data.categories.map((c) => ({ value: c.id, label: c.name })) : []}
        />
        <SelectField label="Academic year" value={year} onChange={(e) => set({ year: e.target.value })} placeholder="All" options={options.status === 'ready' ? options.data.academic_years.map((y) => ({ value: y, label: y })) : []} />
        <div className="filters__actions">
          <Button type="submit" variant="secondary" size="sm">
            <Search size={16} aria-hidden="true" /> Search
          </Button>
          {filtered && (
            <Button variant="quiet" size="sm" onClick={() => setParams({}, { replace: true })}>
              Clear filters
            </Button>
          )}
        </div>
      </form>

      <ResourceView resource={list} reload={reload}>
        {(data) =>
          data.total === 0 ? (
            filtered ? (
              <EmptyState title="No applications match these filters">Try different filters, or clear them to see everything your institution has started.</EmptyState>
            ) : (
              <EmptyState
                title="You have not started any applications"
                action={can('application:create') ? <ButtonLink to="/app/applications/new" size="sm">Start an application</ButtonLink> : undefined}
              >
                Choose the type of application and academic year. You will see the exact requirements before anything is created, and you can save a draft and return to it at any time.
              </EmptyState>
            )
          ) : (
            <>
              <div className="table-wrap">
                <table className="table apps-table">
                  <caption className="sr-only">Applications of your institution</caption>
                  <thead>
                    <tr>
                      <th scope="col">Reference</th>
                      <th scope="col">Category</th>
                      <th scope="col">Year</th>
                      <th scope="col">Status</th>
                      <th scope="col">Required items</th>
                      <th scope="col">Created / submitted</th>
                      <th scope="col">Next step</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((a) => (
                      <tr key={a.id}>
                        <td>
                          <Link to={`/app/applications/${a.id}`} className="mono link">
                            {a.reference}
                          </Link>
                          {a.is_demo && <span className="demo-tag"> DEMO</span>}
                        </td>
                        <td>{a.category.name}</td>
                        <td className="mono">{a.academic_year}</td>
                        <td>
                          <Badge tone={a.status === 'SUBMITTED' ? 'success' : 'warning'}>{a.status === 'SUBMITTED' ? 'Submitted' : 'Draft'}</Badge>
                        </td>
                        <td className="mono">
                          {a.checklist.required_supplied} / {a.checklist.required_total}
                        </td>
                        <td className="mono nowrap">{formatDate(a.submitted_at ?? a.created_at)}</td>
                        <td>{a.outstanding_action ?? <span className="muted">Nothing for you to do. Later processing stages are not available in this phase.</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="pager">
                <span className="muted">
                  {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, data.total)} of {data.total}
                </span>
                <div className="inline-actions">
                  <Button size="sm" variant="secondary" disabled={page <= 1} onClick={() => set({ page: String(page - 1) })}>
                    Previous
                  </Button>
                  <Button size="sm" variant="secondary" disabled={page * PAGE_SIZE >= data.total} onClick={() => set({ page: String(page + 1) })}>
                    Next
                  </Button>
                </div>
              </div>
            </>
          )
        }
      </ResourceView>
    </>
  )
}
