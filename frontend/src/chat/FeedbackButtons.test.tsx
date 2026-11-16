import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/setup'
import { useMessages } from './api'
import FeedbackButtons from './FeedbackButtons'

/** Feeds the saved vote from the transcript query, as ChatPane does. */
function Harness() {
  const messages = useMessages('c1')
  const m = messages.data?.[0]
  if (!m?.id) return null
  return <FeedbackButtons conversationId="c1" turnId={m.id} value={m.feedback ?? null} />
}

function setup(feedback: 'up' | 'down' | null) {
  server.use(
    http.get('*/api/conversations/c1/messages', () =>
      HttpResponse.json([{ role: 'assistant', content: 'Hi', id: 't1', feedback }]),
    ),
  )
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <Harness />
    </QueryClientProvider>,
  )
}

describe('FeedbackButtons', () => {
  it('renders two buttons with aria-pressed matching the value', async () => {
    setup('down')
    expect(await screen.findByRole('button', { name: 'Bad response' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByRole('button', { name: 'Good response' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('PUTs the vote when none is set', async () => {
    let body: unknown
    server.use(
      http.put('*/api/conversations/c1/messages/t1/feedback', async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({ value: 'up' })
      }),
    )
    setup(null)
    await userEvent.click(await screen.findByRole('button', { name: 'Good response' }))
    await waitFor(() => expect(body).toEqual({ value: 'up' }))
    expect(screen.getByRole('button', { name: 'Good response' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('DELETEs when clicking the active vote', async () => {
    let deleted = false
    server.use(
      http.delete('*/api/conversations/c1/messages/t1/feedback', () => {
        deleted = true
        return new HttpResponse(null, { status: 204 })
      }),
    )
    setup('up')
    await userEvent.click(await screen.findByRole('button', { name: 'Good response' }))
    await waitFor(() => expect(deleted).toBe(true))
    expect(screen.getByRole('button', { name: 'Good response' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('rolls back and shows an error on failure', async () => {
    server.use(
      http.put('*/api/conversations/c1/messages/t1/feedback', () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    )
    setup(null)
    await userEvent.click(await screen.findByRole('button', { name: 'Good response' }))
    expect(await screen.findByText("Couldn't save feedback")).toBeVisible()
    expect(screen.getByRole('button', { name: 'Good response' })).toHaveAttribute('aria-pressed', 'false')
  })
})
