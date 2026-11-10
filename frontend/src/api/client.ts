import type { AuthResponse } from './types'

export class ApiError extends Error {
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

let accessToken: string | null = null
let onAuthFailure: () => void = () => {}
let refreshInFlight: Promise<AuthResponse | null> | null = null

export function setAccessToken(token: string | null): void {
  accessToken = token
}

export function setOnAuthFailure(cb: () => void): void {
  onAuthFailure = cb
}

/**
 * Exchange the httpOnly refresh cookie for a new access token. All concurrent
 * callers share one in-flight request so the cookie is only rotated once.
 * Returns null when there is no valid session.
 */
export function refreshSession(): Promise<AuthResponse | null> {
  refreshInFlight ??= (async () => {
    try {
      const res = await fetch('/api/auth/refresh', {
        method: 'POST',
        credentials: 'include',
      })
      if (!res.ok) return null
      const body = (await res.json()) as AuthResponse
      accessToken = body.access_token
      return body
    } catch {
      return null
    } finally {
      refreshInFlight = null
    }
  })()
  return refreshInFlight
}

function send(path: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers)
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
  return fetch(path, { ...init, headers, credentials: 'include' })
}

/**
 * fetch wrapper that attaches the bearer token. On a 401 from a non-auth path
 * it refreshes the session once and retries; if that fails it calls the
 * onAuthFailure callback and returns the 401.
 */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const res = await send(path, init)
  if (res.status !== 401 || path.startsWith('/api/auth/')) return res

  if (await refreshSession()) return send(path, init)

  accessToken = null
  onAuthFailure()
  return res
}

export async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const res = await apiFetch(path, { ...init, headers })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = (await res.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
      else if (body.detail !== undefined) detail = JSON.stringify(body.detail)
    } catch {
      // non-JSON error body: keep statusText
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}
