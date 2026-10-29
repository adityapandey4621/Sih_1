import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { ApiError, setUnauthorizedHandler } from '../../lib/api'
import { authApi } from './authApi'
import type { Permission, SessionUser } from './types'

type AuthState =
  | { status: 'loading' }
  | { status: 'anonymous'; sessionExpired: boolean }
  | { status: 'authenticated'; user: SessionUser }
  | { status: 'error'; message: string }

interface AuthContextValue {
  state: AuthState
  signIn: (email: string, password: string) => Promise<SessionUser>
  signOut: () => Promise<void>
  retry: () => void
  can: (permission: Permission) => boolean
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    setState({ status: 'loading' })
    authApi
      .session()
      .then((user) => !cancelled && setState(user ? { status: 'authenticated', user } : { status: 'anonymous', sessionExpired: false }))
      .catch((e: unknown) => {
        if (cancelled) return
        setState({ status: 'error', message: e instanceof ApiError ? e.message : 'Unexpected error while checking your session.' })
      })
    return () => {
      cancelled = true
    }
  }, [attempt])

  // Any 401 from a protected call means the server ended the session (idle/absolute expiry, deactivation).
  useEffect(() => {
    setUnauthorizedHandler(() =>
      setState((s) => (s.status === 'authenticated' ? { status: 'anonymous', sessionExpired: true } : s)),
    )
    return () => setUnauthorizedHandler(null)
  }, [])

  const signIn = useCallback(async (email: string, password: string) => {
    const user = await authApi.login(email, password)
    setState({ status: 'authenticated', user })
    return user
  }, [])

  const signOut = useCallback(async () => {
    try {
      await authApi.logout()
    } finally {
      setState({ status: 'anonymous', sessionExpired: false })
    }
  }, [])

  const retry = useCallback(() => setAttempt((n) => n + 1), [])

  const value = useMemo<AuthContextValue>(
    () => ({
      state,
      signIn,
      signOut,
      retry,
      can: (p) => state.status === 'authenticated' && state.user.permissions.includes(p),
    }),
    [state, signIn, signOut, retry],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
