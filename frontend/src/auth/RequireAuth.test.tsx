import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { Route, Routes } from 'react-router'
import { server } from '@/test/setup'
import { renderWithProviders } from '@/test/utils'
import RequireAuth from './RequireAuth'

function renderProtected() {
  return renderWithProviders(
    <Routes>
      <Route path="/login" element={<div>login page</div>} />
      <Route element={<RequireAuth />}>
        <Route path="/" element={<div>secret</div>} />
      </Route>
    </Routes>,
    '/',
  )
}

describe('RequireAuth', () => {
  it('redirects anonymous users to /login', async () => {
    server.use(
      http.post('*/api/auth/refresh', () => new HttpResponse(null, { status: 401 })),
    )
    renderProtected()
    expect(await screen.findByText('login page')).toBeVisible()
    expect(screen.queryByText('secret')).not.toBeInTheDocument()
  })

  it('renders children for an authenticated session', async () => {
    server.use(
      http.post('*/api/auth/refresh', () =>
        HttpResponse.json({
          access_token: 't',
          token_type: 'bearer',
          user: { id: 'u1', email: 'a@b.co' },
        }),
      ),
    )
    renderProtected()
    expect(await screen.findByText('secret')).toBeVisible()
  })
})
