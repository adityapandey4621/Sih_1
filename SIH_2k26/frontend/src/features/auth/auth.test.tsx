import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ANON, UNAUTH, makeUser, mockApi, renderApp } from '../../test/helpers'

afterEach(() => vi.unstubAllGlobals())

describe('protected routes', () => {
  it('redirects an anonymous visitor from /app to sign-in', async () => {
    mockApi({ 'GET /api/auth/session': ANON })
    const { router } = renderApp('/app/scrutiny')
    await screen.findByRole('heading', { name: 'Sign in' })
    expect(router.state.location.pathname).toBe('/sign-in')
  })

  it('shows a retryable error, not a redirect, when the server cannot be reached', async () => {
    mockApi({ 'GET /api/auth/session': 'network-error' })
    renderApp('/app')
    expect(await screen.findByText(/could not check your session/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument()
  })

  it('sends a signed-in user to their own dashboard from /app', async () => {
    mockApi({ 'GET /api/auth/session': { body: makeUser(['dashboard:scrutiny']) } })
    const { router } = renderApp('/app')
    await screen.findByRole('heading', { name: 'Scrutiny officer dashboard' })
    expect(router.state.location.pathname).toBe('/app/scrutiny')
  })

  it("denies a dashboard the user's permissions do not include", async () => {
    mockApi({ 'GET /api/auth/session': { body: makeUser(['dashboard:scrutiny']) } })
    renderApp('/app/executive')
    expect(await screen.findByText(/do not have access to this page/i)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Executive Committee dashboard/ })).not.toBeInTheDocument()
  })

  it('denies admin pages without the admin permission', async () => {
    mockApi({ 'GET /api/auth/session': { body: makeUser(['dashboard:audit', 'audit:read']) } })
    renderApp('/app/admin/users')
    expect(await screen.findByText(/do not have access to this page/i)).toBeInTheDocument()
  })

  it('builds navigation only from the permissions it was given', async () => {
    mockApi({ 'GET /api/auth/session': { body: makeUser(['dashboard:audit', 'audit:read']) } })
    renderApp('/app')
    await screen.findByRole('heading', { name: 'Audit and monitoring' })
    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(nav).toHaveTextContent('Audit overview')
    expect(nav).toHaveTextContent('Audit log')
    expect(nav).not.toHaveTextContent('Access requests')
    expect(nav).not.toHaveTextContent('Decision queue')
  })

  it('dashboards state honestly that no application data exists', async () => {
    mockApi({ 'GET /api/auth/session': { body: makeUser(['dashboard:institute'], { role: 'institute_user', role_label: 'Institution' }) } })
    renderApp('/app')
    await screen.findByRole('heading', { name: 'Institution dashboard' })
    expect(screen.getByText(/No application data yet/)).toBeInTheDocument()
    expect(screen.getByText(/nothing below is sample or live data/i)).toBeInTheDocument()
  })
})

describe('sign-in', () => {
  async function openSignIn(extra: Parameters<typeof mockApi>[0] = {}) {
    const api = mockApi({ 'GET /api/auth/session': ANON, ...extra })
    renderApp('/sign-in')
    await screen.findByRole('heading', { name: 'Sign in' })
    return api
  }

  it('validates required fields before calling the server', async () => {
    const api = await openSignIn()
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Enter your email address.')).toBeInTheDocument()
    expect(screen.getByText('Enter your password.')).toBeInTheDocument()
    expect(api.calls.some((c) => c.key === 'POST /api/auth/login')).toBe(false)
  })

  it('shows one generic message for invalid credentials and clears the password', async () => {
    await openSignIn({ 'POST /api/auth/login': { status: 401, body: { detail: 'Incorrect email or password.' } } })
    await userEvent.type(screen.getByLabelText('Email address'), 'a@b.example.org')
    await userEvent.type(screen.getByLabelText('Password'), 'wrongwrongwrong')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect email or password.')
    expect(screen.getByLabelText('Password')).toHaveValue('')
  })

  it('reports throttling and network failure distinctly', async () => {
    await openSignIn({ 'POST /api/auth/login': { status: 429, body: { detail: 'Too many sign-in attempts. Please wait a few minutes and try again.' } } })
    await userEvent.type(screen.getByLabelText('Email address'), 'a@b.example.org')
    await userEvent.type(screen.getByLabelText('Password'), 'x')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText(/Too many sign-in attempts/)).toBeInTheDocument()
  })

  it('reports a server/network failure', async () => {
    await openSignIn({ 'POST /api/auth/login': 'network-error' })
    await userEvent.type(screen.getByLabelText('Email address'), 'a@b.example.org')
    await userEvent.type(screen.getByLabelText('Password'), 'x')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText(/Could not reach the server/)).toBeInTheDocument()
  })

  it('disables the form while the request is in flight and then lands on the dashboard', async () => {
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    const user = makeUser(['dashboard:scrutiny'])
    await openSignIn({
      'POST /api/auth/login': async () => {
        await gate
        return { body: user }
      },
    })
    await userEvent.type(screen.getByLabelText('Email address'), 'person@test.example.org')
    await userEvent.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('button', { name: /Signing in/ })).toBeDisabled()
    release()
    await screen.findByRole('heading', { name: 'Scrutiny officer dashboard' })
  })

  it('toggles password visibility accessibly', async () => {
    await openSignIn()
    const pw = screen.getByLabelText('Password')
    expect(pw).toHaveAttribute('type', 'password')
    await userEvent.click(screen.getByRole('button', { name: 'Show password' }))
    expect(pw).toHaveAttribute('type', 'text')
    expect(screen.getByRole('button', { name: 'Hide password' })).toHaveAttribute('aria-pressed', 'true')
  })
})

describe('session end', () => {
  it('returns to sign-in with an explanation when the server ends the session mid-use', async () => {
    mockApi({
      'GET /api/auth/session': { body: makeUser(['dashboard:audit', 'audit:read']) },
      'GET /api/audit/events': UNAUTH,
    })
    renderApp('/app/audit-log')
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Your session has ended')).toBeInTheDocument())
  })
})
