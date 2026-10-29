import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { makeUser, mockApi, renderApp } from '../../test/helpers'
import type { ClockList, ClockView, ObligationDetail, ObligationList, ValidityItemView, ValidityList } from './types'

afterEach(() => {
  vi.unstubAllGlobals()
})

const INSTITUTE = makeUser(['dashboard:institute', 'compliance:read_own', 'compliance:submit', 'application:read_own', 'notifications:read'], { role: 'institute_user', role_label: 'Institution' })
const OFFICER = makeUser(['dashboard:compliance', 'compliance:queue', 'compliance:verify', 'clock:read', 'notifications:read'], { role: 'compliance_officer', role_label: 'Compliance officer' })
const BUREAU = makeUser(['dashboard:executive', 'compliance:queue', 'compliance:define', 'clock:read', 'clock:manage', 'notifications:read'], { role: 'regulation_bureau', role_label: 'Regulation Bureau' })
const UNREAD = { 'GET /api/notifications': { body: { items: [], total: 0, unread: 0, page: 1, page_size: 20 } } }
const UPLOAD_NOTE = 'Submitting evidence is not verification. An obligation is verified only when the assigned verifier records that outcome.'
const PENALTY = 'Recording an obligation, a submission or a verification outcome revokes nothing and imposes nothing. Any consequence is decided by the competent authority outside this system.'

const app = { id: 'app-1', reference: 'APP-AAAA-BBBB', institution_name: 'Test Institute', category_name: 'Category A', academic_year: '2026-27' }

const summary = (over = {}) => ({
  id: 'ob1', reference: 'OBL-AAAA-BBBB', title: 'Provide the fire safety certificate', status: 'AWAITING_EVIDENCE' as const, status_label: 'Awaiting your evidence', application: app, due_basis: 'SPECIFIED' as const,
  due_at: '2030-01-01T00:00:00Z', effective_due_at: '2030-01-01T00:00:00Z', timing: 'ON_TRACK' as const, verifier: null, defined_at: '2026-10-27T10:00:00Z', closed_at: null, submissions: 0, row_version: 1, ...over,
})
const list = (items: ReturnType<typeof summary>[]): ObligationList => ({ items, total: items.length, page: 1, page_size: 20, counts: { AWAITING_EVIDENCE: items.length, EVIDENCE_SUBMITTED: 0, CORRECTION_REQUESTED: 0, VERIFIED: 0, NOT_MET: 0, WITHDRAWN: 0 } })

const clock = (over: Partial<ClockView> = {}): ClockView => ({
  id: 'c1', kind: 'COMPLIANCE', deadline_type: 'SPECIFIED', label: 'Compliance: Provide the fire safety certificate', status: 'RUNNING', timing: 'ON_TRACK', policy_key: 'obligation-due', trigger_event: 'compliance_due_specified',
  trigger_at: '2026-10-27T10:00:00Z', due_at: '2030-01-01T00:00:00Z', effective_due_at: '2030-01-01T00:00:00Z', paused_seconds: 0, not_established_reason: null, application: app, completed_at: null, completion_reason: null,
  row_version: 1, subject_type: 'obligation', subject_id: 'ob1',
  policy: { state: 'SPECIFIED', message: 'A date stated by an authorised person.', source: null, demo: false, clause_ref: null, duration_value: null, duration_unit: null, calendar_convention: null, authority: 'LoA para 4(b)', note: null, verified_date: null }, ...over,
})
const clocks = (items: ClockView[]): ClockList => ({ items, total: items.length, page: 1, page_size: 25, counts: { overdue: 0, soon: 0, ontrack: items.length, paused: 0, not_established: 0 } })

