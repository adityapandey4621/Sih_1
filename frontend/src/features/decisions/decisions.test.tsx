import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { detail } from '../../test/fixtures'
import { makeUser, mockApi, renderApp } from '../../test/helpers'
import type { DecisionCaseDetail, DecisionPolicyView, DecisionQueue, InstitutionDecision } from './types'

afterEach(() => {
  vi.unstubAllGlobals()
})

const EC = makeUser(['dashboard:executive', 'decision:queue', 'decision:record', 'evidence:read', 'notifications:read'], { role: 'executive_committee', role_label: 'Executive Committee' })
const BUREAU = makeUser(['dashboard:executive', 'decision:queue', 'decision:observe', 'decision:issue', 'evidence:read', 'notifications:read'], { role: 'regulation_bureau', role_label: 'Regulation Bureau' })
const COUNCIL = makeUser(['dashboard:executive', 'decision:queue', 'council:record', 'evidence:read', 'notifications:read'], { role: 'council_member', role_label: 'Council member' })
const INSTITUTE = makeUser(['dashboard:institute', 'application:read_own', 'decision:read_own', 'appeal:submit', 'committee:status_own', 'notice:read_own', 'notifications:read'], { role: 'institute_user', role_label: 'Institution' })
const UNREAD = { 'GET /api/notifications': { body: { items: [], total: 0, unread: 0, page: 1, page_size: 20 } } }
const ERR = (status: number, code: string, message: string) => ({ status, body: { detail: message, code, issues: [] } })

const app = { id: 'app-1', reference: 'APP-AAAA-BBBB', institution_name: 'Test Institute', category_name: 'Category A', academic_year: '2026-27', status: 'SUBMITTED' }
const NOT_A_DECISION = 'This system records what the authorised bodies decide. Nothing on this page was decided by the software, and no report or recommendation is a decision.'

const policy: DecisionPolicyView = {
  id: 'p1', key: 'demo-decision-policy', version: 1, classification: 'DEMO', title: 'DEMO — decision, ratification and appeal', banner: 'DEMO ONLY — fictional decision configuration.',
  sources: [{ title: 'DEMO ONLY', clause_ref: 'DEMO ONLY', verification_status: 'DEMO_ONLY' }],
  decision: { outcomes: [{ key: 'LOA', label: 'Letter of Approval (LoA)' }, { key: 'LOD', label: 'Letter of Deficiency (LoD)' }, { key: 'LOR', label: 'Letter of Rejection (LoR)', requires_speaking_order: true }], bureau_observation_required: true },
  ratification: { required: true, outcomes: [{ key: 'RATIFIED', label: 'Ratified' }, { key: 'NOT_RATIFIED', label: 'Not ratified' }], clause_ref: 'APH 2024-27 Ch. II cl. 2.25(a)' },
  appeal: { permitted: true, appealable_outcomes: ['LOD', 'LOR'], council_outcomes: [{ key: 'LOA', label: 'Letter of Approval (LoA)' }, { key: 'LOR', label: 'Letter of Rejection (LoR)', requires_speaking_order: true }], clause_ref: 'APH 2024-27 Ch. II cl. 2.24' },
}

const decision = (over = {}) => ({
  id: 'd1', version_number: 1, kind: 'ORIGINAL' as const, outcome: 'LOR' as const, outcome_label: 'Letter of Rejection (LoR)', reasons: 'The Committee considered the record.', speaking_order: 'The reasons are set out here in full.', meeting_reference: 'EC 1',
  decided_by: 'Test EC', decided_at: '2026-10-27T12:00:00Z', correction_reason: null, supersedes_id: null, content_hash: 'cd'.repeat(32), ...over,
})

