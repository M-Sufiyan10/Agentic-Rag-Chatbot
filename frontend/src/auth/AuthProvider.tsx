import { useQueryClient } from '@tanstack/react-query'
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
  const queryClient = useQueryClient()

  /**
   * End the session locally. Runs on logout (whether or not the server call
   * succeeds) and when a token refresh fails. It also empties the query
   * cache, so the next user on this browser never sees the previous user's
   * conversations or messages.
   */
  const clearSession = useCallback(() => {
    setAccessToken(null)
    queryClient.clear()
    setUser(null)
    setStatus('anonymous')
  }, [queryClient])

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
        } catch {
          // The server call failing (network, 5xx) must not keep the user
          // signed in here: the local session is ended below regardless.
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
