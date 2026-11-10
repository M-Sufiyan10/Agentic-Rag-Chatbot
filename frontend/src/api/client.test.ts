import { http, HttpResponse } from 'msw'
import { server } from '@/test/setup'
import { apiFetch, apiJson, ApiError, setAccessToken, setOnAuthFailure } from './client'

const authBody = (token: string) => ({
  access_token: token,
  token_type: 'bearer',
  user: { id: 'u1', email: 'a@b.co' },
})

beforeEach(() => {
  setAccessToken('old')
  setOnAuthFailure(() => {})
})

describe('apiFetch', () => {
  it('retries once after a successful refresh', async () => {
    const seen: (string | null)[] = []
    server.use(
      http.get('*/api/conversations', ({ request }) => {
        seen.push(request.headers.get('Authorization'))
        return seen.length === 1
          ? new HttpResponse(null, { status: 401 })
          : HttpResponse.json([])
      }),
      http.post('*/api/auth/refresh', () => HttpResponse.json(authBody('new'))),
    )
    const res = await apiFetch('/api/conversations')
    expect(res.status).toBe(200)
    expect(seen).toEqual(['Bearer old', 'Bearer new'])
  })

  it('calls onAuthFailure when refresh fails', async () => {
    const onFail = vi.fn()
    setOnAuthFailure(onFail)
    server.use(
      http.get('*/api/conversations', () => new HttpResponse(null, { status: 401 })),
      http.post('*/api/auth/refresh', () => new HttpResponse(null, { status: 401 })),
    )
    const res = await apiFetch('/api/conversations')
    expect(res.status).toBe(401)
    expect(onFail).toHaveBeenCalledTimes(1)
  })

  it('concurrent 401s trigger a single refresh call', async () => {
    let refreshCalls = 0
    server.use(
      http.get('*/api/conversations', ({ request }) =>
        request.headers.get('Authorization') === 'Bearer new'
          ? HttpResponse.json([])
          : new HttpResponse(null, { status: 401 }),
      ),
      http.post('*/api/auth/refresh', async () => {
        refreshCalls++
        await new Promise((r) => setTimeout(r, 30))
        return HttpResponse.json(authBody('new'))
      }),
    )
    const [a, b] = await Promise.all([
      apiFetch('/api/conversations'),
      apiFetch('/api/conversations'),
    ])
    expect([a.status, b.status]).toEqual([200, 200])
    expect(refreshCalls).toBe(1)
  })

  it('does not refresh on a 401 from an auth path', async () => {
    const onFail = vi.fn()
    setOnAuthFailure(onFail)
    server.use(
      http.post('*/api/auth/login', () =>
        HttpResponse.json({ detail: 'Invalid email or password' }, { status: 401 }),
      ),
    )
    const res = await apiFetch('/api/auth/login', { method: 'POST' })
    expect(res.status).toBe(401)
    expect(onFail).not.toHaveBeenCalled()
  })
})

describe('apiJson', () => {
  it('throws ApiError with status and detail on non-2xx', async () => {
    server.use(
      http.get('*/api/x', () => HttpResponse.json({ detail: 'nope' }, { status: 404 })),
    )
    await expect(apiJson('/api/x')).rejects.toMatchObject({
      name: 'ApiError',
      status: 404,
      detail: 'nope',
    })
    await expect(apiJson('/api/x')).rejects.toBeInstanceOf(ApiError)
  })
})
