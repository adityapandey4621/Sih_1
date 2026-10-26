import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { detail } from '../../test/fixtures'
import { makeUser, mockApi, renderApp } from '../../test/helpers'
import type { CommitteeCaseDetail, ComplianceRound, InstitutionStatus, QueueRow, Schedule } from './types'

afterEach(() => {
  vi.unstubAllGlobals()
})

const EVC_COORD = makeUser(['dashboard:evc-coordination', 'evc:queue', 'evc:coordinate', 'evidence:read', 'notifications:read'], { role: 'evc_coordinator', role_label: 'EVC coordinator' })
const EVC_MEMBER = makeUser(['dashboard:evc-member', 'evc:queue', 'evc:work', 'evidence:read', 'notifications:read'], { role: 'evc_member', role_label: 'EVC member' })
const SHC_COORD = makeUser(['dashboard:shc', 'shc:queue', 'shc:coordinate', 'evidence:read', 'notifications:read'], { role: 'shc_coordinator', role_label: 'SHC coordinator' })
const SHC_MEMBER = makeUser(['dashboard:shc', 'shc:queue', 'shc:work', 'evidence:read', 'notifications:read'], { role: 'shc_member', role_label: 'SHC member' })
const INSTITUTE = makeUser(['dashboard:institute', 'application:read_own', 'committee:status_own', 'shc:respond', 'notice:read_own', 'notifications:read'], { role: 'institute_user', role_label: 'Institution' })
const UNREAD = { 'GET /api/notifications': { body: { items: [], total: 0, unread: 0, page: 1, page_size: 20 } } }
const ERR = (status: number, code: string, message: string, issues: { code: string; message: string; path: string }[] = []) => ({ status, body: { detail: message, code, issues } })

const policy: NonNullable<CommitteeCaseDetail['policy']> = {
  id: 'p1', key: 'demo-evc-policy', version: 1, classification: 'DEMO', title: 'DEMO — EVC visit, checklist and report structure', banner: 'DEMO ONLY — fictional committee configuration. Not an AICTE checklist, composition or process.',
  sources: [{ title: 'DEMO ONLY', clause_ref: 'DEMO ONLY', verification_status: 'DEMO_ONLY' }], content_hash: 'ab'.repeat(32),
  modes: [
    { key: 'ONLINE', label: 'Online', verification: 'VERIFIED_OFFICIAL', requires: ['access_details'], requires_reason: false, clause_ref: 'APH 2024-27 Ch. I cl. 1.8 NOTE' },
    { key: 'PHYSICAL', label: 'Physical (in person)', verification: 'VERIFIED_OFFICIAL', requires: ['location'], requires_reason: true, clause_ref: 'APH 2024-27 Ch. I cl. 1.8 NOTE' },
  ],
  unverified_modes: [{ key: 'HYBRID', label: 'Hybrid', reason: 'A hybrid mode is reported in a December 2025 corrigendum that has not been verified.' }],
  checklist: [{ item_key: 'demo-premises', label: 'DEMO: premises match the application', description: 'Fictional item.', required: true, evidence_required: false, clause_ref: 'DEMO ONLY', demo: true }],
  report_sections: [{ key: 'summary', label: 'Summary of the visit', required: true }], shc: null, composition: { min_members: 1, note: 'DEMO' },
}

const shcPolicy: NonNullable<CommitteeCaseDetail['policy']> = {
  ...policy, key: 'demo-shc-policy', title: 'DEMO — SHC', modes: [], checklist: [], report_sections: [],
  shc: {
    compliance_submission: 'PERMITTED', compliance_clause_ref: 'APH 2024-27 Ch. II cl. 2.23.3', hearing: 'OPTIONAL', hearing_modes: policy.modes,
    recommendation_options: [{ key: 'RECOMMEND_NEXT_STAGE', label: 'Recommend that the case proceeds to the next stage' }, { key: 'DO_NOT_RECOMMEND', label: 'Do not recommend that the case proceeds' }],
    recommendation_sections: [{ key: 'summary', label: 'Summary of the SHC review', required: true }],
  },
}

const app = { id: 'app-1', reference: 'APP-AAAA-BBBB', institution_name: 'Test Institute', category_name: 'Category A', academic_year: '2026-27', status: 'SUBMITTED' }

const caseOf = (over: Partial<CommitteeCaseDetail> = {}): CommitteeCaseDetail => ({
  id: 'c1', stage: 'EVC', application: app, state: 'SELECTION_PENDING', state_label: 'Awaiting authorised selection', row_version: 2, scope: ['coordinator'], activated_at: '2026-10-27T10:00:00Z', completed_at: null, completion: null,
  trigger: { stage_key: 'evc', from_stage_key: 'scrutiny', source: 'scrutiny_handoff', determination: null, scrutiny_outcome: { outcome: 'COMPLETED_NO_OPEN_FINDINGS', approved: false }, rule_version: { rule_set: 'set-a', number: 1, classification: 'DEMO' }, route_clause: { clause_ref: 'Table 2.3', label: 'EVC', is_conditional: false } },
  policy, blockers: [],
  selection: { availability: { available: true, mode: 'DEMO_MANUAL', label: 'Demonstration selection', notice: 'Demonstration selection: a coordinator recorded fictional members. This is NOT the official AICTE committee selection process.', demo: true }, requests: [], assignees: [], min_members: 1 },
  schedule: { active: null, history: [] }, scrutiny: { state: 'SCRUTINY_COMPLETED', completion: { outcome: 'COMPLETED_NO_OPEN_FINDINGS' }, handoff: { status: 'QUEUED', to_stage_key: 'evc', reason: null } }, next: null, decision_handoff: null, events: [{ at: '2026-10-27T10:00:00Z', actor: 'System', action: 'stage_activated', from_state: null, to_state: 'SELECTION_PENDING', reason: null }],
  allowed_actions: ['request_selection'], outstanding_actions: ['Request an authorised selection of members.'], not_an_approval: 'This stage produces a record for the later decision process. It is not an approval, a rejection or a decision.',
  evc: { observations: [], evidence: [], draft: null, versions: [] }, ...over,
})

