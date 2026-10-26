/**
 * The only module that talks to the backend over HTTP.
 * Same-origin cookies carry the session; nothing sensitive is kept in JS-accessible storage.
 */

const CSRF_COOKIE = 'sih_csrf'

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly fieldErrors: Record<string, string> = {},
    public readonly retryAfterSeconds?: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
  get isNetwork() {
    return this.status === 0
  }
}

type UnauthorizedHandler = () => void
let onUnauthorized: UnauthorizedHandler | null = null
export function setUnauthorizedHandler(fn: UnauthorizedHandler | null) {
  onUnauthorized = fn
}

function readCookie(name: string): string | null {
  for (const part of document.cookie.split('; ')) {
    const [k, ...v] = part.split('=')
    if (k === name) return decodeURIComponent(v.join('='))
  }
  return null
}

// Concurrent first requests must share one token fetch: each /csrf call would otherwise
// issue a different token and the last Set-Cookie would invalidate the others' headers.
let csrfInFlight: Promise<string> | null = null

async function fetchCsrfToken(): Promise<string> {
  const res = await fetch('/api/auth/csrf', { credentials: 'same-origin' })
  if (!res.ok) throw new ApiError(res.status, 'Could not start a secure session with the server.')
  return ((await res.json()) as { csrf_token: string }).csrf_token
}

async function csrfToken(): Promise<string> {
  const existing = readCookie(CSRF_COOKIE)
  if (existing) return existing
  csrfInFlight ??= fetchCsrfToken().finally(() => {
    csrfInFlight = null
  })
  return csrfInFlight
}

interface ValidationIssue {
  loc?: (string | number)[]
  msg?: string
}

function parseError(status: number, payload: unknown, retryAfter: string | null): ApiError {
  const detail = (payload as { detail?: unknown } | null)?.detail
  const retry = retryAfter ? Number.parseInt(retryAfter, 10) : undefined
  if (Array.isArray(detail)) {
    const fields: Record<string, string> = {}
    for (const issue of detail as ValidationIssue[]) {
      const key = String(issue.loc?.[issue.loc.length - 1] ?? 'form')
      fields[key] ??= (issue.msg ?? 'Invalid value').replace(/^Value error, /, '')
    }
    return new ApiError(status, 'Please correct the highlighted fields.', fields)
  }
  if (typeof detail === 'string') return new ApiError(status, detail, {}, retry)
  return new ApiError(status, 'The server returned an unexpected response.', {}, retry)
}

export interface RequestOptions {
  method?: 'GET' | 'POST'
  body?: unknown
  /** Do not treat a 401 as "session expired" (used by sign-in and the initial session probe). */
  expectUnauthorized?: boolean
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = opts.method ?? 'GET'
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (method !== 'GET') {
    headers['Content-Type'] = 'application/json'
    headers['X-CSRF-Token'] = await csrfToken()
  }

  let res: Response
  try {
    res = await fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    })
  } catch {
    throw new ApiError(0, 'Could not reach the server. Check your connection and try again.')
  }

  if (res.status === 204) return undefined as T
  let payload: unknown = null
  try {
    payload = await res.json()
  } catch {
    /* non-JSON error bodies (e.g. a proxy 502) are handled below */
  }
  if (!res.ok) {
    if (res.status === 401 && !opts.expectUnauthorized) onUnauthorized?.()
    if (res.status >= 500 && !payload) throw new ApiError(res.status, 'The server is temporarily unavailable. Please try again shortly.')
    throw parseError(res.status, payload, res.headers.get('Retry-After'))
  }
  return payload as T
}
