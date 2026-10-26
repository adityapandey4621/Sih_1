import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { category, demoItems, detail, docState, evidenceOf, resolved, summary } from '../../test/fixtures'
import { makeUser, mockApi, renderApp } from '../../test/helpers'
import { conditionMet } from './parts'

afterEach(() => vi.unstubAllGlobals())

const INSTITUTE = makeUser(['dashboard:institute', 'application:create', 'application:read_own', 'application:update_own', 'application:submit'], { role: 'institute_user', role_label: 'Institution' })
const session = { 'GET /api/auth/session': { body: INSTITUTE } }
const ERR = (status: number, code: string, detail: string, issues: unknown[] = []) => ({ status, body: { detail, code, issues } })

describe('conditionMet (generic evaluator for configuration data)', () => {
  it('applies only when the referenced answer matches', () => {
    const when = { item_key: 'kind', equals: 'B' }
    expect(conditionMet(null, {})).toBe(true)
    expect(conditionMet(when, {})).toBe(false)
    expect(conditionMet(when, { kind: '' })).toBe(false)
    expect(conditionMet(when, { kind: 'A' })).toBe(false)
    expect(conditionMet(when, { kind: 'B' })).toBe(true)
    expect(conditionMet({ item_key: 'kind', in: ['A', 'B'] }, { kind: 'A' })).toBe(true)
  })
})

describe('application list', () => {
  it('explains what to do when nothing has been started', async () => {
    mockApi({ ...session, 'GET /api/catalogue/options': { body: { categories: [category], academic_years: ['2026-27'] } }, 'GET /api/applications': { body: { items: [], total: 0, page: 1, page_size: 10 } } })
    renderApp('/app/applications')
    expect(await screen.findByText('You have not started any applications')).toBeInTheDocument()
    expect(screen.getByText(/You will see the exact requirements before anything is created/)).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: /new application|start an application/i }).length).toBeGreaterThan(0)
  })

  it('lists references, status, checklist progress and the next step, without invented statistics', async () => {
    mockApi({
      ...session,
      'GET /api/catalogue/options': { body: { categories: [category], academic_years: ['2026-27'] } },
      'GET /api/applications': {
        body: {
          items: [
            summary({ checklist: { required_total: 4, required_supplied: 1, optional_total: 0, optional_supplied: 0 }, outstanding_action: 'Supply 3 required items' }),
            summary({ id: 'app-2', reference: 'APP-CCCC-DDDD', status: 'SUBMITTED', submitted_at: '2026-10-27T10:00:00Z', outstanding_action: null, checklist: { required_total: 4, required_supplied: 4, optional_total: 0, optional_supplied: 0 } }),
          ],
          total: 2, page: 1, page_size: 10,
        },
      },
    })
    renderApp('/app/applications')
    const draftRow = (await screen.findByRole('link', { name: 'APP-AAAA-BBBB' })).closest('tr')!
    expect(within(draftRow).getByText('Draft')).toBeInTheDocument()
    expect(within(draftRow).getByText('1 / 4')).toBeInTheDocument()
    expect(within(draftRow).getByText('Supply 3 required items')).toBeInTheDocument()
    const doneRow = screen.getByRole('link', { name: 'APP-CCCC-DDDD' }).closest('tr')!
    expect(within(doneRow).getByText('Submitted')).toBeInTheDocument()
    expect(within(doneRow).getByText(/Later processing stages are not available/)).toBeInTheDocument()
  })

  it('passes filters to the server and says so when nothing matches', async () => {
    const api = mockApi({
      ...session,
      'GET /api/catalogue/options': { body: { categories: [category], academic_years: ['2026-27'] } },
      'GET /api/applications': { body: { items: [], total: 0, page: 1, page_size: 10 } },
    })
    renderApp('/app/applications?status=SUBMITTED&year=2026-27')
    expect(await screen.findByText('No applications match these filters')).toBeInTheDocument()
    const url = String(api.fn.mock.calls.map((c) => c[0]).find((u) => String(u).startsWith('/api/applications?')))
    expect(url).toContain('status=SUBMITTED')
    expect(url).toContain('academic_year=2026-27')
  })
})

