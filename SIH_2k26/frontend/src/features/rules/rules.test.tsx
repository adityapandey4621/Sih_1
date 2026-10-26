import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { category, version } from '../../test/fixtures'
import { makeUser, mockApi, renderApp } from '../../test/helpers'
import { blankItem, blankStage, fromVersion, toPayload } from './editorModel'

afterEach(() => vi.unstubAllGlobals())

const AUTHOR = makeUser(['dashboard:rules', 'rules:read', 'rules:edit'], { role: 'rules_admin', role_label: 'Rules administrator' })
const APPROVER = makeUser(['dashboard:rules', 'rules:read', 'rules:approve'], { role: 'rules_approver', role_label: 'Rules approver' })
const AUDITOR = makeUser(['dashboard:audit', 'audit:read', 'rules:read'], { role: 'auditor', role_label: 'Auditor' })
const as = (u: ReturnType<typeof makeUser>) => ({ 'GET /api/auth/session': { body: u } })

describe('version page: actions follow permission and state', () => {
  const open = (u: ReturnType<typeof makeUser>, v = version()) => {
    mockApi({ ...as(u), 'GET /api/rules/versions/rv-1': { body: v } })
    renderApp('/app/rules/versions/rv-1')
  }

  it('lets an author edit and submit a draft, but never publish', async () => {
    open(AUTHOR)
    expect(await screen.findByRole('link', { name: 'Edit draft' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Submit for review' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /publish|retire/i })).not.toBeInTheDocument()
  })

  it('gives an approver the review screen for a version in review, and no edit controls', async () => {
    open(APPROVER, version({ status: 'in_review' }))
    expect(await screen.findByRole('link', { name: 'Open review' })).toHaveAttribute('href', '/app/rules/versions/rv-1/review')
    expect(screen.queryByRole('link', { name: 'Edit draft' })).not.toBeInTheDocument()
  })

  it('lets only an approver retire, and requires a reason', async () => {
    open(APPROVER, version({ status: 'published', content_hash: 'cd'.repeat(32), published_at: '2026-10-26T10:00:00Z' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Retire version' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Applications already submitted keep exactly the configuration they were submitted under/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Retire version' })).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText('Reason for retiring'), 'Superseded by guidance')
    expect(within(dialog).getByRole('button', { name: 'Retire version' })).toBeEnabled()
  })

  it('is read-only for an auditor', async () => {
    open(AUDITOR, version({ status: 'published' }))
    expect(await screen.findByText(/your role cannot change it/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /submit|publish|retire|create new version/i })).not.toBeInTheDocument()
  })

  it('shows why a draft cannot be published yet, with stable codes', async () => {
    open(AUTHOR, version({ publication_issues: [{ code: 'checklist_missing', message: 'The checklist is empty. Required documents are never guessed.', path: 'checklist' }] }))
    expect(await screen.findByText(/1 thing to resolve before this can be published/)).toBeInTheDocument()
    expect(screen.getByText(/\[checklist_missing\]/)).toBeInTheDocument()
  })

  it('marks DEMO configuration and unverified sources', async () => {
    open(AUTHOR, version({ verification: 'unverified', rule_set: { id: 'rs-1', key: 'official-x', title: 'Official X', classification: 'OFFICIAL' }, demo_banner: null }))
    expect(await screen.findByText('Sources NOT verified against the official publication')).toBeInTheDocument()
    expect(screen.queryByText(/DEMO ONLY — NOT VERIFIED/)).not.toBeInTheDocument()
  })
})

describe('review screen', () => {
  it('requires explicit supersede for an overlapping version and repeats the DEMO warning', async () => {
    mockApi({
      ...as(APPROVER),
      'GET /api/rules/versions/rv-2': { body: version({ id: 'rv-2', version_number: 2, status: 'in_review', supersedes_version_id: 'rv-1', publication_issues: [{ code: 'overlap_same_rule_set', message: 'Overlaps published set-a v1 for 2026-27.', path: 'academic_years' }] }) },
      'GET /api/rules/versions/rv-1': { body: version({ status: 'published' }) },
    })
    renderApp('/app/rules/versions/rv-2/review')
    await userEvent.click(await screen.findByRole('button', { name: 'Publish version' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('This is DEMO ONLY configuration.')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Retire the currently published version of this rule set')).toBeInTheDocument()
  })

  it('blocks publishing while real problems remain and offers to send it back', async () => {
    mockApi({
      ...as(APPROVER),
      'GET /api/rules/versions/rv-1': { body: version({ status: 'in_review', publication_issues: [{ code: 'source_not_verified', message: 'Source is SECONDARY_UNVERIFIED.', path: 'sources.x' }] }) },
    })
    renderApp('/app/rules/versions/rv-1/review')
    expect(await screen.findByRole('button', { name: 'Publish version' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Request changes' })).toBeEnabled()
  })

  it('is not available to an author', async () => {
    mockApi({ ...as(AUTHOR) })
    renderApp('/app/rules/versions/rv-1/review')
    expect(await screen.findByText('You do not have access to this page')).toBeInTheDocument()
  })
})

describe('catalogue', () => {
  const sets = [{ id: 'rs-1', key: 'set-a', title: 'Set A', classification: 'DEMO', category, versions: [version()] }]

  it('shows create controls to an author only', async () => {
    mockApi({ ...as(AUTHOR), 'GET /api/rules/sets': { body: sets }, 'GET /api/rules/categories': { body: [category] } })
    renderApp('/app/rules')
    expect(await screen.findByRole('button', { name: /New rule set/ })).toBeInTheDocument()
  })

  it('shows no create controls to an auditor and labels demo and unverified clearly', async () => {
    mockApi({ ...as(AUDITOR), 'GET /api/rules/sets': { body: [...sets, { ...sets[0], id: 'rs-2', key: 'official-x', title: 'Official X', classification: 'OFFICIAL', versions: [version({ id: 'rv-9', verification: 'unverified', rule_set: { id: 'rs-2', key: 'official-x', title: 'Official X', classification: 'OFFICIAL' } })] }] }, 'GET /api/rules/categories': { body: [category] } })
    renderApp('/app/rules')
    expect(await screen.findByText('Official X')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /New rule set|Create new version/ })).not.toBeInTheDocument()
    expect(screen.getByText('DEMO ONLY')).toBeInTheDocument()
    expect(screen.getByText(/sources NOT verified/)).toBeInTheDocument()
  })

  it('denies the catalogue to roles without rules:read', async () => {
    mockApi({ ...as(makeUser(['dashboard:institute', 'application:create'])) })
    renderApp('/app/rules')
    expect(await screen.findByText('You do not have access to this page')).toBeInTheDocument()
  })
})

describe('editor model', () => {
  it('round-trips a version through the form state without losing configuration', () => {
    const v = version({
      checklist: [
        { item_key: 'kind', label: 'Kind', explanation: 'x', required: true, item_type: 'form_field', field_kind: 'select', options: ['A', 'B'], validation: null, applies_when: null, source_id: null, clause_ref: null, display_order: 0 },
        { item_key: 'b', label: 'B', explanation: 'x', required: false, item_type: 'form_field', field_kind: 'text', options: null, validation: { max_length: 40 }, applies_when: { item_key: 'kind', equals: 'B' }, source_id: null, clause_ref: 'cl. 1', display_order: 1 },
      ],
      route: [{ stage_key: 's1', label: 'S1', description: '', display_order: 0, is_conditional: true, condition_description: 'When needed', next_stage_keys: [], source_id: null, clause_ref: null }],
    })
    const payload = toPayload(fromVersion(v), 7)
    expect(payload.row_version).toBe(7)
    expect(payload.checklist).toEqual(v.checklist)
    expect(payload.route).toEqual(v.route)
  })

  it('sends empty durations as null, never as an invented number, and clears conditional text when unconditional', () => {
    const state = fromVersion(version())
    state.policies.push({ uid: 'p', policy_key: 'p1', label: 'P', deadline_type: 'INTERNAL_SLA', trigger_event: 'x', duration_value: '', duration_unit: '', calendar_convention: '', pause_resume_note: '', source_id: '', clause_ref: '' })
    state.stages.push({ ...blankStage(), stage_key: 'a', label: 'A', is_conditional: false, condition_description: 'stale text' })
    state.items.push({ ...blankItem(), item_key: 'x', label: 'X', item_type: 'document_upload', field_kind: 'select', optionsText: 'ignored' })
    const out = toPayload(state, 1)
    expect(out.deadlines[0]).toMatchObject({ duration_value: null, duration_unit: null, calendar_convention: null })
    expect(out.route[0]?.condition_description).toBeNull()
    expect(out.checklist[0]).toMatchObject({ field_kind: null, options: null })
  })
})
