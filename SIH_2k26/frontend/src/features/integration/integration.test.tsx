import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { makeUser, mockApi, renderApp } from '../../test/helpers'

afterEach(() => {
  vi.unstubAllGlobals()
})

const BUREAU = makeUser(['dashboard:executive', 'decision:queue', 'integration:read', 'integration:manage', 'notifications:read'], { role: 'regulation_bureau', role_label: 'Regulation Bureau' })
const AUDITOR = makeUser(['dashboard:audit', 'audit:read', 'integration:read', 'notifications:read'], { role: 'auditor', role_label: 'Auditor' })
const EC = makeUser(['dashboard:executive', 'decision:queue', 'decision:record', 'notifications:read'], { role: 'executive_committee', role_label: 'Executive Committee' })
const UNREAD = { 'GET /api/notifications': { body: { items: [], total: 0, unread: 0, page: 1, page_size: 20 } } }

const row = (over = {}) => ({
  id: 'sync-1', kind: 'DECISION_ISSUED', status: 'FAILED', application_reference: 'APP-AAAA-BBBB', attempts: 8, last_error: 'Gave up after 8 attempts. Last problem: The receiver could not be reached.', last_outcome: 'timeout',
  next_attempt_at: null, last_attempt_at: '2026-10-27T10:00:00Z', confirmed_at: null, external_ref: null, adapter: 'http', ...over,
})
const status = (over = {}) => ({
  mode: 'http', mode_note: 'The HTTP adapter speaks this project’s own contract to the configured URL. UNVERIFIED: it has not been tested against any authorised AICTE system.',
  adapter: { adapter: 'http', demo_only: false, configured: true, circuit_open: false, contract_version: 'sih-demo-0.1', base_host: 'aicte.example.invalid' }, verified: false,
  verification_note: 'No AICTE API, credential or specification has been supplied to this project. Real AICTE integration remains UNVERIFIED until an authorised connection has actually been tested.',
  contract: { version: 'sih-demo-0.1', label: 'DEMO ONLY — a contract defined by this project. It is NOT an AICTE specification, and no AICTE endpoint is known to implement it.' },
  counts: { FAILED: 1, SYNCED: 2 }, recent: [row(), row({ id: 'sync-2', status: 'SYNCED', external_ref: 'EXT-9', last_error: null, attempts: 1 })],
  log: [{ at: '2026-10-27T10:00:00Z', sync_id: 'sync-1', adapter: 'http', operation: 'submit', attempt_no: 1, outcome: 'timeout', http_status: null, latency_ms: 1000 }], limits: { max_attempts: 8, backoff_seconds: 60 }, ...over,
})

describe('integration status', () => {
  it('says plainly that real integration is unverified, and shows confirmed and failed records differently', async () => {
    mockApi({ 'GET /api/auth/session': { body: BUREAU }, ...UNREAD, 'GET /api/integration/status': { body: status() } })
    renderApp('/app/integration')
    expect(await screen.findByText('Real AICTE integration is UNVERIFIED')).toBeInTheDocument()
    expect(screen.getByText(/NOT an AICTE specification/)).toBeInTheDocument()
    const rows = within(await screen.findByRole('table')).getAllByRole('row')
    expect(within(rows[1]!).getByText('Failed')).toBeInTheDocument()
    expect(within(rows[2]!).getByText('Confirmed')).toBeInTheDocument() // "SYNCED" is shown as what it means: the receiver confirmed
    expect(screen.getByText(/The log holds no message bodies, documents or secrets/)).toBeInTheDocument()
  })

  it('lets the Bureau retry only a failed or reconciliation-required record, behind a confirmation', async () => {
    const api = mockApi({
      'GET /api/auth/session': { body: BUREAU }, ...UNREAD, 'GET /api/integration/status': { body: status() },
      'POST /api/integration/syncs/sync-1/retry': { body: row({ status: 'SYNC_PENDING', attempts: 1 }) },
    })
    renderApp('/app/integration')
    const buttons = await screen.findAllByRole('button', { name: 'Try again…' })
    expect(buttons).toHaveLength(1) // the confirmed record has none
    await userEvent.click(buttons[0]!)
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent(/same message id, so the receiver cannot end up with it twice/)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/integration/syncs/sync-1/retry')).toBe(true))
  })

  it('gives an auditor the read-only view and nobody else', async () => {
    mockApi({ 'GET /api/auth/session': { body: AUDITOR }, ...UNREAD, 'GET /api/integration/status': { body: status() } })
    renderApp('/app/integration')
    expect(await screen.findByText('Real AICTE integration is UNVERIFIED')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Try again…' })).not.toBeInTheDocument()
  })

  it('refuses a role without integration access', async () => {
    mockApi({ 'GET /api/auth/session': { body: EC }, ...UNREAD })
    renderApp('/app/integration')
    expect(await screen.findByText('You do not have access to this page')).toBeInTheDocument()
  })

  it('shows mode none honestly: nothing is sent', async () => {
    mockApi({ 'GET /api/auth/session': { body: BUREAU }, ...UNREAD, 'GET /api/integration/status': { body: status({ mode: 'none', mode_note: 'No integration is configured. Nothing is sent to any external system, and every record says NOT_CONFIGURED.', adapter: null, counts: {}, recent: [], log: [] }) } })
    renderApp('/app/integration')
    expect(await screen.findByText(/Nothing is sent to any external system/)).toBeInTheDocument()
    expect(screen.getByText('Nothing has been synchronised yet')).toBeInTheDocument()
    expect(screen.getByText('None: nothing is sent')).toBeInTheDocument()
  })
})
