import { QueryClient } from '@tanstack/react-query'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { apiFetch } from '@/api/client'
import { server } from '@/test/setup'
import { renderWithProviders } from '@/test/utils'
import { useAuth } from './AuthProvider'

const session = {
  access_token: 't',
  token_type: 'bearer',
  user: { id: 'u1', email: 'a@b.co' },
}

function Probe() {
  const { status, logout } = useAuth()
  return (
    <div>
      <p>status: {status}</p>
      <button onClick={() => void logout()}>log out</button>
      <button onClick={() => void apiFetch('/api/conversations')}>load</button>
    </div>
  )
}

function renderWithCachedChats() {
  const queryClient = new QueryClient()
  queryClient.setQueryData(['conversations'], [{ id: 'c1', title: 'Secret plans' }])
  renderWithProviders(<Probe />, '/', queryClient)
  return queryClient
}

describe('AuthProvider session end', () => {
  it('logout clears cached data even when /api/auth/logout fails', async () => {
    server.use(
      http.post('*/api/auth/refresh', () => HttpResponse.json(session)),
      http.post('*/api/auth/logout', () => HttpResponse.json({ detail: 'boom' }, { status: 500 })),
    )
    const queryClient = renderWithCachedChats()
    expect(await screen.findByText('status: authenticated')).toBeVisible()

    await userEvent.click(screen.getByRole('button', { name: 'log out' }))

    expect(await screen.findByText('status: anonymous')).toBeVisible()
    expect(queryClient.getQueryData(['conversations'])).toBeUndefined()
  })

  it('clears cached data when a refresh fails (auth failure)', async () => {
    let refreshes = 0
    server.use(
      http.post('*/api/auth/refresh', () => {
        refreshes += 1
        return refreshes === 1
          ? HttpResponse.json(session)
          : HttpResponse.json({ detail: 'expired' }, { status: 401 })
      }),
      http.get('*/api/conversations', () =>
        HttpResponse.json({ detail: 'expired' }, { status: 401 }),
      ),
    )
    const queryClient = renderWithCachedChats()
    expect(await screen.findByText('status: authenticated')).toBeVisible()

    await userEvent.click(screen.getByRole('button', { name: 'load' }))

    expect(await screen.findByText('status: anonymous')).toBeVisible()
    await waitFor(() => expect(queryClient.getQueryData(['conversations'])).toBeUndefined())
  })
})
