import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../../lib/api'
import { useResource } from '../../lib/useResource'
import { Button } from '../../components/ui/Button'
import { DemoBanner, VerificationNote } from '../../components/ui/Banners'
import { Alert, EmptyState, LoadingState } from '../../components/ui/feedback'
import { SelectField } from '../../components/ui/Field'
import { ResourceView } from '../admin/shared'
import { PageHeader } from '../shell/PageHeader'
import { applicationsApi } from './applicationsApi'
import { ChecklistPanel, RouteList, Section, SourceList } from './parts'
import type { ResolvedChecklist } from './types'
import './applications.css'

type Preview = { s: 'idle' } | { s: 'loading' } | { s: 'ready'; data: ResolvedChecklist } | { s: 'unavailable'; message: string } | { s: 'error'; message: string }

export default function NewApplicationPage() {
  const navigate = useNavigate()
  const [options, reload] = useResource(applicationsApi.options, [])
  const [category, setCategory] = useState('')
  const [year, setYear] = useState('')
  const [preview, setPreview] = useState<Preview>({ s: 'idle' })
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<{ message: string; unavailable: boolean } | null>(null)

  // Show the exact requirements for the chosen category and year before anything is created.
  useEffect(() => {
    if (!category || !year) return setPreview({ s: 'idle' })
    let cancelled = false
    setPreview({ s: 'loading' })
    applicationsApi
      .preview(category, year)
      .then((data) => !cancelled && setPreview({ s: 'ready', data }))
      .catch((e: unknown) => {
        if (cancelled) return
        if (e instanceof ApiError && e.code === 'no_applicable_configuration') setPreview({ s: 'unavailable', message: e.message })
        else setPreview({ s: 'error', message: e instanceof ApiError ? e.message : 'The requirements could not be loaded.' })
      })
    return () => {
      cancelled = true
    }
  }, [category, year])

  async function create(e: FormEvent) {
    e.preventDefault()
    if (busy || preview.s !== 'ready') return
    setBusy(true)
    setFailure(null)
    try {
      const app = await applicationsApi.create(category, year)
      navigate(`/app/applications/${app.id}`, { replace: true })
    } catch (err) {
      setFailure({ message: err instanceof ApiError ? err.message : 'The draft could not be created.', unavailable: err instanceof ApiError && err.code === 'no_applicable_configuration' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        title="New application"
        description="Choose the application category and academic year. The requirements come from the approved configuration for that combination."
        crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Applications', to: '/app/applications' }, { label: 'New' }]}
      />
      <ResourceView resource={options} reload={reload}>
        {({ categories, academic_years }) =>
          categories.length === 0 || academic_years.length === 0 ? (
            <EmptyState title="No application types are available yet">
              No approved rule configuration has been published, so there is nothing to apply for. A rules administrator must publish an approved configuration first. This is not an error with your account.
            </EmptyState>
          ) : (
            <form className="new-app" onSubmit={create} noValidate>
              <Section title="1 · Application classification">
                <div className="new-app__fields">
                  <SelectField label="Application category" value={category} onChange={(e) => setCategory(e.target.value)} options={categories.map((c) => ({ value: c.id, label: c.name }))} hint={categories.find((c) => c.id === category)?.applicability_note ?? undefined} />
                  <SelectField label="Academic year" value={year} onChange={(e) => setYear(e.target.value)} options={academic_years.map((y) => ({ value: y, label: y }))} hint="Only years with an approved configuration are listed." />
                </div>
              </Section>

              {preview.s === 'loading' && <LoadingState label="Loading the applicable requirements…" />}
              {preview.s === 'unavailable' && (
                <Alert tone="warning" title="No approved configuration for this category and year">
                  {preview.message} If you try to start it, the request will be recorded for the rule configuration reviewers.
                </Alert>
              )}
              {preview.s === 'error' && <Alert tone="error">{preview.message}</Alert>}
              {preview.s === 'ready' && (
                <>
                  {preview.data.is_demo && <DemoBanner text={preview.data.demo_banner} />}
                  <Section
                    title="2 · What this application requires"
                    aside={<span className="mono muted">{preview.data.rule_set_key} · version {preview.data.version_number}</span>}
                  >
                    <VerificationNote verification={preview.data.verification} />
                    <ChecklistPanel items={preview.data.checklist} showStatus={false} />
                  </Section>
                  <Section title="Processing route">
                    <p className="note">The stages this category passes through under the configuration. Conditional stages apply only where the case calls for them.</p>
                    <RouteList stages={preview.data.route} />
                  </Section>
                  <Section title="Where this comes from">
                    <SourceList sources={preview.data.sources} />
                  </Section>
                </>
              )}
              {failure && (
                <Alert tone={failure.unavailable ? 'warning' : 'error'}>{failure.message}</Alert>
              )}
              <div className="sticky-actions">
                <Button type="submit" loading={busy} disabled={preview.s !== 'ready'}>
                  Create draft
                </Button>
                <span className="muted">A draft can be edited and resumed. Nothing is submitted until you review and confirm.</span>
              </div>
            </form>
          )
        }
      </ResourceView>
    </>
  )
}
