import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { detail, docState, evidenceOf, fieldOf, findingOf, itemEvidence, versionOf } from '../../test/fixtures'
import { makeUser, mockApi, renderApp } from '../../test/helpers'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

const INSTITUTE = makeUser(['dashboard:institute', 'application:create', 'application:read_own', 'application:update_own', 'application:submit', 'document:retry'], { role: 'institute_user', role_label: 'Institution' })
const ERR = (status: number, code: string, message: string) => ({ status, body: { detail: message, code, issues: [] } })

const docRecord = (v: ReturnType<typeof versionOf>) => ({
  id: v.id, item_key: v.item_key, original_filename: v.original_filename, media_type: v.media_type, size_bytes: v.size_bytes, sha256: v.sha256, uploaded_at: v.uploaded_at,
  uploaded_by: v.uploaded_by, removed: false, version_number: v.version_number, is_current: v.is_current, verification_status: 'NOT_VERIFIED', state: v.state,
})

function openDocuments(versions: ReturnType<typeof versionOf>[], evidence = evidenceOf(), extra: Parameters<typeof mockApi>[0] = {}) {
  const api = mockApi({
    'GET /api/auth/session': { body: INSTITUTE },
    'GET /api/applications/app-1': { body: detail({ documents: versions.map(docRecord) }) },
    'GET /api/applications/app-1/evidence': { body: evidence },
    ...extra,
  })
  const r = renderApp('/app/applications/app-1')
  return { api, ...r }
}
const toDocuments = async () => userEvent.click(await screen.findByRole('button', { name: /^2\s*Documents/ }))

describe('every document state is shown in the server\'s plain words', () => {
  const cases: [string, ReturnType<typeof docState>][] = [
    ['File received', docState({ code: 'received', label: 'File received', tone: 'info', terminal: false })],
    ['Extraction queued', docState({ code: 'extraction_queued', label: 'Extraction queued', tone: 'info', terminal: false })],
    ['Technical checks in progress', docState({ code: 'extraction_running', label: 'Technical checks in progress', tone: 'info', terminal: false })],
    ['Extraction complete', docState({})],
    ['Human review recommended', docState({ code: 'review_recommended', label: 'Human review recommended', tone: 'warning', review_recommended: true })],
    ['Processing failed', docState({ code: 'processing_failed', label: 'Processing failed', tone: 'error', can_retry: true, explanation: 'Text recognition took too long on this document. Retry is available.' })],
    ['File could not be read', docState({ code: 'technical_failed', label: 'File could not be read', tone: 'error', explanation: 'The PDF is damaged. Upload a corrected file.' })],
    ['Held before processing', docState({ code: 'held', label: 'Held before processing', tone: 'warning', can_retry: true })],
    ['File quarantined', docState({ code: 'quarantined', label: 'File quarantined', tone: 'error' })],
  ]
  it.each(cases)('%s', async (label, state) => {
    openDocuments([versionOf({ state })], evidenceOf({ items: [itemEvidence({ slot_state: state, current: versionOf({ state }), versions: [versionOf({ state })] })] }))
    await toDocuments()
    expect((await screen.findAllByText(label)).length).toBeGreaterThan(0)
    expect(screen.getAllByText('Contents not verified').length).toBeGreaterThan(0) // a technical state is never presented as acceptance
    expect(screen.queryByText(/^verified$/i)).not.toBeInTheDocument()
  })

  it('distinguishes no file submitted from a file that could not be read', async () => {
    openDocuments([], evidenceOf({ items: [itemEvidence({ slot_state: null, current: null, versions: [], fields: [], pages: [] })] }))
    await toDocuments()
    expect(await screen.findByText('No file submitted')).toBeInTheDocument()
    expect(screen.getByText('No file uploaded yet.')).toBeInTheDocument()
    expect(screen.queryByText('File could not be read')).not.toBeInTheDocument()
  })

  it('offers retry only where the server says retrying can help, and sends the request', async () => {
    const failed = docState({ code: 'processing_failed', label: 'Processing failed', tone: 'error', can_retry: true })
    const { api } = openDocuments([versionOf({ state: failed })], evidenceOf(), {
      'POST /api/documents/d1/retry': { status: 202, body: { document_id: 'd1', version_number: 1, state: docState({ code: 'extraction_queued', label: 'Extraction queued', tone: 'info', terminal: false }), pages: [], fields: [] } },
    })
    await toDocuments()
    await userEvent.click(await screen.findByRole('button', { name: /Retry processing/ }))
    await waitFor(() => expect(api.calls.some((c) => c.key === 'POST /api/documents/d1/retry')).toBe(true))
  })

  it('shows no retry for a file that retrying cannot fix', async () => {
    openDocuments([versionOf({ state: docState({ code: 'technical_failed', label: 'File could not be read', tone: 'error', can_retry: false }) })])
    await toDocuments()
    await screen.findByText('File could not be read')
    expect(screen.queryByRole('button', { name: /Retry processing/ })).not.toBeInTheDocument()
  })
})