const caseOf = (over: Partial<DecisionCaseDetail> = {}): DecisionCaseDetail => ({
  id: 'dc1', application: app, state: 'READY_FOR_DECISION', state_label: 'Ready for the Executive Committee’s decision', row_version: 3, opened_at: '2026-10-27T10:00:00Z', first_issued_at: null, closed_at: null, closure: null, policy, blockers: [],
  route: { stages: ['scrutiny', 'evc'], basis: {} },
  supporting: { scrutiny: { case_id: 's1', state: 'SCRUTINY_COMPLETED', completion: { outcome: 'COMPLETED_NO_OPEN_FINDINGS' }, findings: [] }, evc: { case_id: 'e1', state: 'COMPLETED', record: { version: 1, content_hash: 'ab'.repeat(32), option: null } }, shc: null, bureau: { observation_ids: ['o1'], latest: { id: 'o1', sequence: 1 } }, documents: [] },
  considerable: ['scrutiny', 'evc', 'bureau'], observations: [{ id: 'o1', sequence: 1, text: 'The Bureau has examined the record.', by: 'Test Bureau', at: '2026-10-27T11:00:00Z' }],
  decisions: [], current_decision: null, issuances: [], live_issuance: null, issuance_sync: null, ratifications: [], live_ratification: null,
  appeal_eligibility: { state: 'NOT_ISSUED', message: 'Nothing has been issued.', deadline_at: null, trigger_at: null, outcome: null, clause_ref: null }, appeal: null, closure_blockers: [],
  events: [{ at: '2026-10-27T10:00:00Z', actor: 'System', action: 'case_opened', from_state: null, to_state: 'AWAITING_OBSERVATIONS', reason: null }],
  allowed_actions: [], not_a_decision: NOT_A_DECISION, post_decision: false, ...over,
})

const queue = (items: DecisionQueue['items'], counts: Record<string, number> = { READY_FOR_DECISION: items.length }): DecisionQueue => ({ total: items.length, page: 1, page_size: 20, counts, items, can: { observe: false, record: true, council: false } })
const qrow = (over = {}) => ({ id: 'dc1', application: app, state: 'READY_FOR_DECISION' as const, state_label: 'Ready for the Executive Committee’s decision', row_version: 3, opened_at: '2026-10-27T10:00:00Z', outcome: null, outcome_label: null, blocked: false, appeal_status: null, appeal_label: null, demo: true, ...over })

