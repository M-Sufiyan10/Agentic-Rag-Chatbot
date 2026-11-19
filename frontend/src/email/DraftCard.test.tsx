import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/setup'
import DraftCard from './DraftCard'
import type { EmailDraft } from './api'

const draft: EmailDraft = {
  id: 'd1',
  turn_id: 't1',
  to_address: 'bob@example.com',
  subject: 'Lunch',
  body: 'Hi Bob,\nLunch tomorrow?',
  reply_to_uid: null,
  status: 'pending',
  error: null,
  created_at: '2026-11-19T10:00:00Z',
  sent_at: null,
}

function renderCard(overrides: Partial<EmailDraft> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <DraftCard conversationId="c1" draft={{ ...draft, ...overrides }} />
    </QueryClientProvider>,
  )
}

describe('DraftCard', () => {
  it('pending draft shows send and cancel', () => {
    renderCard()
    expect(screen.getByText('Draft email')).toBeVisible()
    expect(screen.getByText('bob@example.com')).toBeVisible()
    expect(screen.getByText('Lunch')).toBeVisible()
    expect(screen.getByText(/Lunch tomorrow\?/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled()
  })

  it('clicking send calls the send endpoint once and disables the button', async () => {
    let calls = 0
    server.use(
      http.post('*/api/email/drafts/d1/send', async () => {
        calls += 1
        await new Promise((resolve) => setTimeout(resolve, 100))
        return HttpResponse.json({ ...draft, status: 'sent' })
      }),
      http.get('*/api/conversations/c1/email-drafts', () => HttpResponse.json([])),
    )
    renderCard()
    const send = screen.getByRole('button', { name: 'Send' })
    await userEvent.click(send)
    await waitFor(() => expect(send).toBeDisabled())
    await userEvent.click(send)
    await waitFor(() => expect(calls).toBe(1))
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
  })

  it('sent draft shows sent and no buttons', () => {
    renderCard({ status: 'sent', sent_at: '2026-11-19T10:05:00Z' })
    expect(screen.getByText(/Sent ✓/)).toBeVisible()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('failed draft shows the reason', () => {
    renderCard({ status: 'failed', error: 'SMTP refused' })
    expect(screen.getByText('Failed: SMTP refused')).toBeVisible()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('429 detail is shown', async () => {
    server.use(
      http.post('*/api/email/drafts/d1/send', () =>
        HttpResponse.json({ detail: 'Send limit reached: 20 per hour' }, { status: 429 }),
      ),
      http.get('*/api/conversations/c1/email-drafts', () => HttpResponse.json([])),
    )
    renderCard()
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText('Send limit reached: 20 per hour')).toBeVisible()
  })
})
