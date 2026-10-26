import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ANON, mockApi, renderApp } from '../../test/helpers'

afterEach(() => vi.unstubAllGlobals())

describe('access request form', () => {
  it('does not offer any role selection', async () => {
    mockApi({ 'GET /api/auth/session': ANON })
    renderApp('/request-access')
    await screen.findByRole('heading', { name: 'Request institution access' })
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/role/i)).not.toBeInTheDocument()
  })

  it('validates, submits, and confirms receipt (not approval)', async () => {
    const api = mockApi({
      'GET /api/auth/session': ANON,
      'POST /api/access-requests': { status: 201, body: { status: 'received', message: 'Your request has been received and is awaiting review by an administrator. No account has been created yet.' } },
    })
    renderApp('/request-access')
    await screen.findByRole('heading', { name: 'Request institution access' })

    await userEvent.click(screen.getByRole('button', { name: 'Submit request' }))
    expect(await screen.findByText('Enter the full name of the institution.')).toBeInTheDocument()
    expect(api.calls.some((c) => c.key === 'POST /api/access-requests')).toBe(false)

    await userEvent.type(screen.getByLabelText('Institution name'), 'Example College')
    await userEvent.type(screen.getByLabelText('Your full name'), 'A Person')
    await userEvent.type(screen.getByLabelText('Official email address'), 'a@college.example.org')
    await userEvent.type(screen.getByLabelText('Reason for requesting access'), 'To submit our institution applications.')
    await userEvent.click(screen.getByRole('button', { name: 'Submit request' }))

    expect(await screen.findByText('We have received your request')).toBeInTheDocument()
    expect(screen.getByText(/No account has been created yet/)).toBeInTheDocument()
    expect(screen.queryByText(/has been approved/i)).not.toBeInTheDocument()
    expect(api.calls.find((c) => c.key === 'POST /api/access-requests')?.body).toMatchObject({ institution_name: 'Example College' })
  })

  it('shows server-side field errors', async () => {
    mockApi({
      'GET /api/auth/session': ANON,
      'POST /api/access-requests': { status: 422, body: { detail: [{ loc: ['body', 'applicant_email'], msg: 'value is not a valid email address' }] } },
    })
    renderApp('/request-access')
    await screen.findByRole('heading', { name: 'Request institution access' })
    await userEvent.type(screen.getByLabelText('Institution name'), 'Example College')
    await userEvent.type(screen.getByLabelText('Your full name'), 'A Person')
    await userEvent.type(screen.getByLabelText('Official email address'), 'a@college.example.org')
    await userEvent.type(screen.getByLabelText('Reason for requesting access'), 'To submit our institution applications.')
    await userEvent.click(screen.getByRole('button', { name: 'Submit request' }))
    expect(await screen.findByText('value is not a valid email address')).toBeInTheDocument()
  })
})

describe('password recovery', () => {
  it('says plainly that email recovery is unavailable', async () => {
    mockApi({ 'GET /api/auth/session': ANON })
    renderApp('/forgot-password')
    expect(await screen.findByText(/Email-based recovery is not available/)).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })
})

describe('invitation acceptance', () => {
  it('rejects a missing token without calling the server', async () => {
    const api = mockApi({ 'GET /api/auth/session': ANON })
    renderApp('/accept-invitation')
    expect(await screen.findByText('This invitation link is incomplete.')).toBeInTheDocument()
    expect(api.calls.some((c) => c.key.includes('invitations'))).toBe(false)
  })
})
