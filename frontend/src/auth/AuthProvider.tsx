import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { apiJson, refreshSession, setAccessToken, setOnAuthFailure } from '@/api/client'
import type { AuthResponse, User } from '@/api/types'

type Status = 'loading' | 'authenticated' | 'anonymous'

type AuthContextValue = {
  user: User | null
  status: Status
  login(email: string, password: string): Promise<void>
  register(email: string, password: string): Promise<void>
  logout(): Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [status, setStatus] = useState<Status>('loading')

  const clearSession = useCallback(() => {
    setAccessToken(null)
    setUser(null)
    setStatus('anonymous')
  }, [])

  const applySession = useCallback((body: AuthResponse) => {
    setAccessToken(body.access_token)
    setUser(body.user)
    setStatus('authenticated')
  }, [])

  useEffect(() => {
    setOnAuthFailure(clearSession)
    let cancelled = false
    void refreshSession().then((body) => {
      if (cancelled) return
      if (body) applySession(body)
      else clearSession()
    })
    return () => {
      cancelled = true
    }
  }, [applySession, clearSession])

  const authenticate = useCallback(
    async (path: string, email: string, password: string) => {
      const body = await apiJson<AuthResponse>(path, {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      })
      applySession(body)
    },
    [applySession],
  )

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      status,
      login: (email, password) => authenticate('/api/auth/login', email, password),
      register: (email, password) => authenticate('/api/auth/register', email, password),
      logout: async () => {
        try {
          await apiJson<void>('/api/auth/logout', { method: 'POST' })
        } finally {
          clearSession()
        }
      },
    }),
    [user, status, authenticate, clearSession],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