const detail = (over: Partial<ObligationDetail> = {}): ObligationDetail => ({
  ...summary(), condition_text: 'Provide a valid fire safety certificate.', condition_ref: 'LoA para 4', required_evidence: 'A current certificate (PDF).', decision: { id: 'd1', source: 'EC_DECISION', version_number: 1, outcome: 'LOA', outcome_label: 'Letter of Approval (LoA)', decided_at: '2026-10-27T09:00:00Z' },
  due: { basis: 'SPECIFIED', due_at: '2030-01-01T00:00:00Z', authority: 'LoA para 4(b)', policy: {} }, clock: clock(), submissions_detail: [], events: [{ at: '2026-10-27T10:00:00Z', action: 'obligation_defined', from_status: null, to_status: 'AWAITING_EVIDENCE', actor: null, detail: null }],
  withdrawn_reason: null, notes: { upload: UPLOAD_NOTE, penalty: PENALTY }, can: { submit: false, review: false, withdraw: false, reassign: false }, latest_submission_id: null, ...over,
})
const submitted = (over = {}) => ({
  id: 'sub1', sequence: 1, note: 'Certificate attached.', submitted_at: '2026-10-28T10:00:00Z', submitted_by: 'Test User', review: null,
  documents: [{ id: 'doc1', label: 'Fire certificate', version_number: 1, filename: 'fire.pdf', size_bytes: 2048, sha256: 'ab'.repeat(32), media_type: 'application/pdf', scan_status: 'NOT_SCANNED', uploaded_at: '2026-10-28T10:00:00Z', download: '/api/post-approval/documents/doc1/download' }], ...over,
})