const row = (over: Partial<QueueRow> = {}): QueueRow => ({
  id: 'c1', stage: 'EVC', application: app, state: 'SELECTION_PENDING', state_label: 'Awaiting authorised selection', row_version: 2, activated_at: '2026-10-27T10:00:00Z', assignees: [], schedule: null, blocked: false, blockers: [], demo: true,
  awaiting_determination: false, completed_at: null, ...over,
})
const queue = (items: QueueRow[], scope: 'coordinator' | 'member' = 'coordinator', counts: Record<string, number> = { SELECTION_PENDING: items.length }) => ({ scope, total: items.length, page: 1, page_size: 20, counts, items })

const schedule = (over: Partial<Schedule> = {}): Schedule => ({
  id: 's1', kind: 'VISIT', sequence: 1, status: 'PROPOSED', start_at: '2030-03-04T04:30:00Z', timezone: 'Asia/Kolkata', start_local: '2030-03-04T10:00', mode: 'ONLINE', mode_reason: null, location: null, access_details: 'Joining instructions will follow.',
  supersedes_id: null, change_reason: null, created_at: '2026-10-27T10:00:00Z', confirmed_at: null, closed_at: null, closed_reason: null, row_version: 1, policy: { classification: 'DEMO' }, ...over,
})

describe('EVC queue', () => {
  it('lists real cases with state, schedule, members and a demo marker, and states what the person can see', async () => {
    mockApi({
      'GET /api/auth/session': { body: EVC_COORD }, ...UNREAD,
      'GET /api/evc/cases': { body: queue([row({ state: 'SCHEDULED', state_label: 'Visit scheduled', assignees: ['Test evc_member'], schedule: { status: 'CONFIRMED', start_at: '2030-03-04T04:30:00Z', start_local: '2030-03-04T10:00', timezone: 'Asia/Kolkata', mode: 'ONLINE' } }), row({ id: 'c2', blocked: true, blockers: ['no_committee_policy'] })]) },
    })
    renderApp('/app/evc-coordination')
    const link = await screen.findAllByRole('link', { name: 'APP-AAAA-BBBB' })
    const first = link[0]!.closest('tr')!
    expect(link[0]).toHaveAttribute('href', '/app/evc/c1')
    expect(within(first).getByText('Visit scheduled')).toBeInTheDocument()
    expect(within(first).getByText(/4 Mar 2030, 10:00 \(Asia\/Kolkata\)/)).toBeInTheDocument()
    expect(within(first).getByText('Test evc_member')).toBeInTheDocument()
    expect(within(first).getByText(/DEMO ONLY configuration/)).toBeInTheDocument()
    expect(screen.getByText('Blocked by missing configuration')).toBeInTheDocument()
    expect(screen.getByText(/nothing on this page can approve or decide/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument()
  })

  it('tells a member that holding the role alone shows no cases', async () => {
    mockApi({ 'GET /api/auth/session': { body: EVC_MEMBER }, ...UNREAD, 'GET /api/evc/cases': { body: queue([], 'member', {}) } })
    renderApp('/app/evc-member')
    expect(await screen.findByText(/Holding a member role alone does not show you any case/)).toBeInTheDocument()
    expect(screen.getByText(/only once you have been assigned/)).toBeInTheDocument()
  })

  it('filters by state through the server', async () => {
    const api = mockApi({ 'GET /api/auth/session': { body: EVC_COORD }, ...UNREAD, 'GET /api/evc/cases': { body: queue([row()], 'coordinator', { SELECTION_PENDING: 3, SCHEDULED: 1 }) } })
    renderApp('/app/evc-coordination')
    await userEvent.click(await screen.findByRole('button', { name: /Awaiting authorised selection 3/ }))
    await waitFor(() => expect(api.calls.some((c) => c.url.includes('state=SELECTION_PENDING'))).toBe(true))
  })

  it('refuses a person without the permission', async () => {
    mockApi({ 'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD })
    renderApp('/app/evc-coordination')
    expect(await screen.findByText('You do not have access to this page')).toBeInTheDocument()
  })
})

describe('EVC case: selection, schedule, route', () => {
  it('records a demonstration selection with a stable idempotency key and labels it as not the official process', async () => {
    const api = mockApi({
      'GET /api/auth/session': { body: EVC_COORD }, ...UNREAD, 'GET /api/evc/cases/c1': { body: caseOf() },
      'GET /api/evc/assignable': { body: { demo_only: true, members: [{ id: 'm1', name: 'Demo Member One' }] } },
      'POST /api/evc/cases/c1/selection': { body: caseOf({ state: 'SCHEDULE_PENDING', state_label: 'Awaiting schedule confirmation', allowed_actions: ['propose_schedule', 'reselect'] }) },
    })
    renderApp('/app/evc/c1')
    expect(await screen.findByText(/DEMO ONLY — fictional committee configuration/)).toBeInTheDocument()
    expect(screen.getByText(/not an approval, a rejection or a decision/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: 'Selection and schedule' }))
    expect(await screen.findByText(/NOT the official AICTE committee selection process/)).toBeInTheDocument()
    const record = screen.getByRole('button', { name: 'Record demonstration selection' })
    expect(record).toBeDisabled()
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Demo Member One' }))
    await userEvent.click(record)
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/evc/cases/c1/selection')).toBe(true))
    const body = api.calls.find((c) => c.key === 'POST /api/evc/cases/c1/selection')!.body as { user_ids: string[]; idempotency_key: string }
    expect(body.user_ids).toEqual(['m1'])
    expect(body.idempotency_key.length).toBeGreaterThanOrEqual(8)
  })

  it('says plainly that the official selection is not connected and offers no way to request it', async () => {
    mockApi({
      'GET /api/auth/session': { body: EVC_COORD }, ...UNREAD,
      'GET /api/evc/cases/c1': { body: caseOf({ allowed_actions: [], selection: { availability: { available: false, mode: 'EXTERNAL_PORTAL', label: 'Official selection (not connected)', notice: 'Committee members must come from the authorised AICTE selection process. No interface to that process is connected to this system, so no selection can be requested or recorded here.', demo: false }, requests: [], assignees: [], min_members: 1 } }) },
    })
    renderApp('/app/evc/c1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Selection and schedule' }))
    expect(await screen.findByText('Official selection (not connected)')).toBeInTheDocument()
    expect(screen.getByText(/No interface to that process is connected/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Request selection|Record demonstration selection/ })).not.toBeInTheDocument()
    expect(screen.getByText(/cannot be requested here until an authorised interface/)).toBeInTheDocument()
  })

  it('shows a failed selection result without implying anyone was assigned', async () => {
    mockApi({
      'GET /api/auth/session': { body: EVC_COORD }, ...UNREAD,
      'GET /api/evc/cases/c1': { body: caseOf({ selection: { ...caseOf().selection, availability: { ...caseOf().selection.availability, available: false, demo: false, label: 'Scripted external provider' }, requests: [{ id: 'r1', request_ref: 'SEL-AAAA', provider: 'EXTERNAL_PORTAL', demo: false, status: 'INCOMPLETE', requested_at: '2026-10-27T10:00:00Z', result_at: '2026-10-27T10:05:00Z', result_source: 'portal', members: [], error_code: 'member_not_verified', error_detail: '1 selected member(s) could not be verified as active evc members.', external_ref: null }] } }) },
    })
    renderApp('/app/evc/c1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Selection and schedule' }))
    expect(await screen.findByText('No members are assigned to this case.')).toBeInTheDocument()
    await userEvent.click(screen.getByText(/Selection requests and results/))
    expect(screen.getByText(/could not be verified/)).toBeInTheDocument()
  })

  it('proposes a schedule using only the configured modes, explains the unverified one, and sends the local time and zone', async () => {
    const api = mockApi({
      'GET /api/auth/session': { body: EVC_COORD }, ...UNREAD,
      'GET /api/evc/cases/c1': { body: caseOf({ state: 'SCHEDULE_PENDING', state_label: 'Awaiting schedule confirmation', allowed_actions: ['propose_schedule', 'reselect'] }) },
      'POST /api/evc/cases/c1/schedule': { status: 201, body: caseOf({ state: 'SCHEDULE_PENDING', schedule: { active: schedule(), history: [schedule()] }, allowed_actions: ['reschedule', 'confirm_schedule', 'cancel_schedule', 'reselect'] }) },
    })
    renderApp('/app/evc/c1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Selection and schedule' }))
    expect(screen.getByText(/never says that anyone is free/)).toBeInTheDocument()
    await userEvent.click(await screen.findByRole('button', { name: /Propose a visit schedule/ }))
    const form = await screen.findByRole('form', { name: 'Propose a schedule' })
    expect(within(form).queryByRole('option', { name: 'Hybrid' })).not.toBeInTheDocument()
    expect(within(form).getByText(/Hybrid is not offered: A hybrid mode is reported in a December 2025 corrigendum/)).toBeInTheDocument()
    await userEvent.type(within(form).getByLabelText('Date and time (local)'), '2030-03-04T10:00')
    await userEvent.selectOptions(within(form).getByLabelText('Mode'), 'ONLINE')
    await userEvent.type(await within(form).findByLabelText(/How to join/), 'Joining instructions will follow.')
    await userEvent.click(within(form).getByRole('button', { name: 'Propose' }))
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/evc/cases/c1/schedule')).toBe(true))
    expect(api.calls.find((c) => c.key === 'POST /api/evc/cases/c1/schedule')!.body).toMatchObject({ start_local: '2030-03-04T10:00', timezone: 'Asia/Kolkata', mode: 'ONLINE', access_details: 'Joining instructions will follow.' })
    expect(await screen.findByText('Proposed — not yet confirmed')).toBeInTheDocument()
  })

  it('asks for the circumstance and place when a physical visit is chosen', async () => {
    mockApi({ 'GET /api/auth/session': { body: EVC_COORD }, ...UNREAD, 'GET /api/evc/cases/c1': { body: caseOf({ state: 'SCHEDULE_PENDING', allowed_actions: ['propose_schedule'] }) } })
    renderApp('/app/evc/c1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Selection and schedule' }))
    await userEvent.click(await screen.findByRole('button', { name: /Propose a visit schedule/ }))
    const form = await screen.findByRole('form', { name: 'Propose a schedule' })
    await userEvent.selectOptions(within(form).getByLabelText('Mode'), 'PHYSICAL')
    expect(await within(form).findByLabelText('Circumstance that justifies this mode')).toBeInTheDocument()
    expect(within(form).getByLabelText('Place')).toBeInTheDocument()
  })

  it('shows rescheduled history and confirms a proposed schedule with its version', async () => {
    const api = mockApi({
      'GET /api/auth/session': { body: EVC_COORD }, ...UNREAD,
      'GET /api/evc/cases/c1': { body: caseOf({ state: 'SCHEDULE_PENDING', schedule: { active: schedule({ id: 's2', sequence: 2, change_reason: 'The institution asked for a later date.', supersedes_id: 's1', row_version: 3 }), history: [schedule({ status: 'SUPERSEDED', closed_reason: 'The institution asked for a later date.' }), schedule({ id: 's2', sequence: 2, row_version: 3 })] }, allowed_actions: ['reschedule', 'confirm_schedule', 'cancel_schedule', 'reselect'] }) },
      'POST /api/evc/cases/c1/schedule/s2/confirm': { body: caseOf({ state: 'SCHEDULED', state_label: 'Visit scheduled', schedule: { active: schedule({ id: 's2', status: 'CONFIRMED', row_version: 4 }), history: [] }, allowed_actions: ['reschedule', 'cancel_schedule', 'reselect'] }) },
    })
    renderApp('/app/evc/c1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Selection and schedule' }))
    expect(await screen.findByText(/Rescheduled because: The institution asked for a later date/)).toBeInTheDocument()
    await userEvent.click(screen.getByText(/Earlier schedules/))
    expect(screen.getByText('Replaced by a later schedule')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Confirm this schedule' }))
    await waitFor(() => expect(api.calls.find((c) => c.key.endsWith('/confirm'))?.body).toEqual({ row_version: 3 }))
    expect(await screen.findByText('Confirmed')).toBeInTheDocument()
  })

  it('records a determination for a conditional next stage, including that it does not apply', async () => {
    const api = mockApi({
      'GET /api/auth/session': { body: EVC_COORD }, ...UNREAD,
      'GET /api/evc/cases/c1': { body: caseOf({ state: 'COMPLETED', state_label: 'EVC stage completed (not an approval)', allowed_actions: ['determine_next'], completed_at: '2026-10-27T12:00:00Z', next: { status: 'AWAITING_DETERMINATION', to_stage_key: null, to_stage_label: null, reason: '“SHC” applies only under a condition (Table 2.3 AA). An authorised person must determine whether it applies.', options: [{ stage_key: 'shc', label: 'SHC', is_conditional: true, condition_description: 'Table 2.3 AA' }], determined_at: null, determination_reason: null } }) },
      'POST /api/evc/cases/c1/route/determine': { body: caseOf({ state: 'COMPLETED', allowed_actions: [], next: { status: 'NO_FURTHER_STAGE', to_stage_key: null, to_stage_label: null, reason: 'Recorded.', options: [], determined_at: '2026-10-27T12:05:00Z', determination_reason: 'Not needed.' }, decision_handoff: { status: 'READY_FOR_DECISION_STAGE', created_at: '2026-10-27T12:05:00Z', basis: { note: 'A hand-off record only. No decision, approval or rejection has been made.', stages: [{ stage_key: 'scrutiny' }, { stage_key: 'evc' }] } } }) },
    })
    renderApp('/app/evc/c1')
    expect(await screen.findByText('An authorised person must determine the next stage')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('Determination'), '__none__')
    await userEvent.type(screen.getByLabelText('Reason (kept on record)'), 'Nothing in the report needs it.')
    await userEvent.click(screen.getByRole('button', { name: 'Record determination' }))
    await waitFor(() => expect(api.calls.find((c) => c.key.endsWith('/route/determine'))?.body).toEqual({ stage_key: null, reason: 'Nothing in the report needs it.' }))
    expect(await screen.findByText('Ready for the decision stage')).toBeInTheDocument()
    expect(screen.getByText(/No decision, approval or rejection has been made/)).toBeInTheDocument()
  })

  it('tells the person when the case changed under them and reloads it', async () => {
    let loads = 0
    mockApi({
      'GET /api/auth/session': { body: EVC_COORD }, ...UNREAD,
      'GET /api/evc/cases/c1': () => ({ body: caseOf({ row_version: ++loads, state: 'SCHEDULE_PENDING', allowed_actions: ['propose_schedule'] }) }),
      'POST /api/evc/cases/c1/schedule': ERR(409, 'stale_version', 'This case was changed by someone else since you opened it. Reload it to see the latest state.'),
    })
    renderApp('/app/evc/c1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Selection and schedule' }))
    await userEvent.click(await screen.findByRole('button', { name: /Propose a visit schedule/ }))
    const form = await screen.findByRole('form', { name: 'Propose a schedule' })
    await userEvent.type(within(form).getByLabelText('Date and time (local)'), '2030-03-04T10:00')
    await userEvent.selectOptions(within(form).getByLabelText('Mode'), 'ONLINE')
    await userEvent.type(await within(form).findByLabelText(/How to join/), 'x')
    await userEvent.click(within(form).getByRole('button', { name: 'Propose' }))
    expect(await screen.findByText('The case changed')).toBeInTheDocument()
    expect(loads).toBeGreaterThanOrEqual(2)
  })

  it('shows "not found" for a case outside the person’s scope', async () => {
    mockApi({ 'GET /api/auth/session': { body: EVC_MEMBER }, ...UNREAD, 'GET /api/evc/cases/c9': { status: 404, body: { detail: 'EVC case not found.', code: 'not_found' } } })
    renderApp('/app/evc/c9')
    expect(await screen.findByText('Case not found')).toBeInTheDocument()
    expect(screen.getByText(/not within your scope/)).toBeInTheDocument()
  })
})

