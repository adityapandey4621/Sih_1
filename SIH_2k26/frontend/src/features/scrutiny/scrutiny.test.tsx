import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { caseDetail, detail, docState, evidenceOf, findingOf, instItem, instNotice, itemEvidence, queueItem, reviewFinding, staffNotice, staffNoticeItem } from '../../test/fixtures'
import { makeUser, mockApi, renderApp } from '../../test/helpers'
import type { NoticePreview } from './types'

afterEach(() => {
  vi.unstubAllGlobals()
})

const REVIEWER = makeUser(['dashboard:scrutiny', 'evidence:read', 'document:retry', 'scrutiny:queue', 'scrutiny:work', 'notifications:read'], { role: 'scrutiny_officer', role_label: 'Scrutiny officer', full_name: 'Test Reviewer' })
const COORDINATOR = makeUser(['dashboard:scrutiny', 'scrutiny:queue', 'scrutiny:assign', 'scrutiny:route_determine', 'notifications:read'], { role: 'scrutiny_coordinator', role_label: 'Scrutiny coordinator' })
const ISSUER = makeUser(['dashboard:scrutiny', 'evidence:read', 'scrutiny:queue', 'notice:issue', 'notifications:read'], { role: 'notice_issuer', role_label: 'Notice issuer' })
const INSTITUTE = makeUser(['dashboard:institute', 'application:read_own', 'notice:read_own', 'notice:respond', 'notifications:read'], { role: 'institute_user', role_label: 'Institution' })
const UNREAD = { 'GET /api/notifications': { body: { items: [], total: 0, unread: 0, page: 1, page_size: 20 } } }
const ERR = (status: number, code: string, message: string) => ({ status, body: { detail: message, code, issues: [] } })

const queuePage = (items = [queueItem()], over: Record<string, unknown> = {}) => ({
  items, total: items.length, page: 1, page_size: 20, scope: 'reviewer', processing_exceptions: 0,
  counts: { UNDER_SCRUTINY: items.length, AWAITING_ASSIGNMENT: 0 },
  state_labels: { AWAITING_ASSIGNMENT: 'Awaiting assignment', UNDER_SCRUTINY: 'Under scrutiny', SCRUTINY_COMPLETED: 'Scrutiny completed' }, ...over,
})