describe('decision queue', () => {
  it('lists only what the server returns and states that nothing is decided by the software', async () => {
    mockApi({ 'GET /api/auth/session': { body: EC }, ...UNREAD, 'GET /api/decisions/cases': { body: queue([qrow(), qrow({ id: 'dc2', blocked: true, outcome_label: 'Letter of Rejection (LoR)', appeal_label: 'Under review by the Standing Appellate Committee' })]) } })
    renderApp('/app/executive')
    const links = await screen.findAllByRole('link', { name: 'APP-AAAA-BBBB' })
    expect(links[0]).toHaveAttribute('href', '/app/decisions/dc1')
    expect(screen.getAllByText(/DEMO ONLY configuration/).length).toBeGreaterThan(0)
    expect(screen.getByText(/Blocked: see the case/)).toBeInTheDocument()
    expect(screen.getByText(/Appeal: Under review by the Standing Appellate Committee/)).toBeInTheDocument()
    expect(screen.getByText(/Nothing on this page approves or rejects an application/)).toBeInTheDocument()
    expect(screen.getByText(/Holding an administrator role gives no decision power/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /approve|reject/i })).not.toBeInTheDocument()
  })

  it('explains that an incomplete route keeps a case out of the queue', async () => {
    mockApi({ 'GET /api/auth/session': { body: EC }, ...UNREAD, 'GET /api/decisions/cases': { body: queue([], {}) } })
    renderApp('/app/executive')
    expect(await screen.findByText(/only when the scrutiny stage and every other mandatory stage/)).toBeInTheDocument()
  })

  it('filters by state through the server', async () => {
    const api = mockApi({ 'GET /api/auth/session': { body: EC }, ...UNREAD, 'GET /api/decisions/cases': { body: queue([qrow()], { READY_FOR_DECISION: 2, ISSUED: 1 }) } })
    renderApp('/app/executive')
    await userEvent.click(await screen.findByRole('button', { name: /Issued 1/ }))
    await waitFor(() => expect(api.calls.some((c) => c.url.includes('state=ISSUED'))).toBe(true))
  })

  it('refuses a person without the permission, including an institution', async () => {
    mockApi({ 'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD })
    renderApp('/app/executive')
    expect(await screen.findByText('You do not have access to this page')).toBeInTheDocument()
  })
})

describe('Executive Committee decision', () => {
  it('records only an outcome the committee chose, with its confirmation of what it considered, and a stable key', async () => {
    const api = mockApi({
      'GET /api/auth/session': { body: EC }, ...UNREAD,
      'GET /api/decisions/cases/dc1': { body: caseOf({ allowed_actions: ['record_decision'] }) },
      'POST /api/decisions/cases/dc1/decision': { status: 201, body: caseOf({ state: 'DECIDED', state_label: 'Decision recorded — not yet issued', decisions: [decision()], current_decision: decision(), allowed_actions: ['correct_decision'] }) },
    })
    renderApp('/app/decisions/dc1')
    expect(await screen.findByText(NOT_A_DECISION, { selector: 'p.note' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: 'Decision' }))
    expect(screen.getByText(/never decides, suggests or defaults an outcome/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Record the decision…' }))
    const form = await screen.findByRole('form', { name: 'Record the decision' })
    const submit = within(form).getByRole('button', { name: 'Record the decision…' })
    expect(submit).toBeDisabled()
    expect(within(form).getByLabelText('Outcome')).toHaveValue('') // nothing is preselected
    await userEvent.selectOptions(within(form).getByLabelText('Outcome'), 'LOR')
    await userEvent.type(within(form).getByLabelText('Reasons'), 'The Committee considered the record.')
    expect(submit).toBeDisabled() // the speaking order and the considered records are still missing
    await userEvent.type(within(form).getByLabelText(/Speaking order/), 'The reasons are set out here in full.')
    for (const name of ['the scrutiny record', 'the expert visit (EVC) report', 'the Regulation Bureau’s observations']) await userEvent.click(within(form).getByRole('checkbox', { name }))
    expect(submit).toBeEnabled()
    await userEvent.click(submit)
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/cannot be edited or deleted/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Record' }))
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/decisions/cases/dc1/decision')).toBe(true))
    const body = api.calls.find((c) => c.key === 'POST /api/decisions/cases/dc1/decision')!.body as { outcome: string; considered: string[]; idempotency_key: string; row_version: number }
    expect(body).toMatchObject({ outcome: 'LOR', considered: ['scrutiny', 'evc', 'bureau'], row_version: 3 })
    expect(body.idempotency_key.length).toBeGreaterThanOrEqual(8)
    expect(await screen.findByText('Current')).toBeInTheDocument()
  })

  it('offers no decision form to the Bureau or the Council and shows why a blocked case waits', async () => {
    mockApi({ 'GET /api/auth/session': { body: BUREAU }, ...UNREAD, 'GET /api/decisions/cases/dc1': { body: caseOf({ state: 'AWAITING_OBSERVATIONS', state_label: 'Awaiting the Regulation Bureau’s observations', blockers: [{ code: 'bureau_observation_required', message: 'The configuration requires the Regulation Bureau’s observations first.' }], allowed_actions: ['observe'] }) } })
    renderApp('/app/decisions/dc1')
    expect(await screen.findByText('A decision cannot be recorded yet')).toBeInTheDocument()
    expect(screen.getByText(/Nothing is assumed to keep the case moving/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: 'Decision' }))
    expect(screen.queryByRole('button', { name: /Record the decision/ })).not.toBeInTheDocument()
  })

  it('shows the server’s refusal in full and keeps the form', async () => {
    mockApi({
      'GET /api/auth/session': { body: EC }, ...UNREAD, 'GET /api/decisions/cases/dc1': { body: caseOf({ allowed_actions: ['record_decision'] }) },
      'POST /api/decisions/cases/dc1/decision': ERR(409, 'decision_blocked', 'A decision cannot be recorded: the route is ambiguous.'),
    })
    renderApp('/app/decisions/dc1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Decision' }))
    await userEvent.click(screen.getByRole('button', { name: 'Record the decision…' }))
    const form = await screen.findByRole('form', { name: 'Record the decision' })
    await userEvent.selectOptions(within(form).getByLabelText('Outcome'), 'LOA')
    await userEvent.type(within(form).getByLabelText('Reasons'), 'The Committee considered the record.')
    for (const name of ['the scrutiny record', 'the expert visit (EVC) report', 'the Regulation Bureau’s observations']) await userEvent.click(within(form).getByRole('checkbox', { name }))
    await userEvent.click(within(form).getByRole('button', { name: 'Record the decision…' }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Record' }))
    expect(await screen.findByText('A decision cannot be recorded: the route is ambiguous.')).toBeInTheDocument()
  })

  it('keeps a superseded decision visible beside its correction', async () => {
    const original = decision()
    const fix = decision({ id: 'd2', version_number: 2, kind: 'CORRECTION', outcome: 'LOD', outcome_label: 'Letter of Deficiency (LoD)', correction_reason: 'A clerical error.', supersedes_id: 'd1' })
    mockApi({ 'GET /api/auth/session': { body: EC }, ...UNREAD, 'GET /api/decisions/cases/dc1': { body: caseOf({ state: 'DECIDED', decisions: [original, fix], current_decision: fix, allowed_actions: ['correct_decision'] }) } })
    renderApp('/app/decisions/dc1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Decision' }))
    expect(screen.getByText('Superseded')).toBeInTheDocument()
    expect(screen.getByText('Current')).toBeInTheDocument()
    expect(screen.getByText(/Correction reason: A clerical error/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Record a correction…' })).toBeInTheDocument()
  })
})

describe('issuing, sync and the Council', () => {
  it('says the AICTE portal was NOT updated when nothing is connected, and shows SYNC_PENDING honestly', async () => {
    const live = { id: 'i1', decision_id: 'd1', status: 'ISSUED' as const, document_reference: 'LOR/2026/0001', issued_at: '2026-10-27T13:00:00Z', superseded_at: null, file: null }
    const sync = (status: 'NOT_CONFIGURED' | 'SYNC_PENDING', message: string) => ({ status, message, attempts: 1, last_error: status === 'SYNC_PENDING' ? 'timeout' : null, last_attempt_at: null, confirmed_at: null, external_ref: null })
    mockApi({
      'GET /api/auth/session': { body: BUREAU }, ...UNREAD,
      'GET /api/decisions/cases/dc1': { body: caseOf({ state: 'ISSUED', decisions: [decision()], current_decision: decision(), issuances: [live], live_issuance: live, issuance_sync: sync('SYNC_PENDING', 'AICTE’s portal has not confirmed receipt. The system will retry; it does NOT show the portal as updated.') }) },
    })
    renderApp('/app/decisions/dc1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Issued outcome' }))
    expect(await screen.findByText(/AICTE portal — the issued outcome: Sync pending/i)).toBeInTheDocument()
    expect(screen.getByText(/does NOT show the portal as updated/)).toBeInTheDocument()
    expect(screen.getByText(/Last problem: timeout/)).toBeInTheDocument()
    expect(screen.queryByText(/portal confirmed receipt/)).not.toBeInTheDocument()
    expect(screen.getByText(/does not draft letter text, signatures or seals/)).toBeInTheDocument()
  })

  it('issues by reference without generating any letter text', async () => {
    const live = { id: 'i1', decision_id: 'd1', status: 'ISSUED' as const, document_reference: 'LOR/2026/0001', issued_at: '2026-10-27T13:00:00Z', superseded_at: null, file: null }
    const api = mockApi({
      'GET /api/auth/session': { body: BUREAU }, ...UNREAD,
      'GET /api/decisions/cases/dc1': { body: caseOf({ state: 'DECIDED', state_label: 'Decision recorded — not yet issued', decisions: [decision()], current_decision: decision(), allowed_actions: ['issue_outcome'] }) },
      'POST /api/decisions/cases/dc1/issue': { status: 201, body: caseOf({ state: 'ISSUED', decisions: [decision()], current_decision: decision(), issuances: [live], live_issuance: live }) },
    })
    renderApp('/app/decisions/dc1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Issued outcome' }))
    const issue = screen.getByRole('button', { name: 'Issue the outcome' })
    expect(issue).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/Official document reference/), 'LOR/2026/0001')
    await userEvent.click(issue)
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/decisions/cases/dc1/issue')).toBe(true))
    expect(await screen.findByText(/LOR\/2026\/0001/)).toBeInTheDocument()
  })

  it('lets only the Council record its step, and records it with the configured outcomes', async () => {
    const api = mockApi({
      'GET /api/auth/session': { body: COUNCIL }, ...UNREAD,
      'GET /api/decisions/cases/dc1': { body: caseOf({ state: 'ISSUED', decisions: [decision()], current_decision: decision(), allowed_actions: ['record_ratification'] }) },
      'POST /api/decisions/cases/dc1/ratification': { status: 201, body: caseOf({ state: 'ISSUED', decisions: [decision()], current_decision: decision() }) },
    })
    renderApp('/app/decisions/dc1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Council' }))
    expect(screen.getByText(/kept separate from the decision itself/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Record the Council’s step…' }))
    const form = await screen.findByRole('form', { name: 'Record the Council’s step' })
    expect(within(form).getByRole('option', { name: 'Not ratified' })).toBeInTheDocument()
    await userEvent.selectOptions(within(form).getByLabelText('Outcome'), 'RATIFIED')
    await userEvent.type(within(form).getByLabelText('Reasons'), 'Considered.')
    await userEvent.click(within(form).getByRole('button', { name: 'Record…' }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Record' }))
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/decisions/cases/dc1/ratification')).toBe(true))
    expect((api.calls.find((c) => c.key.endsWith('/ratification'))!.body as { outcome: string }).outcome).toBe('RATIFIED')
  })

  it('blocks the Council’s appeal decision until the SAC has finished, and says so', async () => {
    const appeal = {
      id: 'ap1', status: 'UNDER_SAC_REVIEW' as const, status_label: 'Under review by the Standing Appellate Committee', submitted_at: '2026-10-28T09:00:00Z', grounds: 'We are aggrieved.',
      eligibility: { state: 'ELIGIBLE', message: 'The institution may appeal once.', deadline_at: '2026-11-03T13:00:00Z', trigger_at: '2026-10-27T13:00:00Z', outcome: 'LOR', clause_ref: 'APH 2024-27 Ch. II cl. 2.24(a)' }, sac_case_id: 'sc1', sac_state: 'UNDER_REVIEW',
      sac_recommendation: null, documents: [], council_decisions: [], live_council_decision: null, sync: null,
    }
    mockApi({ 'GET /api/auth/session': { body: COUNCIL }, ...UNREAD, 'GET /api/decisions/cases/dc1': { body: caseOf({ state: 'ISSUED', appeal, decisions: [decision()], current_decision: decision() }) } })
    renderApp('/app/decisions/dc1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Council' }))
    expect(screen.getByText(/The SAC has not finished its review, so the Council cannot yet record a decision/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Record the Council’s decision/ })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: 'Appeal' }))
    expect(screen.getByText(/It is a recommendation|The SAC has not submitted its recommendation/)).toBeInTheDocument()
  })
})