describe('obligations list', () => {
  it('tells an institution that submitting is not verification and offers no verification control', async () => {
    mockApi({ 'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD, 'GET /api/post-approval/obligations': { body: list([summary()]) } })
    renderApp('/app/post-approval')
    const link = await screen.findByRole('link', { name: 'Provide the fire safety certificate' })
    expect(link).toHaveAttribute('href', '/app/post-approval/obligations/ob1')
    expect(screen.getByText(/Submitting evidence is not verification/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /verify|record an obligation/i })).not.toBeInTheDocument()
  })

  it('shows an empty state that explains where obligations come from', async () => {
    mockApi({ 'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD, 'GET /api/post-approval/obligations': { body: list([]) } })
    renderApp('/app/post-approval')
    expect(await screen.findByText('You have no obligations')).toBeInTheDocument()
  })

  it('lets the Regulation Bureau open the form to record an obligation, and nobody else', async () => {
    mockApi({ 'GET /api/auth/session': { body: BUREAU }, ...UNREAD, 'GET /api/post-approval/obligations': { body: list([summary({ verifier: 'Officer One' })]) }, 'GET /api/post-approval/cases': { body: { items: [] } }, 'GET /api/post-approval/verifiers': { body: { items: [] } } })
    renderApp('/app/post-approval')
    await userEvent.click(await screen.findByRole('button', { name: 'Record an obligation' }))
    expect(await screen.findByText(/The system derives no obligation from a decision/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Record the obligation' })).toBeDisabled()
  })

  it('refuses a person without compliance permissions', async () => {
    mockApi({ 'GET /api/auth/session': { body: makeUser(['dashboard:scrutiny', 'notifications:read'], { role: 'scrutiny_officer', role_label: 'Scrutiny officer' }) }, ...UNREAD })
    renderApp('/app/post-approval')
    expect(await screen.findByText('You do not have access to this page')).toBeInTheDocument()
  })
})

describe('one obligation', () => {
  it('lets the institution submit evidence behind a confirmation that says it is not verification', async () => {
    const api = mockApi({
      'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD,
      'GET /api/post-approval/obligations/ob1': { body: detail({ can: { submit: true, review: false, withdraw: false, reassign: false } }) },
      'POST /api/post-approval/obligations/ob1/evidence': { status: 201, body: detail({ status: 'EVIDENCE_SUBMITTED', status_label: 'Evidence submitted — awaiting verification', submissions_detail: [submitted()], submissions: 1, row_version: 2 }) },
    })
    renderApp('/app/post-approval/obligations/ob1')
    expect(await screen.findByText('Submit your evidence')).toBeInTheDocument()
    const send = screen.getByRole('button', { name: 'Submit evidence' })
    expect(send).toBeDisabled()
    await userEvent.upload(screen.getByLabelText(/Files \(PDF/), new File(['%PDF-1.4'], 'fire.pdf', { type: 'application/pdf' }))
    expect(send).toBeEnabled()
    await userEvent.click(send)
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent(/not verification/)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Submit evidence' }))
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/post-approval/obligations/ob1/evidence')).toBe(true))
    expect(await screen.findByText('Evidence submitted — awaiting verification')).toBeInTheDocument()
    expect(screen.queryByText('Submit your evidence')).not.toBeInTheDocument()
  })

  it('never shows an institution the verifier’s internal note or any review control', async () => {
    mockApi({
      'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD,
      'GET /api/post-approval/obligations/ob1': {
        body: detail({ status: 'CORRECTION_REQUESTED', status_label: 'Correction requested', submissions_detail: [submitted({ review: { outcome: 'CORRECTION_REQUESTED', reason: 'Please send the current certificate.', reviewed_at: '2026-10-29T10:00:00Z', reviewed_by: 'The assigned verifier' } })] }),
      },
    })
    renderApp('/app/post-approval/obligations/ob1')
    expect(await screen.findByText('Please send the current certificate.')).toBeInTheDocument()
    expect(screen.queryByText(/Internal note/)).not.toBeInTheDocument()
    expect(screen.queryByText('Record your verification outcome')).not.toBeInTheDocument()
  })

  it('gives the assigned verifier a form with nothing preselected, and records exactly the chosen outcome', async () => {
    const api = mockApi({
      'GET /api/auth/session': { body: OFFICER }, ...UNREAD,
      'GET /api/post-approval/obligations/ob1': { body: detail({ status: 'EVIDENCE_SUBMITTED', status_label: 'Evidence submitted — awaiting verification', row_version: 2, submissions_detail: [submitted()], latest_submission_id: 'sub1', verifier: 'Officer One', can: { submit: false, review: true, withdraw: false, reassign: false } }) },
      'POST /api/post-approval/obligations/ob1/review': { status: 201, body: detail({ status: 'VERIFIED', status_label: 'Verified', row_version: 3, submissions_detail: [submitted({ review: { outcome: 'VERIFIED', reason: 'Checked against the registry.', reviewed_at: '2026-10-29T10:00:00Z', reviewed_by: 'Officer One', internal_note: 'Registry stamp visible' } })] }) },
    })
    renderApp('/app/post-approval/obligations/ob1')
    expect(await screen.findByText('Record your verification outcome')).toBeInTheDocument()
    expect(screen.getByLabelText('Outcome')).toHaveValue('')
    const go = screen.getByRole('button', { name: 'Choose an outcome' })
    expect(go).toBeDisabled()
    await userEvent.selectOptions(screen.getByLabelText('Outcome'), 'VERIFIED')
    await userEvent.type(screen.getByLabelText(/Reason \(shown/), 'short')
    expect(screen.getByRole('button', { name: 'Record as verified' })).toBeDisabled() // a reason needs at least ten characters
    await userEvent.type(screen.getByLabelText(/Reason \(shown/), ' and complete reason')
    await userEvent.click(screen.getByRole('button', { name: 'Record as verified' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/cannot be edited afterwards/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record as verified' }))
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/post-approval/obligations/ob1/review')).toBe(true))
    const body = api.calls.find((c) => c.key === 'POST /api/post-approval/obligations/ob1/review')!.body as { outcome: string; submission_id: string; row_version: number; idempotency_key: string }
    expect(body).toMatchObject({ outcome: 'VERIFIED', submission_id: 'sub1', row_version: 2 })
    expect(body.idempotency_key.length).toBeGreaterThanOrEqual(8)
    expect(await screen.findByText('Internal note: Registry stamp visible')).toBeInTheDocument() // the verifier sees their own internal note
  })

  it('states that a not-met outcome is a record and that no penalty follows from this system', async () => {
    mockApi({
      'GET /api/auth/session': { body: OFFICER }, ...UNREAD,
      'GET /api/post-approval/obligations/ob1': { body: detail({ status: 'EVIDENCE_SUBMITTED', submissions_detail: [submitted()], latest_submission_id: 'sub1', can: { submit: false, review: true, withdraw: false, reassign: false } }) },
    })
    renderApp('/app/post-approval/obligations/ob1')
    expect((await screen.findAllByText(new RegExp(PENALTY.slice(0, 60)))).length).toBeGreaterThan(0)
  })
})

describe('deadlines', () => {
  it('shows a statutory deadline with its source, and says plainly when no date could be established', async () => {
    const statutory = clock({ id: 'c2', kind: 'APPEAL', deadline_type: 'STATUTORY', label: 'Appeal window', policy: { ...clock().policy, source: { title: 'APH 2024-27', verification_status: 'VERIFIED_OFFICIAL', clause_ref: 'cl. 2.24' }, clause_ref: 'Ch. II cl. 2.24', duration_value: 7, duration_unit: 'days', authority: null } })
    const none = clock({ id: 'c3', kind: 'INSTITUTION_RESPONSE', deadline_type: 'INTERNAL_SLA', label: 'Response to deficiency notice DN-1', status: 'NOT_ESTABLISHED', timing: 'NOT_ESTABLISHED', due_at: null, effective_due_at: null, not_established_reason: 'The policy states no duration, so no deadline has been set. Nothing is assumed.' })
    mockApi({ 'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD, 'GET /api/clocks': { body: clocks([statutory, none]) } })
    renderApp('/app/post-approval?tab=deadlines')
    expect((await screen.findAllByText('Appeal window')).length).toBeGreaterThan(0)
    expect(screen.getByText('Statutory / regulatory')).toBeInTheDocument()
    expect(screen.getByText('APH 2024-27')).toBeInTheDocument()
    expect(screen.getByText('The policy states no duration, so no deadline has been set. Nothing is assumed.')).toBeInTheDocument()
    expect(screen.getByText('No date established')).toBeInTheDocument()
  })

  it('filters through the server rather than in the browser', async () => {
    const api = mockApi({ 'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD, 'GET /api/clocks': { body: { ...clocks([clock()]), counts: { overdue: 2, soon: 1, ontrack: 0, paused: 0, not_established: 0 } } } })
    renderApp('/app/post-approval?tab=deadlines')
    await userEvent.click(await screen.findByRole('button', { name: /Past its date 2/ }))
    await waitFor(() => expect(api.calls.some((c) => c.url.includes('due=overdue'))).toBe(true))
  })

  it('does not offer to pause a deadline whose policy forbids it', async () => {
    mockApi({
      'GET /api/auth/session': { body: BUREAU }, ...UNREAD, 'GET /api/clocks/health': { body: { work: {}, due_and_waiting: 0, oldest_waiting_lag_seconds: 0, live_clocks: 1, failed: [], note: '' } },
      'GET /api/clocks/c1': { body: clock({ deadline_type: 'STATUTORY', pause: { allowed: false, reasons: [] }, alerts: { pre_due_hours: [], at_due: false, overdue_hours: [] }, escalate_to: [], segments: [], events: [], alert_work: [] }) },
    })
    renderApp('/app/deadlines/c1')
    expect(await screen.findByText(/does not permit pausing/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Pause this deadline/ })).not.toBeInTheDocument()
    expect(screen.getByText(/None configured — none is raised/)).toBeInTheDocument()
  })

  it('offers a pause only for the reasons the policy lists', async () => {
    const api = mockApi({
      'GET /api/auth/session': { body: BUREAU }, ...UNREAD, 'GET /api/clocks/health': { body: { work: {}, due_and_waiting: 0, oldest_waiting_lag_seconds: 0, live_clocks: 1, failed: [], note: '' } },
      'GET /api/clocks/c1': { body: clock({ deadline_type: 'INTERNAL_SLA', pause: { allowed: true, reasons: ['awaiting_clarification'] }, alerts: { pre_due_hours: [24], at_due: false, overdue_hours: [] }, escalate_to: [], segments: [], events: [], alert_work: [] }) },
      'POST /api/clocks/c1/pause': { body: clock({ status: 'PAUSED', timing: 'PAUSED', effective_due_at: null, row_version: 2, pause: { allowed: true, reasons: ['awaiting_clarification'] }, segments: [], events: [], alert_work: [] }) },
    })
    renderApp('/app/deadlines/c1')
    const pause = await screen.findByRole('button', { name: 'Pause this deadline' })
    expect(pause).toBeDisabled()
    await userEvent.selectOptions(screen.getByLabelText('Reason'), 'awaiting_clarification')
    await userEvent.type(screen.getByLabelText(/Why it is being paused/), 'Waiting for the institution')
    await userEvent.click(pause)
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Pause' }))
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/clocks/c1/pause')).toBe(true))
    expect(api.calls.find((c) => c.key === 'POST /api/clocks/c1/pause')!.body).toMatchObject({ reason_code: 'awaiting_clarification', row_version: 1 })
  })

  it('keeps institutions out of the officials’ deadline pages', async () => {
    mockApi({ 'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD })
    renderApp('/app/deadlines')
    expect(await screen.findByText('You do not have access to this page')).toBeInTheDocument()
  })
})

describe('document validity', () => {
  const version = (over = {}) => ({
    id: 'v1', version_number: 1, source: 'EXTRACTED' as const, status: 'UNVERIFIED' as const, value_date: '2029-03-31', raw_value: '31/03/2029', verified_date: null, verified_at: null, needs_review_reason: null, review_reason: null,
    created_at: '2026-10-27T10:00:00Z', provenance: { document_id: 'doc1', document_version: 1, page: 2, method: 'PDF_TEXT', confidence: null, evidence: 'Valid until: 31/03/2029' }, file: null, conflict: null, clock: null, internal_note: null, verified_by: null, source_download: '/api/post-approval/validity/versions/v1/download', ...over,
  })
  const item = (over: Partial<ValidityItemView> = {}): ValidityItemView => ({
    id: 'it1', label: 'Main document — Valid until', status: 'ACTIVE', application: app, versions: [version()], resolved_at: null, resolution_reason: null, current_verified_date: null, expiry_timing: null, effective_due_at: null, awaiting_review: 1,
    can: { renew: false, review: true }, note: 'Only a date a person has confirmed against the document produces an expiry alert. A renewal does not end the earlier date’s alerts until it is verified.', ...over,
  })
  const vlist = (items: ValidityItemView[]): ValidityList => ({ items, total: items.length, page: 1, page_size: 20 })

  it('shows an extracted date as a candidate with its source, and no expiry alert', async () => {
    mockApi({ 'GET /api/auth/session': { body: OFFICER }, ...UNREAD, 'GET /api/post-approval/validity': { body: vlist([item()]) } })
    renderApp('/app/post-approval?tab=validity')
    expect(await screen.findByText('Read from the submitted document (candidate)')).toBeInTheDocument()
    expect(screen.getByText(/Submitted document version 1, page 2/)).toBeInTheDocument()
    expect(screen.getByText('none confirmed yet')).toBeInTheDocument()
    expect(screen.getByText('not yet confirmed')).toBeInTheDocument()
    expect(screen.getByText(/Only a date a person has confirmed/)).toBeInTheDocument()
  })

  it('records the date a person confirms, keeping the extracted date beside it', async () => {
    const confirmed = item({ current_verified_date: '2029-03-30', expiry_timing: 'ON_TRACK', awaiting_review: 0, versions: [version({ status: 'VERIFIED', verified_date: '2029-03-30', verified_by: 'Officer One' })] })
    const api = mockApi({ 'GET /api/auth/session': { body: OFFICER }, ...UNREAD, 'GET /api/post-approval/validity': { body: vlist([item()]) }, 'POST /api/post-approval/validity/versions/v1/verify': { status: 201, body: confirmed } })
    renderApp('/app/post-approval?tab=validity')
    await userEvent.click(await screen.findByRole('button', { name: 'Confirm a date…' }))
    const confirm = screen.getByRole('button', { name: 'Confirm this date' })
    expect(confirm).toBeDisabled()
    await userEvent.clear(screen.getByLabelText(/Expiry date as shown/))
    await userEvent.type(screen.getByLabelText(/Expiry date as shown/), '2029-03-30')
    await userEvent.type(screen.getByLabelText('What you checked'), 'The stamp on page 2 says 30 March.')
    await userEvent.click(confirm)
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Only a date a person has confirmed produces an expiry alert/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm this date' }))
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/post-approval/validity/versions/v1/verify')).toBe(true))
    expect(api.calls.find((c) => c.key === 'POST /api/post-approval/validity/versions/v1/verify')!.body).toMatchObject({ verified_date: '2029-03-30' })
  })

  it('flags a conflicting date for a person instead of choosing one', async () => {
    mockApi({
      'GET /api/auth/session': { body: OFFICER }, ...UNREAD,
      'GET /api/post-approval/validity': { body: vlist([item({ versions: [version(), version({ id: 'v2', version_number: 2, value_date: '2030-03-31', status: 'NEEDS_REVIEW', needs_review_reason: 'This date differs from another recorded date for the same document. A person must decide which is right.', conflict: { with_versions: [1], dates: ['2029-03-31'] } })], awaiting_review: 2 })]) },
    })
    renderApp('/app/post-approval?tab=validity')
    expect(await screen.findByText('Conflicting dates')).toBeInTheDocument()
    expect(screen.getAllByText(/A person must decide which is right/).length).toBeGreaterThan(0)
  })

  it('lets an institution send a renewal and says the earlier date stays in force', async () => {
    const api = mockApi({
      'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD,
      'GET /api/post-approval/validity': { body: vlist([item({ can: { renew: true, review: false }, current_verified_date: '2029-03-31', expiry_timing: 'ON_TRACK', awaiting_review: 0, versions: [version({ status: 'VERIFIED', verified_date: '2029-03-31', source_download: null, internal_note: undefined })] })]) },
      'POST /api/post-approval/validity/items/it1/renewals': { status: 201, body: item({ can: { renew: false, review: false } }) },
    })
    renderApp('/app/post-approval?tab=validity')
    expect(await screen.findByText(/Your earlier date and its reminders stay in force/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Confirm a date…' })).not.toBeInTheDocument()
    const send = screen.getByRole('button', { name: 'Send the renewal' })
    expect(send).toBeDisabled()
    await userEvent.upload(screen.getByLabelText(/Renewed document/), new File(['%PDF-1.4'], 'renewed.pdf', { type: 'application/pdf' }))
    await userEvent.type(screen.getByLabelText('New expiry date shown on it'), '2032-03-31')
    await userEvent.click(send)
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Send the renewal' }))
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/post-approval/validity/items/it1/renewals')).toBe(true))
  })

  it('has a useful empty state', async () => {
    mockApi({ 'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD, 'GET /api/post-approval/validity': { body: vlist([]) } })
    renderApp('/app/post-approval?tab=validity')
    expect(await screen.findByText('No documents are tracked for validity')).toBeInTheDocument()
  })
})