describe('EVC visit workspace', () => {
  const visit = (over: Partial<CommitteeCaseDetail> = {}) =>
    caseOf({ scope: ['member'], state: 'VISIT_IN_PROGRESS', state_label: 'Visit in progress — report being prepared', allowed_actions: ['record_observation', 'add_evidence', 'save_report', 'submit_report'], outstanding_actions: ['Record observations and submit the report.'],
      evc: { observations: [], evidence: [], draft: { id: 'r1', kind: 'EVC_REPORT', version_number: 1, status: 'DRAFT', content: { sections: {} }, row_version: 1, created_at: '2026-10-27T10:00:00Z', submitted_at: null, submitted_by: null, created_by: 'Test', content_hash: null, supersedes_id: null, amendment_reason: null }, versions: [] }, ...over })

  it('starts the visit only from a scheduled case, sending the case version', async () => {
    const api = mockApi({
      'GET /api/auth/session': { body: EVC_MEMBER }, ...UNREAD,
      'GET /api/evc/cases/c1': { body: visit({ state: 'SCHEDULED', state_label: 'Visit scheduled', allowed_actions: ['start_visit'], row_version: 7 }) },
      'POST /api/evc/cases/c1/visit/start': { body: visit({ row_version: 8 }) },
    })
    renderApp('/app/evc/c1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Visit and report' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Start the visit' }))
    await waitFor(() => expect(api.calls.find((c) => c.key.endsWith('/visit/start'))?.body).toEqual({ row_version: 7 }))
  })

  it('records an observation with neutral statuses, an internal note kept apart, and the case version', async () => {
    const api = mockApi({
      'GET /api/auth/session': { body: EVC_MEMBER }, ...UNREAD,
      'GET /api/evc/cases/c1': { body: visit() },
      'GET /api/evc/cases/c1/evidence': { body: { application_id: 'app-1', reference: 'APP-AAAA-BBBB', status: 'SUBMITTED', academic_year: '2026-27', category_name: 'A', institution_name: 'T', viewer: 'reviewer', items: [], cross_document_findings: [], disclaimer: '', ocr_available: false, malware_scanner: 'none' } },
      'PUT /api/evc/cases/c1/observations/demo-premises': { body: visit({ evc: { observations: [{ id: 'o1', item_key: 'demo-premises', status: 'OBSERVED', note: 'Matches.', internal_note: null, shortcoming: false, document_refs: [], row_version: 1, recorded_by: 'Test', updated_at: '2026-10-27T10:30:00Z' }], evidence: [], draft: null, versions: [] } }) },
    })
    renderApp('/app/evc/c1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Visit and report' }))
    const form = await screen.findByRole('form', { name: /Observation for DEMO: premises/ })
    const status = within(form).getByLabelText('What you observed')
    expect(within(status).queryByRole('option', { name: /compliant|approved/i })).not.toBeInTheDocument()
    expect(within(status).getAllByRole('option').map((o) => o.textContent)).toEqual(['Select…', 'Observed', 'Not observed', 'Evidence unavailable', 'Uncertain'])
    await userEvent.selectOptions(status, 'OBSERVED')
    await userEvent.type(within(form).getByLabelText(/Observation \(goes into the report\)/), 'Matches.')
    await userEvent.type(within(form).getByLabelText(/Internal note/), 'Check the plan again.')
    await userEvent.click(within(form).getByRole('button', { name: 'Record observation' }))
    await waitFor(() => expect(api.calls.some((c) => c.key === 'PUT /api/evc/cases/c1/observations/demo-premises')).toBe(true))
    expect(api.calls.find((c) => c.key.includes('/observations/'))!.body).toMatchObject({ status: 'OBSERVED', note: 'Matches.', internal_note: 'Check the plan again.', shortcoming: false, row_version: null })
  })

  it('is honest that visit files are stored privately but not scanned or read', async () => {
    mockApi({ 'GET /api/auth/session': { body: EVC_MEMBER }, ...UNREAD, 'GET /api/evc/cases/c1': { body: visit() }, 'GET /api/evc/cases/c1/evidence': { body: { items: [] } } })
    renderApp('/app/evc/c1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Visit and report' }))
    expect(await screen.findByText(/scanned for malware or read by text recognition/)).toBeInTheDocument()
    expect(screen.getByText(/never opens or vouches for it/)).toBeInTheDocument()
  })

  it('lists what is missing when the server refuses to submit the report', async () => {
    mockApi({
      'GET /api/auth/session': { body: EVC_MEMBER }, ...UNREAD,
      'GET /api/evc/cases/c1': { body: visit({ evc: { observations: [], evidence: [], draft: { id: 'r1', kind: 'EVC_REPORT', version_number: 1, status: 'DRAFT', content: { sections: { summary: 'The visit took place.' } }, row_version: 3, created_at: '2026-10-27T10:00:00Z', submitted_at: null, submitted_by: null, created_by: 'T', content_hash: null, supersedes_id: null, amendment_reason: null }, versions: [] } }) },
      'GET /api/evc/cases/c1/evidence': { body: { items: [] } },
      'POST /api/evc/cases/c1/report/submit': ERR(409, 'report_incomplete', 'The report cannot be submitted yet.', [{ code: 'observation_missing', message: 'No observation was recorded for “DEMO: premises match the application”.', path: 'demo-premises' }]),
    })
    renderApp('/app/evc/c1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Visit and report' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Submit the report…' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Submit the report' }))
    expect(await screen.findByText('The report cannot be submitted yet')).toBeInTheDocument()
    expect(screen.getByText(/No observation was recorded for/)).toBeInTheDocument()
  })

  it('never labels a submitted report as approved and keeps earlier versions visible', async () => {
    mockApi({
      'GET /api/auth/session': { body: EVC_COORD }, ...UNREAD,
      'GET /api/evc/cases/c1': { body: caseOf({ state: 'REPORT_SUBMITTED', state_label: 'Report submitted — awaiting stage completion', allowed_actions: ['complete', 'open_amendment'], evc: { observations: [], evidence: [], draft: null, versions: [{ id: 'r1', kind: 'EVC_REPORT', version_number: 1, status: 'SUBMITTED', content: { sections: { summary: 'The visit took place.' } }, row_version: 2, created_at: '2026-10-27T10:00:00Z', submitted_at: '2026-10-27T11:00:00Z', submitted_by: 'Test evc_member', created_by: 'T', content_hash: 'cd'.repeat(32), supersedes_id: null, amendment_reason: null, source_snapshot: { documents: [{ item_key: 'doc-main', version_number: 2 }], observations: [{}], evidence: [], visit: { mode: 'ONLINE' } } }] } }) },
    })
    renderApp('/app/evc/c1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Visit and report' }))
    expect(await screen.findByText(/Version 1/)).toBeInTheDocument()
    expect(screen.getByText(/Rests on doc-main v2/)).toBeInTheDocument()
    expect(screen.queryByText(/approved/i)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open an amendment…' })).toBeInTheDocument()
  })
})

