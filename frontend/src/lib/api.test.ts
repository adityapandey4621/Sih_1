import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, request } from './api'
import { mockApi } from '../test/helpers'

afterEach(() => {
  vi.unstubAllGlobals()
  document.cookie = 'sih_csrf=; max-age=0; path=/'
})

describe('request()', () => {
  it('fetches one CSRF token even when two first requests start together', async () => {
    document.cookie = 'sih_csrf=; max-age=0; path=/'
    let issued = 0
    const api = mockApi({
      'GET /api/auth/csrf': () => {
        issued += 1
        document.cookie = `sih_csrf=token-${issued}; path=/`
        return { body: { csrf_token: `token-${issued}` } }
      },
      'POST /api/x': { body: { ok: true } },
    })
    await Promise.all([request('/api/x', { method: 'POST', body: {} }), request('/api/x', { method: 'POST', body: {} })])
    expect(api.calls.filter((c) => c.key === 'GET /api/auth/csrf')).toHaveLength(1)
    const sent = api.fn.mock.calls.filter(([, init]) => init?.method === 'POST').map(([, init]) => (init?.headers as Record<string, string>)['X-CSRF-Token'])
    expect(sent).toEqual(['token-1', 'token-1'])
  })

  it('maps validation errors to fields and strips pydantic prefixes', async () => {
    document.cookie = 'sih_csrf=t; path=/'
    mockApi({ 'POST /api/x': { status: 422, body: { detail: [{ loc: ['body', 'password'], msg: 'Value error, Password must be at least 12 characters.' }] } } })
    const err = await request('/api/x', { method: 'POST', body: {} }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).fieldErrors).toEqual({ password: 'Password must be at least 12 characters.' })
  })

  it('reports retry-after for throttling', async () => {
    document.cookie = 'sih_csrf=t; path=/'
    mockApi({ 'POST /api/x': { status: 429, body: { detail: 'Too many' }, headers: { 'Retry-After': '900' } } })
    const err = (await request('/api/x', { method: 'POST', body: {} }).catch((e: unknown) => e)) as ApiError
    expect(err.status).toBe(429)
    expect(err.retryAfterSeconds).toBe(900)
  })

  it('sends FormData as multipart (no JSON content type) with the CSRF header', async () => {
    document.cookie = 'sih_csrf=t; path=/'
    const api = mockApi({ 'POST /api/up': { body: { ok: true } } })
    const body = new FormData()
    body.set('item_key', 'doc-main')
    body.set('file', new File(['x'], 'a.pdf'))
    await request('/api/up', { method: 'POST', body })
    const init = api.fn.mock.calls[0]![1] as RequestInit
    expect((init.headers as Record<string, string>)['Content-Type']).toBeUndefined()
    expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('t')
    expect(init.body).toBeInstanceOf(FormData)
  })

  it('exposes the stable error code and itemised issues from the backend', async () => {
    document.cookie = 'sih_csrf=t; path=/'
    mockApi({ 'POST /api/x': { status: 422, body: { detail: 'Blocked.', code: 'submission_blocked', issues: [{ code: 'missing', message: 'Main document is required.', path: 'doc-main' }] } } })
    const err = (await request('/api/x', { method: 'POST', body: {} }).catch((e: unknown) => e)) as ApiError
    expect(err.code).toBe('submission_blocked')
    expect(err.issues).toEqual([{ code: 'missing', message: 'Main document is required.', path: 'doc-main' }])
  })
})