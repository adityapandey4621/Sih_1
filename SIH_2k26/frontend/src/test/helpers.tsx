import { render } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { vi } from 'vitest'
import { routes } from '../app/router'
import type { SessionUser } from '../features/auth/types'

export const makeUser = (permissions: string[], overrides: Partial<SessionUser> = {}): SessionUser => ({
  id: 'u1',
  email: 'person@test.example.org',
  full_name: 'Test Person',
  role: 'scrutiny_officer',
  role_label: 'Scrutiny officer',
  institution: null,
  permissions,
  session_expires_at: '2099-01-01T00:00:00Z',
  ...overrides,
})

export interface MockReply {
  status?: number
  body?: unknown
  headers?: Record<string, string>
}
type Handler = MockReply | (() => MockReply | Promise<MockReply>) | 'network-error'

/** Stubs fetch with replies keyed by "METHOD /path". Unlisted calls fail loudly. */
export function mockApi(handlers: Record<string, Handler>) {
  const calls: { key: string; body: unknown }[] = []
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).split('?')[0]
    const key = `${init?.method ?? 'GET'} ${path}`
    const raw = init?.body
    const body = raw instanceof FormData ? Object.fromEntries([...raw.entries()].map(([k, v]) => [k, v instanceof File ? { filename: v.name, size: v.size } : v])) : raw ? JSON.parse(String(raw)) : undefined
    calls.push({ key, body })
    const h = handlers[key]
    if (h === undefined) throw new Error(`Unexpected request: ${key}`)
    if (h === 'network-error') throw new TypeError('Failed to fetch')
    const r = typeof h === 'function' ? await h() : h
    const status = r.status ?? 200
    return new Response(status === 204 ? null : JSON.stringify(r.body === undefined ? {} : r.body), {
      status,
      headers: { 'Content-Type': 'application/json', ...r.headers },
    })
  })
  vi.stubGlobal('fetch', fn)
  return { calls, fn }
}

export function renderApp(path: string) {
  document.cookie = 'sih_csrf=test-csrf-token; path=/'
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  const utils = render(<RouterProvider router={router} />)
  return { router, ...utils }
}

/** Session probe answer for a signed-out visitor. */
export const ANON: MockReply = { body: null }
/** A protected call whose session the server has ended. */
export const UNAUTH: MockReply = { status: 401, body: { detail: 'Not signed in or the session has expired.' } }