describe('the institution', () => {
  const issued: NonNullable<InstitutionDecision['decision']> = {
    outcome: 'LOR', outcome_label: 'Letter of Rejection (LoR)', reasons: 'The Committee considered the record.', speaking_order: 'The reasons in full.', issued_at: '2026-10-27T13:00:00Z', first_issued_at: '2026-10-27T13:00:00Z', document_reference: 'LOR/2026/0001', file: null,
    version_number: 1, corrected: false, correction_reason: null, earlier_issues: [], ratification: { required: true, recorded: false, outcome: null, recorded_at: null, reasons: null, clause_ref: 'APH 2024-27 Ch. II cl. 2.25(a)' },
    sync: { status: 'NOT_CONFIGURED', message: 'No authorised integration with AICTE’s portal is configured. Nothing was sent; AICTE’s portal has NOT been updated by this system.', attempts: 0, last_error: null, last_attempt_at: null, confirmed_at: null, external_ref: null },
  }
  const status = (over: Partial<InstitutionDecision> = {}): InstitutionDecision => ({
    official_status: { state: 'EXECUTIVE_COMMITTEE_DECISION_ISSUED', label: 'Executive Committee decision issued: Letter of Rejection (LoR)', outcome: 'LOR' }, decision: issued, closed: false,
    appeal: { eligibility: { state: 'ELIGIBLE', message: 'The institution may appeal once, against this decision, until the deadline.', deadline_at: '2026-11-03T13:00:00Z', trigger_at: '2026-10-27T13:00:00Z', outcome: 'LOR', clause_ref: 'APH 2024-27 Ch. II cl. 2.24(a)' },
      window: { note: 'The handbook does not state how the days are counted or the cut-off time; this is a calendar-day count and is not verified.' }, submitted: null }, not_a_decision: NOT_A_DECISION, ...over,
  })
  const base = {
    'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD,
    'GET /api/applications/app-1': { body: detail({ status: 'SUBMITTED', editable: false }) },
    'GET /api/applications/app-1/scrutiny': { body: { state: 'SCRUTINY_COMPLETED', state_label: 'Scrutiny completed', explanation: 'Done.', notices: [], events: [], not_an_approval: 'This is not an approval.' } },
    'GET /api/applications/app-1/evidence': { body: { application_id: 'app-1', reference: 'APP-AAAA-BBBB', status: 'SUBMITTED', academic_year: '2026-27', category_name: 'A', institution_name: null, viewer: 'owner', items: [], cross_document_findings: [], disclaimer: '', ocr_available: true, malware_scanner: 'none' } },
    'GET /api/applications/app-1/committee': { body: { evc: null, shc: null, ready_for_decision_stage: false, not_a_decision: 'x' } },
  }

  it('sees the issued outcome, its reasons, the Council’s pending step and an honest sync status', async () => {
    mockApi({ ...base, 'GET /api/applications/app-1/decision': { body: status() } })
    renderApp('/app/applications/app-1')
    expect(await screen.findByText('Executive Committee decision issued: Letter of Rejection (LoR)')).toBeInTheDocument()
    expect(screen.getByText('The Committee considered the record.')).toBeInTheDocument()
    expect(screen.getByText('The reasons in full.')).toBeInTheDocument()
    expect(screen.getByText(/Council step: not yet recorded/)).toBeInTheDocument()
    expect(screen.getByText(/AICTE’s portal has NOT been updated by this system/)).toBeInTheDocument()
    expect(screen.queryByText(/portal confirmed receipt/)).not.toBeInTheDocument()
  })

  it('shows the appeal period from the rules, with the unverified counting note, and submits once with a stable key', async () => {
    const submitted = status({
      appeal: { eligibility: { state: 'ALREADY_APPEALED', message: 'An appeal was already submitted.', deadline_at: '2026-11-03T13:00:00Z', trigger_at: null, outcome: 'LOR', clause_ref: 'APH 2024-27 Ch. II cl. 2.24(a)' },
        submitted: { id: 'ap1', status: 'UNDER_SAC_REVIEW', status_label: 'Under review by the Standing Appellate Committee', submitted_at: '2026-10-28T09:00:00Z', grounds: 'We are aggrieved by the decision and say why here.', documents: [], can_add_documents: true, council: null, note: 'An appeal is decided by the Council, with the SAC’s recommendation. Submitting it or adding evidence does not promise any outcome.' } },
    })
    const api = mockApi({ ...base, 'GET /api/applications/app-1/decision': { body: status() }, 'POST /api/applications/app-1/appeal': { status: 201, body: submitted } })
    renderApp('/app/applications/app-1')
    expect(await screen.findByText('You may appeal')).toBeInTheDocument()
    expect(screen.getByText(/APH 2024-27 Ch\. II cl\. 2\.24\(a\)/, { selector: '.alert *, .alert' })).toBeInTheDocument()
    expect(screen.getByText(/calendar-day count and is not verified/)).toBeInTheDocument()
    const submit = screen.getByRole('button', { name: 'Submit the appeal…' })
    expect(submit).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Grounds of the appeal'), 'We are aggrieved by the decision and say why here.')
    await userEvent.click(submit)
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/can be made once/)).toBeInTheDocument()
    expect(within(dialog).getByText(/not predicted or promised/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Submit' }))
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/applications/app-1/appeal')).toBe(true))
    const body = api.calls.find((c) => c.key.endsWith('/appeal'))!.body as { grounds: string; idempotency_key: string }
    expect(body.idempotency_key.length).toBeGreaterThanOrEqual(8)
  })

  it('offers no appeal form when the period has ended and says why', async () => {
    mockApi({
      ...base,
      'GET /api/applications/app-1/decision': { body: status({ appeal: { eligibility: { state: 'WINDOW_CLOSED', message: 'The appeal period has ended.', deadline_at: '2026-11-03T13:00:00Z', trigger_at: '2026-10-27T13:00:00Z', outcome: 'LOR', clause_ref: 'APH 2024-27 Ch. II cl. 2.24(a)' }, submitted: null } }) },
    })
    renderApp('/app/applications/app-1')
    expect(await screen.findByText('The appeal period has ended.', { exact: false })).toBeInTheDocument()
    expect(screen.queryByLabelText('Grounds of the appeal')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Submit the appeal/ })).not.toBeInTheDocument()
  })

  it('shows a blocked configuration without inventing a deadline', async () => {
    mockApi({
      ...base,
      'GET /api/applications/app-1/decision': { body: status({ appeal: { eligibility: { state: 'BLOCKED_BY_CONFIGURATION', message: 'The rule version has no appeal-window policy, so no deadline can be calculated.', deadline_at: null, trigger_at: null, outcome: 'LOR', clause_ref: null }, submitted: null } }) },
    })
    renderApp('/app/applications/app-1')
    expect(await screen.findByText(/no appeal-window policy, so no deadline can be calculated/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Submit the appeal/ })).not.toBeInTheDocument()
    expect(screen.queryByText(/The period ends/)).not.toBeInTheDocument()
  })

  it('shows nothing for an application that has not reached the decision stage', async () => {
    mockApi({ ...base, 'GET /api/applications/app-1/decision': { body: { official_status: { state: 'NO_DECISION_CASE', label: 'Not yet at the decision stage' }, decision: null, appeal: null, closed: false, not_a_decision: NOT_A_DECISION } } })
    renderApp('/app/applications/app-1')
    await screen.findByText(/This application is read-only/)
    expect(screen.queryByText('Decision and appeal')).not.toBeInTheDocument()
  })

  it('does not let an institution open a staff decision page', async () => {
    mockApi({ 'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD })
    renderApp('/app/decisions/dc1')
    expect(await screen.findByText('You do not have access to this page')).toBeInTheDocument()
  })
})

describe('navigation', () => {
  it('shows the decision queue for decision roles, not as planned', async () => {
    mockApi({ 'GET /api/auth/session': { body: EC }, ...UNREAD, 'GET /api/decisions/cases': { body: queue([]) } })
    renderApp('/app/executive')
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: /Decision queue/ })).toBeInTheDocument()
    expect(within(nav).queryByText('Planned')).not.toBeInTheDocument()
    expect(screen.getByText(/Phase 6/)).toBeInTheDocument()
  })
})