describe('scrutiny queue', () => {
  it('lists real cases with state, assignee, outstanding actions, rule version and processing, and states its scope', async () => {
    mockApi({
      'GET /api/auth/session': { body: REVIEWER }, ...UNREAD,
      'GET /api/scrutiny/cases': { body: queuePage([queueItem({ processing: { documents: 2, still_processing: 1, failed: 1, review_recommended: 0 } })]) },
    })
    renderApp('/app/scrutiny')
    const link = await screen.findByRole('link', { name: 'APP-AAAA-BBBB' })
    const row = link.closest('tr')!
    expect(link).toHaveAttribute('href', '/app/scrutiny/case-1')
    expect(within(row).getByText('Test Institute')).toBeInTheDocument()
    expect(within(row).getByText('Under scrutiny')).toBeInTheDocument()
    expect(within(row).getByText('Test Reviewer')).toBeInTheDocument()
    expect(within(row).getByText('Record findings or complete the round')).toBeInTheDocument()
    expect(within(row).getByText('DEMO ONLY')).toBeInTheDocument()
    expect(within(row).getByText('1 processing')).toBeInTheDocument()
    expect(within(row).getByText('1 failed')).toBeInTheDocument()
    expect(within(row).queryByText(/deadline/i)).not.toBeInTheDocument() // no due date is invented: only a dash
    expect(screen.getByText(/only the cases that were assigned to you/)).toBeInTheDocument()
    expect(screen.getByText(/never an approval/)).toBeInTheDocument()
  })

  it('shows a due date only when the case has one, with its basis', async () => {
    mockApi({
      'GET /api/auth/session': { body: REVIEWER }, ...UNREAD,
      'GET /api/scrutiny/cases': { body: queuePage([queueItem({ state: 'AWAITING_RESPONSE', state_label: 'Notice issued — awaiting response', due_at: '2026-11-03T10:00:00Z', due_basis: 'DEMO ONLY — Response period from the approved configuration' })]) },
    })
    renderApp('/app/scrutiny')
    expect(await screen.findByText(/DEMO ONLY — Response period/)).toBeInTheDocument()
  })

  it('filters by state through the server and shows real counts', async () => {
    const api = mockApi({ 'GET /api/auth/session': { body: COORDINATOR }, ...UNREAD, 'GET /api/scrutiny/cases': { body: queuePage([queueItem()], { scope: 'coordinator', counts: { AWAITING_ASSIGNMENT: 4, UNDER_SCRUTINY: 1 } }) } })
    renderApp('/app/scrutiny')
    const chip = await screen.findByRole('button', { name: /Awaiting assignment 4/ })
    expect(screen.getByRole('button', { name: /All 5/ })).toBeInTheDocument()
    await userEvent.click(chip)
    await waitFor(() => expect(api.calls.some((c) => c.url.includes('state=AWAITING_ASSIGNMENT'))).toBe(true))
  })

  it('has an empty state that explains whose cases appear', async () => {
    mockApi({ 'GET /api/auth/session': { body: REVIEWER }, ...UNREAD, 'GET /api/scrutiny/cases': { body: queuePage([], { counts: {} }) } })
    renderApp('/app/scrutiny')
    expect(await screen.findByText('No cases in your scope yet')).toBeInTheDocument()
  })

  it('is not available to an institution user', async () => {
    mockApi({ 'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD })
    renderApp('/app/scrutiny/case-1')
    expect(await screen.findByText('You do not have access to this page')).toBeInTheDocument()
  })

  it('shows the error state with a retry when the queue cannot load', async () => {
    mockApi({ 'GET /api/auth/session': { body: REVIEWER }, ...UNREAD, 'GET /api/scrutiny/cases': ERR(500, 'x', 'The server is temporarily unavailable.') })
    renderApp('/app/scrutiny')
    expect(await screen.findByText('The server is temporarily unavailable.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})

const evidenceFor = () =>
  evidenceOf({
    viewer: 'reviewer', status: 'SUBMITTED', institution_name: 'Test Institute',
    items: [itemEvidence({ findings: [findingOf({ priority: 'medium', id: 'pf-1' })] })],
  })
const viewerMocks = {
  'GET /api/scrutiny/cases/case-1/evidence': { body: evidenceFor() },
  'GET /api/documents/d1/extraction': { body: { document_id: 'd1', version_number: 1, state: docState(), fields: [], pages: [{ page_number: 1, method: 'PDF_TEXT', status: 'OK', confidence: null, char_count: 40, note: null, text: 'Name of the Institution: Test Institute' }] } },
  'GET /api/documents/d1/history': { body: [] },
}

describe('reviewer workspace', () => {
  const open = (detailBody = caseDetail(), extra: Parameters<typeof mockApi>[0] = {}) => {
    const api = mockApi({ 'GET /api/auth/session': { body: REVIEWER }, ...UNREAD, 'GET /api/scrutiny/cases/case-1': { body: detailBody }, ...viewerMocks, ...extra })
    renderApp('/app/scrutiny/case-1')
    return api
  }

  it('shows the original page beside the extracted data and offers findings, not decisions', async () => {
    open()
    expect(await screen.findByAltText('Page 1 of main.pdf')).toHaveAttribute('src', '/api/documents/d1/pages/1/preview')
    expect(screen.getByRole('button', { name: 'Record a finding about page 1' })).toBeInTheDocument()
    expect(screen.getByText(/Completing scrutiny is not an approval/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /approve|reject|issue (a )?notice|accept|verify|record (a )?decision/i })).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /Evidence/ })).toBeInTheDocument()
  })

  it('records a finding tied to the exact version and page being viewed, with internal and institution wording kept apart', async () => {
    const created = caseDetail({ findings: [reviewFinding()] })
    const api = open(caseDetail(), { 'POST /api/scrutiny/cases/case-1/findings': { status: 201, body: created } })
    await userEvent.click(await screen.findByRole('button', { name: 'Record a finding about page 1' }))
    const form = await screen.findByRole('form', { name: 'Record a finding' })
    expect(within(form).getByText('Evidence this finding refers to')).toBeInTheDocument()
    expect(within(form).getByText('v1')).toBeInTheDocument() // the exact version being viewed
    expect(within(form).getByLabelText(/^Page/)).toHaveValue(1)
    await userEvent.type(within(form).getByLabelText(/Your description/), 'The letter does not show a validity period.')
    await userEvent.click(within(form).getByLabelText('May be included in a notice to the institution'))
    await userEvent.type(within(form).getByLabelText('What the institution is told'), 'Please show the validity period.')
    await userEvent.type(within(form).getByLabelText(/asked to supply/), 'Upload a letter that states it.')
    await userEvent.click(within(form).getByRole('button', { name: 'Record finding' }))
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/scrutiny/cases/case-1/findings')).toBe(true))
    const body = api.calls.find((c) => c.key === 'POST /api/scrutiny/cases/case-1/findings')!.body as Record<string, unknown>
    expect(body).toMatchObject({ item_key: 'doc-main', document_id: 'd1', page_number: 1, visibility: 'ELIGIBLE_FOR_NOTICE', institution_text: 'Please show the validity period.', issue_type: 'incomplete_information' })
    expect(body.description).toContain('validity period')
    expect(await screen.findByText('Finding 1 — Incomplete information')).toBeInTheDocument()
  })

  it('keeps an automated preliminary issue separate: it becomes a finding only when the reviewer records one', async () => {
    const api = open(caseDetail(), { 'POST /api/scrutiny/cases/case-1/findings': { status: 201, body: caseDetail({ findings: [reviewFinding({ preliminary_finding_id: 'pf-1' })] }) } })
    await userEvent.click(await screen.findByRole('tab', { name: /Automated issues \(1\)/ })) // the viewer's own tab, named so it cannot be mistaken for the reviewer's findings
    expect(await screen.findByText('Potential issue — awaiting scrutiny review')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Record my own finding about this' }))
    const form = await screen.findByRole('form', { name: 'Record a finding' })
    await userEvent.type(within(form).getByLabelText(/Your description/), 'I looked at it and it is a real mismatch.')
    await userEvent.click(within(form).getByRole('button', { name: 'Record finding' }))
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/scrutiny/cases/case-1/findings')).toBe(true))
    expect((api.calls.find((c) => c.key === 'POST /api/scrutiny/cases/case-1/findings')!.body as Record<string, unknown>).preliminary_finding_id).toBe('pf-1')
  })

  it('labels the reviewer’s internal description as internal and the notice wording as the institution’s', async () => {
    open(caseDetail({ findings: [reviewFinding()] }))
    await userEvent.click(await screen.findByRole('tab', { name: /Findings/ }))
    expect(await screen.findByText('Reviewer’s description — internal')).toBeInTheDocument()
    expect(screen.getByText('Wording for the institution')).toBeInTheDocument()
    expect(screen.getByText('Reviewer finding')).toBeInTheDocument()
    expect(screen.getByText('May go into a notice')).toBeInTheDocument()
  })

  it('needs a reason to conclude a finding and sends the finding’s version', async () => {
    const api = open(caseDetail({ findings: [reviewFinding({ row_version: 4 })] }), {
      'POST /api/scrutiny/cases/case-1/findings/sf-1/disposition': { body: caseDetail({ findings: [reviewFinding({ state: 'RESOLVED', state_reason: 'Checked the original letter.' })] }) },
    })
    await userEvent.click(await screen.findByRole('tab', { name: /Findings/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Resolved' }))
    const confirm = await screen.findByRole('button', { name: 'Record as resolved' })
    expect(confirm).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/Why is this resolved/), 'Checked the original letter.')
    expect(confirm).toBeEnabled()
    await userEvent.click(confirm)
    await waitFor(() => expect(api.calls.some((c) => c.key.endsWith('/disposition'))).toBe(true))
    expect(api.calls.find((c) => c.key.endsWith('/disposition'))!.body).toEqual({ state: 'RESOLVED', reason: 'Checked the original letter.', row_version: 4 })
  })

  it('shows that a finding refers to a version that has since been replaced', async () => {
    open(caseDetail({ findings: [reviewFinding({ document_superseded: true, document_current_version: 2 })] }))
    await userEvent.click(await screen.findByRole('tab', { name: /Findings/ }))
    expect(await screen.findByText(/since replaced — the current version is 2/)).toBeInTheDocument()
  })

  it('prepares a notice only from findings that may go into one, and says preparing does not issue it', async () => {
    const api = open(caseDetail({ findings: [reviewFinding(), reviewFinding({ id: 'sf-2', sequence: 2, visibility: 'INTERNAL_ONLY', institution_text: null, requested_correction: null })] }), {
      'POST /api/scrutiny/cases/case-1/notices': { status: 201, body: caseDetail({ findings: [reviewFinding()], notices: [staffNotice()], allowed_actions: ['record_finding', 'conclude_finding', 'complete', 'edit_notice', 'submit_notice', 'withdraw_notice'] }) },
    })
    await userEvent.click(await screen.findByRole('tab', { name: /Findings/ }))
    expect(await screen.findAllByRole('checkbox', { name: /Include in a notice/ })).toHaveLength(1) // the internal-only finding cannot be selected
    const prepare = screen.getByRole('button', { name: /Prepare a notice from/ })
    expect(prepare).toBeDisabled()
    await userEvent.click(screen.getByRole('checkbox', { name: /Include in a notice/ }))
    expect(screen.getByText(/Preparing a notice does not issue it/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Prepare a notice from 1 finding/ }))
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/scrutiny/cases/case-1/notices')).toBe(true))
    expect(await screen.findByText('Notice draft — not issued')).toBeInTheDocument()
    expect(await screen.findByLabelText('Explanation to the institution')).toBeInTheDocument() // the draft opens in the editor: the wording is the reviewer's to check
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.getByRole('button', { name: /Submit for an issuing decision/ })).toBeInTheDocument() // the next step is a decision by someone else, not issuing
    expect(screen.queryByRole('button', { name: /^Issue/ })).not.toBeInTheDocument()
  })

  it('offers no way to issue a notice to the reviewer, even for a notice awaiting a decision', async () => {
    open(caseDetail({ queue: queueItem({ state: 'AWAITING_NOTICE_DECISION', state_label: 'Awaiting notice decision' }), notices: [staffNotice({ status: 'IN_REVIEW' })], allowed_actions: ['withdraw_notice'] }))
    await userEvent.click(await screen.findByRole('tab', { name: /Notices/ }))
    expect(await screen.findByText('Awaiting issuing decision')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Inspect and decide|Issue this notice/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Withdraw this prepared notice/ })).toBeInTheDocument()
  })

  it('starts re-scrutiny from the banner and sends the case version', async () => {
    const api = open(caseDetail({ queue: queueItem({ state: 'AWAITING_RESCRUTINY', state_label: 'Response received — awaiting re-scrutiny', row_version: 9 }), allowed_actions: ['start'] }), {
      'POST /api/scrutiny/cases/case-1/start': { body: caseDetail({ queue: queueItem({ state: 'UNDER_RESCRUTINY', state_label: 'Under re-scrutiny', row_version: 10 }) }) },
    })
    await userEvent.click(await screen.findByRole('button', { name: 'Start re-scrutiny' }))
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/scrutiny/cases/case-1/start')).toBe(true))
    expect(api.calls.find((c) => c.key.endsWith('/start'))!.body).toEqual({ row_version: 9 })
    expect(await screen.findAllByText('Under re-scrutiny')).not.toHaveLength(0)
  })

  it('tells the person plainly when the case was changed by someone else, and reloads it', async () => {
    let loads = 0
    const api = mockApi({
      'GET /api/auth/session': { body: REVIEWER }, ...UNREAD, ...viewerMocks,
      'GET /api/scrutiny/cases/case-1': () => ({ body: caseDetail({ queue: queueItem({ row_version: ++loads }) }) }),
      'PUT /api/scrutiny/cases/case-1/notes': ERR(409, 'stale_version', 'These notes were changed by someone else since you opened them.'),
    })
    renderApp('/app/scrutiny/case-1')
    await userEvent.click(await screen.findByRole('tab', { name: /Case, assignment and history/ }))
    await userEvent.type(await screen.findByLabelText(/Working notes/), 'my notes')
    await userEvent.click(screen.getByRole('button', { name: 'Save progress' }))
    expect(await screen.findByText('The case changed')).toBeInTheDocument()
    expect(screen.getByText(/changed by someone else/)).toBeInTheDocument()
    await waitFor(() => expect(loads).toBeGreaterThan(1)) // the latest version was loaded
    expect(api.calls.filter((c) => c.key === 'GET /api/scrutiny/cases/case-1').length).toBeGreaterThan(1)
  })

  it('shows a not-found state for a case outside the reviewer’s scope', async () => {
    mockApi({ 'GET /api/auth/session': { body: REVIEWER }, ...UNREAD, 'GET /api/scrutiny/cases/case-9': ERR(404, 'not_found', 'Scrutiny case not found.') })
    renderApp('/app/scrutiny/case-9')
    expect(await screen.findByText('Case not found')).toBeInTheDocument()
  })

  it('completes scrutiny only with a recorded summary and states it is not an approval', async () => {
    const api = open(caseDetail(), {
      'POST /api/scrutiny/cases/case-1/complete': { body: caseDetail({ queue: queueItem({ state: 'SCRUTINY_COMPLETED', state_label: 'Scrutiny completed' }), allowed_actions: [] }) },
    })
    await userEvent.click(await screen.findByRole('tab', { name: /Findings/ }))
    const btn = await screen.findByRole('button', { name: 'Complete scrutiny…' })
    expect(btn).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/Summary of the outcome/), 'Reviewed; nothing to raise.')
    await userEvent.click(btn)
    expect(await screen.findByText(/The application is not approved or rejected by this step/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Complete' }))
    await waitFor(() => expect(api.calls.some((c) => c.key.endsWith('/complete'))).toBe(true))
    expect(api.calls.find((c) => c.key.endsWith('/complete'))!.body).toEqual({ row_version: 3, summary: 'Reviewed; nothing to raise.', acknowledge_unresolved: false })
  })
})

describe('coordinator and issuer views', () => {
  it('gives the coordinator the queue and assignment but no evidence, findings or notices', async () => {
    mockApi({ 'GET /api/auth/session': { body: COORDINATOR }, ...UNREAD, 'GET /api/scrutiny/cases/case-1': { body: caseDetail({ scope: ['coordinator'], allowed_actions: ['assign'], queue: queueItem({ state: 'AWAITING_ASSIGNMENT', state_label: 'Awaiting assignment', assignees: [] }) }) } })
    renderApp('/app/scrutiny/case-1')
    expect(await screen.findByText('Application summary')).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: /Evidence/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: /Findings/ })).not.toBeInTheDocument()
    expect(screen.getByText(/NOT the official AICTE committee selection/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Assign a reviewer…' })).toBeInTheDocument()
  })

  it('does not offer assignment where no authorised selection interface exists', async () => {
    mockApi({
      'GET /api/auth/session': { body: COORDINATOR }, ...UNREAD,
      'GET /api/scrutiny/cases/case-1': { body: caseDetail({ scope: ['coordinator'], allowed_actions: [], selection: { mode: 'OFFICIAL_SELECTION', available: false, label: 'Official selection (unavailable)', notice: 'No interface to that process is configured, so no assignment can be recorded here.' }, queue: queueItem({ state: 'AWAITING_ASSIGNMENT', assignees: [] }) }) },
    })
    renderApp('/app/scrutiny/case-1')
    expect(await screen.findByText('Official selection (unavailable)')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Assign a reviewer/ })).not.toBeInTheDocument()
  })

  const preview = (over: Partial<NoticePreview> = {}): NoticePreview => ({
    notice_id: 'n-1', content_hash: 'c'.repeat(64), row_version: 2, notice_status: 'IN_REVIEW', can_issue: true, blockers: [], unsourced_positions: [],
    policy: { state: 'NO_DURATION', message: 'The policy exists but no response period is established by a verified source. No due date has been set.', policy_key: 'p', duration_value: null, duration_unit: null, calendar_convention: null, demo: false, source: null },
    content: {
      reference: 'DN-AAAA-BBBB', sequence: 1, previous_notice_reference: null, application: { reference: 'APP-AAAA-BBBB', institution: 'Test Institute', category: 'Category A', academic_year: '2026-27' },
      rule_version: { rule_set: 'set-a', number: 1, classification: 'DEMO' }, demo_notice: 'DEMO ONLY — NOT VERIFIED AS CURRENT OFFICIAL RULES',
      template: { key: 'demo-notice-v1', label: 'DEMO ONLY — demonstration template', demo: true }, summary: 'We need the validity period.',
      items: [{ position: 1, title: 'Main document', description: 'Please show the validity period.', requested_correction: 'Upload a letter that states it.', item_key: 'doc-main', document_version: 1, page_number: 1, source_ref: null, source_ref_status: 'NEEDS_VERIFICATION' }],
    }, ...over,
  })
  const issuerCase = () => caseDetail({ scope: ['issuer'], allowed_actions: ['issue_notice', 'return_notice'], queue: queueItem({ state: 'AWAITING_NOTICE_DECISION', state_label: 'Awaiting notice decision' }), notices: [staffNotice({ status: 'IN_REVIEW', row_version: 2 })] })

  it('requires inspection, a reason and a confirmation before issuing, and sends the confirmed content with an idempotency key', async () => {
    const api = mockApi({
      'GET /api/auth/session': { body: ISSUER }, ...UNREAD, 'GET /api/scrutiny/cases/case-1': { body: issuerCase() }, 'GET /api/scrutiny/notices/n-1/preview': { body: preview({ unsourced_positions: [1] }) },
      'POST /api/scrutiny/notices/n-1/issue': { body: caseDetail({ scope: ['issuer'], allowed_actions: [], notices: [staffNotice({ status: 'ISSUED', issued_by: 'Issuer', issued_at: '2026-10-27T10:00:00Z' })] }) },
    })
    renderApp('/app/scrutiny/case-1')
    await userEvent.click(await screen.findByRole('tab', { name: /Notices/ }))
    expect(screen.queryByRole('tab', { name: /Case, assignment/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Record a finding' })).not.toBeInTheDocument()
    await userEvent.click(await screen.findByRole('button', { name: 'Inspect and decide…' }))
    const article = await screen.findByLabelText('Notice as it would be issued')
    expect(within(article).getByText('Please show the validity period.')).toBeInTheDocument()
    expect(within(article).getByText(/No source reference established/)).toBeInTheDocument()
    expect(within(article).getByText(/No due date has been set/)).toBeInTheDocument() // no invented deadline
    const issue = screen.getByRole('button', { name: 'Issue this notice…' })
    expect(issue).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/Your reason for issuing/), 'Inspected the notice and the evidence.')
    expect(issue).toBeDisabled() // still needs the read confirmation and the unsourced acknowledgement
    await userEvent.click(screen.getByLabelText(/I have read this notice/))
    await userEvent.click(screen.getByLabelText(/has no established source reference/))
    expect(issue).toBeEnabled()
    await userEvent.click(issue)
    expect(await screen.findByText('Issue this formal deficiency notice?')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Issue notice' }))
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/scrutiny/notices/n-1/issue')).toBe(true))
    const body = api.calls.find((c) => c.key.endsWith('/issue'))!.body as Record<string, unknown>
    expect(body).toMatchObject({ content_hash: 'c'.repeat(64), issue_reason: 'Inspected the notice and the evidence.', acknowledge_unsourced: true, row_version: 2 })
    expect(String(body.idempotency_key).length).toBeGreaterThanOrEqual(8)
    expect(await screen.findByText('Issued — awaiting response')).toBeInTheDocument()
  })

  it('shows why a notice cannot be issued and keeps issuing unavailable', async () => {
    mockApi({
      'GET /api/auth/session': { body: ISSUER }, ...UNREAD, 'GET /api/scrutiny/cases/case-1': { body: issuerCase() },
      'GET /api/scrutiny/notices/n-1/preview': { body: preview({ can_issue: false, blockers: [{ code: 'response_policy_unresolved', message: 'The response period cannot be established: no due date will be invented.' }] }) },
    })
    renderApp('/app/scrutiny/case-1')
    await userEvent.click(await screen.findByRole('tab', { name: /Notices/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Inspect and decide…' }))
    expect(await screen.findByText('This notice cannot be issued yet')).toBeInTheDocument()
    expect(screen.getByText(/no due date will be invented/)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText(/Your reason for issuing/), 'Trying anyway.')
    await userEvent.click(screen.getByLabelText(/I have read this notice/))
    expect(screen.getByRole('button', { name: 'Issue this notice…' })).toBeDisabled()
  })

  it('lets the issuer return the notice with a reason', async () => {
    const api = mockApi({
      'GET /api/auth/session': { body: ISSUER }, ...UNREAD, 'GET /api/scrutiny/cases/case-1': { body: issuerCase() }, 'GET /api/scrutiny/notices/n-1/preview': { body: preview() },
      'POST /api/scrutiny/notices/n-1/return': { body: caseDetail({ scope: ['issuer'], allowed_actions: [], notices: [staffNotice({ status: 'DRAFT', returned_reason: 'Be more specific.' })] }) },
    })
    renderApp('/app/scrutiny/case-1')
    await userEvent.click(await screen.findByRole('tab', { name: /Notices/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Inspect and decide…' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Return to the reviewer…' }))
    const send = await screen.findByRole('button', { name: 'Return notice' })
    expect(send).toBeDisabled()
    await userEvent.type(screen.getByLabelText('What needs to change?'), 'Be more specific.')
    await userEvent.click(send)
    await waitFor(() => expect(api.calls.some((c) => c.key.endsWith('/return'))).toBe(true))
    expect(api.calls.find((c) => c.key.endsWith('/return'))!.body).toEqual({ reason: 'Be more specific.' })
  })
})

describe('institution: notices and responses', () => {
  const open = (notice = instNotice(), extra: Parameters<typeof mockApi>[0] = {}) => {
    const api = mockApi({ 'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD, 'GET /api/notices/n-1': { body: notice }, ...extra })
    renderApp('/app/notices/n-1')
    return api
  }

  it('shows the issued notice with what is requested and no invented deadline, and never any internal field', async () => {
    open()
    expect(await screen.findByText('Deficiency notice DN-AAAA-BBBB')).toBeInTheDocument()
    expect(screen.getByText('Upload a letter that states it.')).toBeInTheDocument()
    expect(screen.getAllByText(/No response deadline has been established/).length).toBeGreaterThan(0)
    expect(screen.getByText('Formal deficiency notice — issued')).toBeInTheDocument()
    expect(screen.getByText(/DEMO ONLY — NOT VERIFIED/)).toBeInTheDocument()
    expect(screen.getByText(/It is not a rejection/)).toBeInTheDocument()
    expect(screen.queryByText(/INTERNAL/)).not.toBeInTheDocument()
    expect(screen.getByText('Not responded')).toBeInTheDocument()
  })

  it('shows the deadline when the server has established one', async () => {
    open(instNotice({ response_due_at: '2026-11-03T10:00:00Z', due_note: 'DEMO ONLY — Respond by the date shown.' }))
    expect((await screen.findAllByText(/Respond by/)).length).toBeGreaterThan(0)
    expect(screen.getByText('DEMO ONLY — Respond by the date shown.')).toBeInTheDocument()
  })

  it('uploads corrected evidence as a new version for an item, and says earlier versions are kept', async () => {
    const after = instNotice({ items: [instItem({ response_documents: [{ document_id: 'd2', version_number: 2, filename: 'corrected.pdf', uploaded_at: '2026-10-28T10:00:00Z', current: true, state_label: 'File received' }] })], response: { status: 'DRAFT', explanation: '', row_version: 1, submitted_at: null, submitted_by: null } })
    const api = open(instNotice(), { 'POST /api/notices/n-1/items/ni-1/documents': { status: 201, body: after } })
    expect(await screen.findByText(/Your earlier versions are kept/)).toBeInTheDocument()
    await userEvent.upload(screen.getByLabelText('Choose a corrected file for item 1'), new File(['%PDF-1.4'], 'corrected.pdf', { type: 'application/pdf' }), { applyAccept: false })
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/notices/n-1/items/ni-1/documents')).toBe(true))
    expect(await screen.findByText('corrected.pdf · version 2')).toBeInTheDocument()
    expect(screen.getByText(/added to item 1 as a new version/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Download/ })).toHaveAttribute('href', '/api/documents/d2/download')
  })

  it('saves a draft, then submits with an idempotency key, and calls the response received — not accepted', async () => {
    const saved = instNotice({ response: { status: 'DRAFT', explanation: '', row_version: 2, submitted_at: null, submitted_by: null }, items: [instItem({ response_state: 'DRAFT', response_explanation: 'The letter is enclosed.' })] })
    const submitted = instNotice({ status: 'RESPONSE_RECEIVED', status_label: 'Response submitted — awaiting re-scrutiny', can_respond: false, response: { status: 'SUBMITTED', explanation: '', row_version: 3, submitted_at: '2026-10-28T10:00:00Z', submitted_by: 'Test Person' }, items: [instItem({ response_state: 'SUBMITTED', response_explanation: 'The letter is enclosed.' })] })
    const api = open(instNotice(), { 'PUT /api/notices/n-1/response': { body: saved }, 'POST /api/notices/n-1/response/submit': { body: submitted } })
    const submit = await screen.findByRole('button', { name: 'Submit response…' })
    expect(submit).toBeDisabled() // nothing answered yet
    await userEvent.type(screen.getByLabelText('Your response to item 1'), 'The letter is enclosed.')
    expect(submit).toBeEnabled()
    await userEvent.click(submit)
    expect(await screen.findByText(/received for re-scrutiny — not accepted/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Submit response' }))
    expect(await screen.findByText('Your response was submitted')).toBeInTheDocument()
    expect(screen.getByText(/re-scrutiny will examine it/)).toBeInTheDocument()
    const put = api.calls.find((c) => c.key === 'PUT /api/notices/n-1/response')!.body as Record<string, unknown>
    expect(put).toMatchObject({ items: [{ notice_item_id: 'ni-1', explanation: 'The letter is enclosed.' }], row_version: null })
    const sub = api.calls.find((c) => c.key === 'POST /api/notices/n-1/response/submit')!.body as Record<string, unknown>
    expect(sub.row_version).toBe(2)
    expect(String(sub.idempotency_key).length).toBeGreaterThanOrEqual(8)
    expect(screen.queryByRole('button', { name: /Submit response/ })).not.toBeInTheDocument() // read-only afterwards
    expect(screen.queryByLabelText('Your response to item 1')).not.toBeInTheDocument()
  })

  it('is read-only once the response is submitted', async () => {
    open(instNotice({ status: 'RESPONSE_RECEIVED', status_label: 'Response submitted — awaiting re-scrutiny', can_respond: false, response: { status: 'SUBMITTED', explanation: 'General.', row_version: 3, submitted_at: '2026-10-28T10:00:00Z', submitted_by: 'Test Person' }, items: [instItem({ response_state: 'SUBMITTED', response_explanation: 'Our answer.' })] }))
    expect(await screen.findByText('Our answer.')).toBeInTheDocument()
    expect(screen.getByText('Submitted — awaiting re-scrutiny')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Save draft|Submit response/ })).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/Choose a corrected file/)).not.toBeInTheDocument()
  })

  it('shows the server’s message when the notice is no longer open, instead of pretending it worked', async () => {
    open(instNotice(), { 'PUT /api/notices/n-1/response': ERR(409, 'notice_not_open', 'This notice is no longer open for a response.') })
    await userEvent.type(await screen.findByLabelText('Your response to item 1'), 'Late answer')
    await userEvent.click(screen.getByRole('button', { name: 'Save draft' }))
    expect(await screen.findByText('This notice is no longer open for a response.')).toBeInTheDocument()
    expect(screen.queryByText(/Draft saved/)).not.toBeInTheDocument()
  })

  it('says a notice is not found when it is not the institution’s own', async () => {
    mockApi({ 'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD, 'GET /api/notices/n-2': ERR(404, 'not_found', 'Notice not found.') })
    renderApp('/app/notices/n-2')
    expect(await screen.findByText('Notice not found')).toBeInTheDocument()
  })

  it('lists notices with their real status and deadline (or that none exists)', async () => {
    mockApi({
      'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD,
      'GET /api/notices': { body: [{ id: 'n-1', reference: 'DN-AAAA-BBBB', sequence: 1, status: 'ISSUED', status_label: 'Issued — response awaited', application_id: 'app-1', application_reference: 'APP-AAAA-BBBB', issued_at: '2026-10-27T10:00:00Z', response_due_at: null, items: 2, items_answered: 0 }] },
    })
    renderApp('/app/notices')
    expect(await screen.findByRole('link', { name: 'DN-AAAA-BBBB' })).toHaveAttribute('href', '/app/notices/n-1')
    expect(screen.getByText('No deadline established')).toBeInTheDocument()
    expect(screen.getByText('A response is needed')).toBeInTheDocument()
    expect(within(screen.getByRole('navigation', { name: 'Main' })).getByText('Notices')).toBeInTheDocument()
  })

  it('shows the scrutiny status and the open notice on a submitted application', async () => {
    mockApi({
      'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD,
      'GET /api/applications/app-1': { body: detail({ status: 'SUBMITTED', editable: false, documents: [] }) },
      'GET /api/applications/app-1/evidence': { body: evidenceOf({ status: 'SUBMITTED' }) },
      'GET /api/applications/app-1/scrutiny': {
        body: {
          application_id: 'app-1', state_label: 'Deficiency notice issued — your response is awaited', explanation: 'A formal deficiency notice has been issued.',
          events: [{ at: '2026-10-26T10:00:00Z', label: 'Application received for scrutiny' }],
          notices: [{ id: 'n-1', reference: 'DN-AAAA-BBBB', sequence: 1, status: 'ISSUED', status_label: 'Issued — response awaited', application_id: 'app-1', application_reference: 'APP-AAAA-BBBB', issued_at: '2026-10-27T10:00:00Z', response_due_at: null, items: 1, items_answered: 0 }],
          not_an_approval: 'Completing scrutiny is not an approval.',
        },
      },
    })
    renderApp('/app/applications/app-1')
    expect(await screen.findByText('Deficiency notice issued — your response is awaited')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open notice DN-AAAA-BBBB' })).toHaveAttribute('href', '/app/notices/n-1')
    expect(screen.getByText(/no response deadline has been established/)).toBeInTheDocument()
    expect(screen.getByText('Application received for scrutiny')).toBeInTheDocument()
  })
})

describe('notifications', () => {
  it('shows the unread count in the top bar and lists notifications as stored in the app only', async () => {
    const item = { id: 'nt-1', event_type: 'NOTICE_ISSUED', title: 'Deficiency notice DN-AAAA-BBBB issued', body: 'A deficiency notice was issued.', link: '/app/notices/n-1', created_at: '2026-10-27T10:00:00Z', read: false, delivery_state: 'STORED_IN_APP', external_delivery: 'NOT_CONFIGURED', application_reference: 'APP-AAAA-BBBB' }
    const api = mockApi({
      'GET /api/auth/session': { body: INSTITUTE },
      'GET /api/notifications': { body: { items: [item], total: 1, unread: 1, page: 1, page_size: 20 } },
      'POST /api/notifications/nt-1/read': { body: { ...item, read: true } },
    })
    renderApp('/app/notifications')
    expect(await screen.findByRole('link', { name: 'Notifications, 1 unread' })).toBeInTheDocument()
    expect(await screen.findByText(/No email or text message is sent/)).toBeInTheDocument()
    expect(await screen.findByText(/stored in the app/)).toBeInTheDocument()
    expect(screen.getByText(/external delivery: not configured/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Mark as read' }))
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/notifications/nt-1/read')).toBe(true))
  })
})

describe('no stray decision controls anywhere in the scrutiny interface', () => {
  it('has no approve or reject control in the workspace for any scrutiny role', async () => {
    for (const [user, scope, actions] of [[REVIEWER, ['reviewer'], caseDetail().allowed_actions], [COORDINATOR, ['coordinator'], ['assign']], [ISSUER, ['issuer'], ['issue_notice', 'return_notice']]] as const) {
      mockApi({ 'GET /api/auth/session': { body: user }, ...UNREAD, 'GET /api/scrutiny/cases/case-1': { body: caseDetail({ scope: [...scope], allowed_actions: [...actions] }) }, ...viewerMocks })
      const { unmount } = renderApp('/app/scrutiny/case-1')
      await screen.findByText('Application summary').catch(() => screen.findByRole('tab', { name: /Evidence/ }))
      expect(screen.queryByRole('button', { name: /approve|reject|grant|decision|mark as approved/i })).not.toBeInTheDocument()
      unmount()
      vi.unstubAllGlobals()
    }
  })
})

const _unused = staffNoticeItem
void _unused