describe('new application', () => {
  it('shows the requirements for the chosen category and year before anything is created, with the demo banner', async () => {
    const api = mockApi({
      ...session,
      'GET /api/catalogue/options': { body: { categories: [category], academic_years: ['2026-27'] } },
      'GET /api/catalogue/checklist': { body: resolved },
    })
    renderApp('/app/applications/new')
    await userEvent.selectOptions(await screen.findByLabelText('Application category'), 'cat-1')
    expect(screen.getByRole('button', { name: 'Create draft' })).toBeDisabled()
    await userEvent.selectOptions(screen.getByLabelText('Academic year'), '2026-27')
    expect(await screen.findByText('Main document')).toBeInTheDocument()
    expect(screen.getByText(/DEMO ONLY — NOT VERIFIED AS CURRENT OFFICIAL RULES/)).toBeInTheDocument()
    expect(screen.getAllByText('Explains the item.').length).toBe(5)
    expect(api.calls.some((c) => c.key === 'POST /api/applications')).toBe(false)
  })

  it('says plainly that there is no approved configuration instead of guessing', async () => {
    mockApi({
      ...session,
      'GET /api/catalogue/options': { body: { categories: [category], academic_years: ['2026-27'] } },
      'GET /api/catalogue/checklist': ERR(409, 'no_applicable_configuration', 'No single approved configuration applies. Nothing has been guessed.'),
    })
    renderApp('/app/applications/new')
    await userEvent.selectOptions(await screen.findByLabelText('Application category'), 'cat-1')
    await userEvent.selectOptions(screen.getByLabelText('Academic year'), '2026-27')
    expect(await screen.findByText('No approved configuration for this category and year')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create draft' })).toBeDisabled()
  })

  it('explains an empty catalogue as a configuration matter, not an error', async () => {
    mockApi({ ...session, 'GET /api/catalogue/options': { body: { categories: [], academic_years: [] } } })
    renderApp('/app/applications/new')
    expect(await screen.findByText('No application types are available yet')).toBeInTheDocument()
  })

  it('creates the draft and opens it', async () => {
    mockApi({
      ...session,
      'GET /api/catalogue/options': { body: { categories: [category], academic_years: ['2026-27'] } },
      'GET /api/catalogue/checklist': { body: resolved },
      'POST /api/applications': { status: 201, body: detail() },
      'GET /api/applications/app-1': { body: detail() },
    })
    const { router } = renderApp('/app/applications/new')
    await userEvent.selectOptions(await screen.findByLabelText('Application category'), 'cat-1')
    await userEvent.selectOptions(screen.getByLabelText('Academic year'), '2026-27')
    await userEvent.click(await screen.findByRole('button', { name: 'Create draft' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/app/applications/app-1'))
    expect(await screen.findByRole('heading', { level: 1, name: 'APP-AAAA-BBBB' })).toBeInTheDocument()
  })
})

describe('draft editor', () => {
  const open = (extra: Parameters<typeof mockApi>[0] = {}, d = detail()) => {
    const api = mockApi({ ...session, 'GET /api/applications/app-1': { body: d }, 'GET /api/applications/app-1/evidence': { body: evidenceOf() }, ...extra })
    renderApp('/app/applications/app-1')
    return api
  }

  it('reveals a conditional item as soon as its answer is chosen, and hides it again', async () => {
    open()
    await screen.findByLabelText('Contact person')
    expect(screen.queryByLabelText('Detail for B')).not.toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('Kind of change'), 'B')
    expect(screen.getByLabelText('Detail for B')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('Kind of change'), 'A')
    expect(screen.queryByLabelText('Detail for B')).not.toBeInTheDocument()
  })

  it('saves only the changed answers together with the row version it loaded', async () => {
    const saved = detail({ row_version: 2, form_data: { contact: 'R. Sharma' } })
    const api = open({ 'PATCH /api/applications/app-1': { body: saved } })
    await userEvent.type(await screen.findByLabelText('Contact person'), 'R. Sharma')
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Save draft' }))
    expect(await screen.findByText('All changes saved')).toBeInTheDocument()
    expect(api.calls.find((c) => c.key === 'PATCH /api/applications/app-1')?.body).toEqual({ form_data: { contact: 'R. Sharma' }, row_version: 1 })
  })

  it('shows server validation messages on the right fields and says nothing was saved', async () => {
    open({ 'PATCH /api/applications/app-1': ERR(422, 'validation_failed', 'Some values were not accepted.', [{ code: 'invalid_value', message: 'Enter at most 5 characters.', path: 'contact' }]) })
    await userEvent.type(await screen.findByLabelText('Contact person'), 'too long')
    await userEvent.click(screen.getByRole('button', { name: 'Save draft' }))
    expect(await screen.findByText('Enter at most 5 characters.')).toBeInTheDocument()
    expect(screen.getByText(/Nothing was saved/)).toBeInTheDocument()
  })

  it('offers a reload instead of overwriting when the draft changed elsewhere', async () => {
    open({ 'PATCH /api/applications/app-1': ERR(409, 'stale_version', 'This draft was changed elsewhere since you opened it.') })
    await userEvent.type(await screen.findByLabelText('Contact person'), 'x')
    await userEvent.click(screen.getByRole('button', { name: 'Save draft' }))
    expect(await screen.findByText(/changed elsewhere/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reload latest version' })).toBeInTheDocument()
  })

  it('shows the technical upload error, and an uploaded file as uploaded but never verified', async () => {
    const withFile = detail({
      documents: [{ id: 'd1', item_key: 'doc-main', original_filename: 'main.pdf', media_type: 'application/pdf', size_bytes: 2048, sha256: 'x', uploaded_at: '2026-10-26T10:00:00Z', uploaded_by: 'Test Person', removed: false, version_number: 1, is_current: true, verification_status: 'NOT_VERIFIED', state: docState({ code: 'extraction_queued', label: 'Extraction queued', tone: 'info', terminal: false }) }],
      checklist_items: demoItems.map((i) => (i.item_key === 'doc-main' ? { ...i, status: 'supplied' as const, document_id: 'd1' } : i)),
    })
    open({ 'POST /api/applications/app-1/documents': ERR(415, 'type_not_allowed', 'Only PDF, PNG and JPEG files are accepted, and the file’s content must match its type.') })
    await userEvent.click(await screen.findByRole('button', { name: /^2\s*Documents/ }))
    const input = screen.getByLabelText('Choose a file for Main document') as HTMLInputElement
    await userEvent.upload(input, new File(['hello'], 'notes.pdf', { type: 'application/pdf' }), { applyAccept: false })
    expect(await screen.findByRole('alert')).toHaveTextContent('Only PDF, PNG and JPEG files are accepted')

    vi.unstubAllGlobals()
    mockApi({ ...session, 'GET /api/applications/app-1': { body: withFile }, 'GET /api/applications/app-1/evidence': { body: evidenceOf() } })
    document.body.innerHTML = ''
    renderApp('/app/applications/app-1')
    await userEvent.click(await screen.findByRole('button', { name: /^2\s*Documents/ }))
    expect(await screen.findByText('main.pdf')).toBeInTheDocument()
    expect(screen.getByText('Extraction queued')).toBeInTheDocument() // the server's description of the file's technical state
    expect(screen.getByText('Contents not verified')).toBeInTheDocument()
    expect(screen.queryByText(/^Verified$/)).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Download/ })).toHaveAttribute('href', '/api/documents/d1/download')
  })

  it('blocks submission until required items are supplied and lists what is outstanding', async () => {
    open()
    await userEvent.click(await screen.findByRole('button', { name: /^3\s*Review/ }))
    expect(await screen.findByText(/required items? still to supply/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Submit application' })).toBeDisabled()
  })

  it('submits through a confirmation, then shows the reference and a read-only record', async () => {
    const ready = detail({ checklist_items: demoItems.map((i) => (i.applicable ? { ...i, status: 'supplied' as const } : i)), checklist: { required_total: 4, required_supplied: 4, optional_total: 0, optional_supplied: 0 } })
    const submitted = detail({ status: 'SUBMITTED', editable: false, submitted_at: '2026-10-27T10:00:00Z', submitted_by: 'Test Person', checklist_items: ready.checklist_items, row_version: 2 })
    const api = open({ 'POST /api/applications/app-1/submit': { body: submitted } }, ready)
    await userEvent.click(await screen.findByRole('button', { name: /^3\s*Review/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Submit application' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/cannot edit the answers or change the documents/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Submit application' }))
    expect(await screen.findByText('Application submitted')).toBeInTheDocument()
    expect(screen.getAllByText('APP-AAAA-BBBB').length).toBeGreaterThan(0)
    expect(screen.getByText(/This application is read-only/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save draft' })).not.toBeInTheDocument()
    expect(api.calls.find((c) => c.key === 'POST /api/applications/app-1/submit')?.body).toEqual({ row_version: 1 })
  })

  it('tells the user when a newer configuration applies and moves the draft only on request', async () => {
    const stale = detail({ current_rule_version_differs: true })
    const moved = detail({ current_rule_version_differs: false, rule_version: { ...detail().rule_version, number: 2 } })
    const api = open({ 'POST /api/applications/app-1/rebind': { body: moved } }, stale)
    expect(await screen.findByText('A newer approved configuration applies')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Update to current requirements' }))
    await waitFor(() => expect(screen.queryByText('A newer approved configuration applies')).not.toBeInTheDocument())
    expect(api.calls.some((c) => c.key === 'POST /api/applications/app-1/rebind')).toBe(true)
  })

  it('shows submission blockers returned by the server when it refuses', async () => {
    const ready = detail({ checklist_items: demoItems.map((i) => (i.applicable ? { ...i, status: 'supplied' as const } : i)) })
    open({ 'POST /api/applications/app-1/submit': ERR(422, 'submission_blocked', 'The application cannot be submitted until every required item is supplied.', [{ code: 'missing', message: '“Main document” is required.', path: 'doc-main' }]) }, ready)
    await userEvent.click(await screen.findByRole('button', { name: /^3\s*Review/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Submit application' }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Submit application' }))
    expect(await screen.findByText('The server did not accept the submission')).toBeInTheDocument()
    expect(screen.getByText('“Main document” is required.')).toBeInTheDocument()
  })
})

describe('permissions in the interface (the backend enforces them regardless)', () => {
  it('shows no application navigation or create action to a role without application permissions', async () => {
    mockApi({ 'GET /api/auth/session': { body: makeUser(['dashboard:scrutiny']) } })
    renderApp('/app')
    await screen.findByRole('heading', { name: 'Scrutiny officer dashboard' })
    expect(within(screen.getByRole('navigation', { name: 'Main' })).queryByText('Applications')).not.toBeInTheDocument()
    expect(screen.getByText('Planned — not available in this phase')).toBeInTheDocument()
  })

  it('denies the applications screens to a role without the permission', async () => {
    mockApi({ 'GET /api/auth/session': { body: makeUser(['dashboard:scrutiny']) } })
    renderApp('/app/applications/new')
    expect(await screen.findByText('You do not have access to this page')).toBeInTheDocument()
  })
})
