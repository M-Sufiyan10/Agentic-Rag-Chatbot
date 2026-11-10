import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { Route, Routes } from 'react-router'
import { server } from '@/test/setup'
import { renderWithProviders } from '@/test/utils'
import LoginPage from './LoginPage'

const anonymousRefresh = http.post(
  '*/api/auth/refresh',
  () => HttpResponse.json({ detail: 'no session' }, { status: 401 }),
)

function renderLogin() {
  return renderWithProviders(
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<div>home page</div>} />
    </Routes>,
    '/login',
  )
}

async function submit() {
  await userEvent.type(await screen.findByLabelText(/email/i), 'a@b.co')
  await userEvent.type(screen.getByLabelText(/password/i), 'password123')
  await userEvent.click(screen.getByRole('button', { name: /sign in/i }))
}

describe('LoginPage', () => {
  it('shows server error on 401', async () => {
    server.use(
      anonymousRefresh,
      http.post('*/api/auth/login', () =>
        HttpResponse.json({ detail: 'Invalid email or password' }, { status: 401 }),
      ),
    )
    renderLogin()
    await submit()
    expect(await screen.findByText('Invalid email or password')).toBeVisible()
  })

  it('navigates to / on success', async () => {
    server.use(
      anonymousRefresh,
      http.post('*/api/auth/login', () =>
        HttpResponse.json({
          access_token: 't',
          token_type: 'bearer',
          user: { id: 'u1', email: 'a@b.co' },
        }),
      ),
    )
    renderLogin()
    await submit()
    await waitFor(() => expect(screen.getByText('home page')).toBeVisible())
  })
})