describe('SHC', () => {
  const shc = (over: Partial<CommitteeCaseDetail> = {}): CommitteeCaseDetail =>
    caseOf({
      id: 's1', stage: 'SHC', state: 'PREPARATION', state_label: 'Being prepared by the coordinator', policy: shcPolicy, allowed_actions: ['add_requirement', 'edit_requirement', 'request_compliance'], outstanding_actions: ['Prepare the requirements the institution is asked to address, then request compliance.'],
      trigger: { stage_key: 'shc', from_stage_key: 'scrutiny', source: 'scrutiny_handoff', determination: null, scrutiny_outcome: { outcome: 'COMPLETED_NO_OPEN_FINDINGS', approved: false }, rule_version: { rule_set: 'set-a', number: 1, classification: 'DEMO' }, route_clause: { clause_ref: null, label: 'SHC', is_conditional: false } },
      evc: undefined,
      shc: { shortcomings: [{ origin: 'scrutiny_finding', id: 'f1', summary: 'The letter does not show its validity.', sequence: 1 }], prepared: [], rounds: [], findings: [], proceedings: [], draft: null, versions: [] }, ...over,
    })

  it('offers the shortcomings from earlier stages as internal pointers, not as institution-facing text', async () => {
    mockApi({ 'GET /api/auth/session': { body: SHC_COORD }, ...UNREAD, 'GET /api/shc/cases/s1': { body: shc() } })
    renderApp('/app/shc/s1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Requirements and compliance' }))
    expect(await screen.findByText(/Shortcomings recorded when this stage was activated/)).toBeInTheDocument()
    expect(screen.getByText(/internal records from earlier stages/)).toBeInTheDocument()
    expect(screen.getByText(/Cl\. 2\.23\.3|Ch\. II cl\. 2\.23\.3/)).toBeInTheDocument()
    expect(screen.getByText(/never accepted automatically/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Request compliance…' })).toBeDisabled() // nothing prepared yet
  })

  it('adds a requirement and requests compliance with a warning that it freezes and no email is sent', async () => {
    const prepared = { id: 'q1', position: 0, title: 'Show the validity period', description: 'Please show it.', requested_evidence: '', item_key: null, origin: { type: 'manual' }, source_ref: null, source_ref_status: 'NEEDS_VERIFICATION', row_version: 1, round_id: null }
    const api = mockApi({
      'GET /api/auth/session': { body: SHC_COORD }, ...UNREAD, 'GET /api/shc/cases/s1': { body: shc() },
      'POST /api/shc/cases/s1/requirements': { status: 201, body: shc({ shc: { ...shc().shc!, prepared: [prepared] } }) },
      'POST /api/shc/cases/s1/compliance/request': { body: shc({ state: 'AWAITING_COMPLIANCE', allowed_actions: [], shc: { ...shc().shc!, rounds: [{ id: 'rd1', number: 1, status: 'OPEN', instructions: 'Please respond.', requested_at: '2026-10-27T10:00:00Z', response_received_at: null, policy_resolution: { state: 'NO_DURATION', message: 'No response period is established by the configuration. No due date has been set.' }, requirements: [{ ...prepared, round_id: 'rd1', position: 1 }], response: null }] } }) },
    })
    renderApp('/app/shc/s1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Requirements and compliance' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Add a requirement…' }))
    const form = await screen.findByRole('form', { name: 'Add a requirement' })
    await userEvent.type(within(form).getByLabelText('Title'), 'Show the validity period')
    await userEvent.type(within(form).getByLabelText('What the institution is asked to address'), 'Please show it.')
    await userEvent.click(within(form).getByRole('button', { name: 'Add requirement' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Request compliance…' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/can no longer be changed.*no email or SMS is sent/)).toBeInTheDocument()
    await userEvent.type(within(dialog).getByLabelText('Instructions the institution will see'), 'Please respond.')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Send the request' }))
    await waitFor(() => expect(api.calls.find((c) => c.key.endsWith('/compliance/request'))?.body).toEqual({ instructions: 'Please respond.', row_version: 2 }))
    expect(await screen.findByText(/No response period is established by the configuration/)).toBeInTheDocument()
  })

  it('records a finding that assesses evidence but never says accepted', async () => {
    const base = shc({ scope: ['member'], state: 'UNDER_REVIEW', state_label: 'Under review', allowed_actions: ['record_finding', 'edit_finding', 'record_proceeding', 'save_recommendation', 'submit_recommendation'] })
    const api = mockApi({
      'GET /api/auth/session': { body: SHC_MEMBER }, ...UNREAD,
      'GET /api/shc/cases/s1': { body: { ...base, shc: { ...base.shc!, rounds: [{ id: 'rd1', number: 1, status: 'RESPONSE_RECEIVED', instructions: 'x', requested_at: '2026-10-27T10:00:00Z', response_received_at: null, policy_resolution: null, requirements: [{ id: 'q1', position: 1, title: 'Show the validity period', description: 'd', requested_evidence: '', item_key: null, origin: null, source_ref: null, source_ref_status: 'NEEDS_VERIFICATION', row_version: 1, round_id: 'rd1' }], response: null }] } } },
      'POST /api/shc/cases/s1/findings': { status: 201, body: base },
    })
    renderApp('/app/shc/s1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Review' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Record a finding…' }))
    const form = await screen.findByRole('form', { name: 'Record a finding' })
    const assessment = within(form).getByLabelText('What the evidence shows')
    expect(within(assessment).queryByRole('option', { name: /accepted|compliant|approved/i })).not.toBeInTheDocument()
    await userEvent.selectOptions(within(form).getByLabelText('Requirement'), 'q1')
    await userEvent.selectOptions(assessment, 'EVIDENCE_SUPPORTS')
    await userEvent.type(within(form).getByLabelText('Reasons'), 'The letter now states its validity.')
    await userEvent.click(within(form).getByRole('button', { name: 'Record finding' }))
    await waitFor(() => expect(api.calls.find((c) => c.key.endsWith('/findings'))?.body).toEqual({ requirement_id: 'q1', assessment: 'EVIDENCE_SUPPORTS', text: 'The letter now states its validity.' }))
    expect(screen.getByText(/kept separate from the recommendation and from any decision/)).toBeInTheDocument()
  })

  it('keeps the recommendation apart from any decision', async () => {
    const base = shc({ scope: ['member'], state: 'UNDER_REVIEW', state_label: 'Under review', allowed_actions: ['save_recommendation', 'submit_recommendation'],
      shc: { shortcomings: [], prepared: [], rounds: [], findings: [], proceedings: [], draft: { id: 'd1', kind: 'SHC_RECOMMENDATION', version_number: 1, status: 'DRAFT', content: { option: null, sections: {} }, row_version: 1, created_at: '2026-10-27T10:00:00Z', submitted_at: null, submitted_by: null, created_by: 'T', content_hash: null, supersedes_id: null, amendment_reason: null }, versions: [] } })
    mockApi({ 'GET /api/auth/session': { body: SHC_MEMBER }, ...UNREAD, 'GET /api/shc/cases/s1': { body: base } })
    renderApp('/app/shc/s1')
    await userEvent.click(await screen.findByRole('tab', { name: 'Recommendation' }))
    expect(await screen.findByText(/It is not that decision, and nothing here approves or rejects an application/)).toBeInTheDocument()
    const select = screen.getByLabelText('Recommendation')
    expect(within(select).getAllByRole('option').map((o) => o.textContent)).toEqual(['Select…', 'Recommend that the case proceeds to the next stage', 'Do not recommend that the case proceeds'])
    expect(screen.getByRole('button', { name: 'Submit the recommendation…' })).toBeInTheDocument()
  })
})

describe('the institution', () => {
  const status = (over: Partial<InstitutionStatus> = {}): InstitutionStatus => ({
    evc: { state: 'SCHEDULED', label: 'Visit scheduled', schedule: { id: 's1', kind: 'VISIT', status: 'CONFIRMED', start_at: '2030-03-04T04:30:00Z', timezone: 'Asia/Kolkata', start_local: '2030-03-04T10:00', mode: 'ONLINE', location: null, access_details: 'Joining instructions will follow.', closed_reason: null, change_reason: null }, report: null, timeline: [] },
    shc: null, ready_for_decision_stage: false, not_a_decision: 'This stage produces a record for the later decision process. It is not an approval, a rejection or a decision.', ...over,
  })

  it('sees the visit schedule on the application page, and the report only when it is released, without internal notes', async () => {
    mockApi({
      'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD,
      'GET /api/applications/app-1': { body: detail({ status: 'SUBMITTED', editable: false }) },
      'GET /api/applications/app-1/scrutiny': { body: { state: 'SCRUTINY_COMPLETED', state_label: 'Scrutiny completed', explanation: 'Done.', notices: [], events: [], not_an_approval: 'This is not an approval.' } },
      'GET /api/applications/app-1/evidence': { body: { application_id: 'app-1', reference: 'APP-AAAA-BBBB', status: 'SUBMITTED', academic_year: '2026-27', category_name: 'A', institution_name: null, viewer: 'owner', items: [], cross_document_findings: [], disclaimer: '', ocr_available: true, malware_scanner: 'none' } },
      'GET /api/applications/app-1/committee': { body: status({ evc: { ...status().evc!, state: 'COMPLETED', label: 'Expert visit stage complete', report: { version_number: 1, submitted_at: '2026-10-27T11:00:00Z', demo: true, sections: [{ key: 'summary', label: 'Summary of the visit', text: 'The visit took place as scheduled.' }], observations: [{ item_key: 'demo-premises', label: 'DEMO: premises', status: 'OBSERVED', note: 'Matches the application.' }] } }, ready_for_decision_stage: true }) },
    })
    renderApp('/app/applications/app-1')
    expect(await screen.findByText('Expert visit stage complete')).toBeInTheDocument()
    expect(screen.getByText(/4 Mar 2030, 10:00 \(Asia\/Kolkata\)/)).toBeInTheDocument()
    expect(screen.getByText('The visit took place as scheduled.')).toBeInTheDocument()
    expect(screen.getByText(/Matches the application\./)).toBeInTheDocument()
    expect(screen.queryByText(/Internal:|internal note/i)).not.toBeInTheDocument()
    expect(screen.getByText(/The report is a record of the visit. It is not an approval or a decision/)).toBeInTheDocument()
    expect(screen.getByText('Ready for the decision stage')).toBeInTheDocument()
    expect(screen.getByText(/nothing has been decided/)).toBeInTheDocument()
  })

  const round = (over: Partial<ComplianceRound> = {}): ComplianceRound => ({
    id: 'rd1', number: 1, status: 'OPEN', case_state: 'AWAITING_COMPLIANCE', instructions: 'Please respond to each point.', requested_at: '2026-10-27T10:00:00Z', response_due_at: null, due_note: 'No response deadline has been established.', can_respond: true,
    demo_notice: 'DEMO ONLY — fictional committee configuration. Not an AICTE checklist, composition or process.', application: { id: 'app-1', reference: 'APP-AAAA-BBBB' },
    requirements: [{ id: 'q1', position: 1, title: 'Show the validity period', description: 'Please show it.', requested_evidence: 'A letter stating its validity.', item_key: 'doc-main', source_ref: null, explanation: '', documents: [] }], response: null, received_note: null, ...over,
  })

  it('answers each point, uploads evidence as a new version, and submits once with a stable key', async () => {
    const api = mockApi({
      'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD, 'GET /api/compliance/rd1': { body: round() },
      'PUT /api/compliance/rd1/response': { body: round({ response: { id: 'r1', status: 'DRAFT', explanation: '', row_version: 2, submitted_at: null } }) },
      'POST /api/compliance/rd1/requirements/q1/documents': { status: 201, body: round({ requirements: [{ ...round().requirements[0]!, documents: [{ id: 'd2', item_key: 'doc-main', version_number: 2, filename: 'compliance.pdf' }] }] }) },
      'POST /api/compliance/rd1/response/submit': { body: round({ can_respond: false, status: 'RESPONSE_RECEIVED', response: { id: 'r1', status: 'SUBMITTED', explanation: '', row_version: 3, submitted_at: '2026-10-27T12:00:00Z' }, received_note: 'A submitted response is received and goes to the committee for review. It is not accepted or assessed automatically.' }) },
    })
    renderApp('/app/compliance/rd1')
    expect(await screen.findByText('A response is needed')).toBeInTheDocument()
    expect(screen.getByText(/No response deadline has been established/)).toBeInTheDocument()
    expect(screen.getByText(/DEMO ONLY/)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Your response to point 1'), 'The corrected letter is enclosed.')
    const file = new File(['%PDF-1.4'], 'compliance.pdf', { type: 'application/pdf' })
    await userEvent.upload(screen.getByLabelText('Choose a file for point 1'), file)
    await waitFor(() => expect(api.calls.some((c) => c.key.endsWith('/documents'))).toBe(true))
    expect(await screen.findByText(/compliance\.pdf · version 2/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Submit response…' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/received, not accepted/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Submit response' }))
    await waitFor(() => expect(api.calls.some((c) => c.key.endsWith('/response/submit'))).toBe(true))
    const submit = api.calls.find((c) => c.key.endsWith('/response/submit'))!.body as { idempotency_key: string }
    expect(submit.idempotency_key.length).toBeGreaterThanOrEqual(8)
    expect(await screen.findByText('Response received')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Submit response…' })).not.toBeInTheDocument()
  })

  it('shows a received response as closed to editing and says it is not accepted', async () => {
    mockApi({ 'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD, 'GET /api/compliance/rd1': { body: round({ can_respond: false, status: 'RESPONSE_RECEIVED', response: { id: 'r1', status: 'SUBMITTED', explanation: '', row_version: 3, submitted_at: '2026-10-27T12:00:00Z' }, received_note: 'A submitted response is received and goes to the committee for review. It is not accepted or assessed automatically.' }) } })
    renderApp('/app/compliance/rd1')
    expect(await screen.findByText(/It is not accepted or assessed automatically/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Your response to point 1')).not.toBeInTheDocument()
  })

  it('lists compliance requests with a response-needed marker', async () => {
    mockApi({ 'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD, 'GET /api/compliance': { body: [{ id: 'rd1', number: 1, status: 'OPEN', requested_at: '2026-10-27T10:00:00Z', application: { id: 'app-1', reference: 'APP-AAAA-BBBB' }, case_state: 'AWAITING_COMPLIANCE', can_respond: true }] } })
    renderApp('/app/compliance')
    expect(await screen.findByRole('link', { name: 'Round 1' })).toHaveAttribute('href', '/app/compliance/rd1')
    expect(screen.getByText('Response needed')).toBeInTheDocument()
  })

  it('does not let an institution open a staff committee page', async () => {
    mockApi({ 'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD })
    renderApp('/app/shc/s1')
    expect(await screen.findByText('You do not have access to this page')).toBeInTheDocument()
  })
})

describe('navigation', () => {
  it('labels the EVC and SHC areas as real workspaces, not as planned, and shows Compliance to institutions', async () => {
    mockApi({ 'GET /api/auth/session': { body: EVC_COORD }, ...UNREAD, 'GET /api/evc/cases': { body: queue([]) } })
    renderApp('/app/evc-coordination')
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: /EVC coordination/ })).toBeInTheDocument()
    expect(within(nav).queryByText('Planned')).not.toBeInTheDocument() // Executive and appeals dashboards are not visible to this role
    expect(screen.getByText(/Phase 5/)).toBeInTheDocument()
  })

  it('shows Compliance in the institution’s navigation', async () => {
    mockApi({ 'GET /api/auth/session': { body: INSTITUTE }, ...UNREAD, 'GET /api/applications': { body: [] } })
    renderApp('/app/applications')
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: /Compliance/ })).toHaveAttribute('href', '/app/compliance')
  })
})