describe('uploads', () => {
  it('shows upload in progress, then the new state', async () => {
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    const uploaded = detail({ documents: [docRecord(versionOf({ state: docState({ code: 'received', label: 'File received', tone: 'info', terminal: false }) }))] })
    openDocuments([], evidenceOf(), { 'POST /api/applications/app-1/documents': async () => { await gate; return { status: 201, body: uploaded } } })
    await toDocuments()
    await userEvent.upload(await screen.findByLabelText('Choose a file for Main document'), new File(['%PDF-1.4'], 'main.pdf', { type: 'application/pdf' }), { applyAccept: false })
    expect(await screen.findByText('Upload in progress…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Choose file/ })).toBeDisabled()
    release()
    expect(await screen.findByText('File received')).toBeInTheDocument()
    expect(screen.queryByText('Upload in progress…')).not.toBeInTheDocument()
  })

  it('shows an upload failure with the server\'s reason and keeps the page usable', async () => {
    openDocuments([], evidenceOf(), { 'POST /api/applications/app-1/documents': ERR(413, 'file_too_large', 'The file is larger than the 10 MB limit.') })
    await toDocuments()
    await userEvent.upload(await screen.findByLabelText('Choose a file for Main document'), new File(['x'], 'big.pdf', { type: 'application/pdf' }), { applyAccept: false })
    expect(await screen.findByRole('alert')).toHaveTextContent('larger than the 10 MB limit')
    expect(screen.getByRole('button', { name: /Choose file/ })).toBeEnabled()
  })

  it('says plainly when the same file was uploaded again and nothing changed', async () => {
    const same = detail({ documents: [docRecord(versionOf())] })
    openDocuments([versionOf()], evidenceOf(), { 'POST /api/applications/app-1/documents': { status: 200, body: same } })
    await toDocuments()
    await userEvent.upload(await screen.findByLabelText('Choose a file for Main document'), new File(['same'], 'copy.pdf', { type: 'application/pdf' }), { applyAccept: false })
    expect(await screen.findByText(/same file as the current version, so nothing was changed/)).toBeInTheDocument()
  })

  it('keeps earlier versions visible and downloadable, and offers a replacement', async () => {
    const v1 = versionOf({ id: 'd1', version_number: 1, is_current: false, state: docState({ code: 'superseded', label: 'Replaced by a newer version', tone: 'neutral' }) })
    const v2 = versionOf({ id: 'd2', version_number: 2, original_filename: 'revised.pdf', previous_version_id: 'd1' })
    openDocuments([v1, v2])
    await toDocuments()
    expect(await screen.findByText('revised.pdf')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Upload a replacement/ })).toBeInTheDocument()
    const summary = screen.getByText(/1 earlier version kept on record/)
    await userEvent.click(summary)
    const link = within(summary.closest('details')!).getByRole('link', { name: 'Download' })
    expect(link).toHaveAttribute('href', '/api/documents/d1/download')
  })
})

describe('document processing panel (institution view)', () => {
  it('frames the results as technical checks and shows values and preliminary issues in plain language', async () => {
    const item = itemEvidence({ findings: [findingOf()], fields: [fieldOf(), fieldOf({ field_key: 'document_date', label: 'Document date', status: 'NOT_FOUND', value: null, normalised_value: null, page_number: null, method: null, evidence: null, note: 'No value was found after the expected label(s).' })] })
    openDocuments([versionOf()], evidenceOf({ items: [item] }))
    await toDocuments()
    expect(await screen.findByText(/technical check only/)).toBeInTheDocument()
    expect(screen.getByText(/never changes the status of your application/)).toBeInTheDocument()
    expect(screen.getByText('Unable to extract')).toBeInTheDocument()
    expect(screen.getByText('Test Institute', { selector: 'strong' })).toBeInTheDocument()
    const finding = screen.getByText('Possible inconsistency between values').closest('li')!
    expect(within(finding).getByText('Potential issue — awaiting scrutiny review')).toBeInTheDocument()
    expect(within(finding).getByText(/A reviewer will look at it/)).toBeInTheDocument()
    expect(within(finding).getByText(/not a deficiency notice/)).toBeInTheDocument()
    expect(within(finding).queryByText(/review priority/i)).not.toBeInTheDocument() // internal priority is not shown to institutions
    expect(screen.queryByText(/deficiency notice issued|deficient/i)).not.toBeInTheDocument()
  })

  it('says text recognition is unavailable when it is, instead of hiding it', async () => {
    openDocuments([versionOf()], evidenceOf({ ocr_available: false }))
    await toDocuments()
    expect(await screen.findByText(/text recognition is not available/)).toBeInTheDocument()
  })

  it('refreshes while processing is pending and stops when it finishes', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    const pending = docState({ code: 'extraction_queued', label: 'Extraction queued', tone: 'info', terminal: false })
    let calls = 0
    const { api } = openDocuments([versionOf({ state: pending })], evidenceOf({ items: [itemEvidence({ slot_state: pending, current: versionOf({ state: pending }) })] }), {
      'GET /api/applications/app-1': () => ({ body: detail({ documents: [docRecord(versionOf({ state: ++calls > 1 ? docState() : pending }))] }) }),
    })
    await userEvent.click(await screen.findByRole('button', { name: /^2\s*Documents/ })) // the processing panel lives on this step
    await screen.findByText('Document processing')
    const count = () => api.calls.filter((c) => c.key === 'GET /api/applications/app-1').length
    const before = count()
    const evidenceCalls = () => api.calls.filter((c) => c.key === 'GET /api/applications/app-1/evidence').length
    const evidenceBefore = evidenceCalls()
    await act(async () => { await vi.advanceTimersByTimeAsync(3100) })
    expect(count()).toBeGreaterThan(before)
    // the state flipped to complete: the processing panel must refetch at once (not wait for its own timer), so the two never disagree
    await act(async () => { await vi.advanceTimersByTimeAsync(50) })
    expect(evidenceCalls()).toBeGreaterThan(evidenceBefore)
    await act(async () => { await vi.advanceTimersByTimeAsync(3100) })
    const settled = count()
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(count()).toBe(settled) // terminal state reached: polling stops
  })
})

