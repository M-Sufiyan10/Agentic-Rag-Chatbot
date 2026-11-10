import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/setup'
import ChatPane from './ChatPane'

type Frame = { event: string; data: unknown }

/** An SSE response that emits each frame after a short pause, like a real model. */
function sseResponse(frames: Frame[], pauseMs = 30): HttpResponse<ReadableStream<Uint8Array>> {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      for (const { event, data } of frames) {
        await new Promise((resolve) => setTimeout(resolve, pauseMs))
        controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`))
      }
      controller.close()
    },
  })
  return new HttpResponse(body, { headers: { 'content-type': 'text/event-stream' } })
}

function renderPane() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <ChatPane conversationId="c1" />
    </QueryClientProvider>,
  )
}

async function typeAndSend(text: string) {
  const input = await screen.findByRole('textbox', { name: /message/i })
  await userEvent.type(input, `${text}{Enter}`)
}

describe('ChatPane', () => {
  it('shows Thinking then streamed tokens then final message', async () => {
    let history: { role: string; content: string }[] = []
    server.use(
      http.get('*/api/conversations/c1/messages', () => HttpResponse.json(history)),
      http.post('*/api/conversations/c1/messages', () => {
        history = [
          { role: 'user', content: 'Hi there' },
          { role: 'assistant', content: 'Hello' },
        ]
        return sseResponse([
          { event: 'token', data: { text: 'Hel' } },
          { event: 'token', data: { text: 'lo' } },
          { event: 'done', data: { content: 'Hello' } },
        ])
      }),
    )
    renderPane()
    await typeAndSend('Hi there')

    expect(await screen.findByText('Thinking…')).toBeVisible()
    expect(screen.getByText('Hi there')).toBeVisible()
    expect(await screen.findByText('Hello')).toBeVisible()
    await waitFor(() => expect(screen.queryByText('Thinking…')).not.toBeInTheDocument())
    await waitFor(() => expect(screen.getAllByText('Hello')).toHaveLength(1))
    expect(screen.getAllByText('Hi there')).toHaveLength(1)
  })

  it('shows error event with Retry', async () => {
    let posts = 0
    server.use(
      http.get('*/api/conversations/c1/messages', () => HttpResponse.json([])),
      http.post('*/api/conversations/c1/messages', () => {
        posts += 1
        return sseResponse([
          {
            event: 'error',
            data: { message: "The assistant couldn't respond. Please try again." },
          },
        ])
      }),
    )
    renderPane()
    await typeAndSend('Hi there')

    expect(
      await screen.findByText("The assistant couldn't respond. Please try again."),
    ).toBeVisible()
    const retry = screen.getByRole('button', { name: /retry/i })
    expect(retry).toBeVisible()

    await userEvent.click(retry)
    await waitFor(() => expect(posts).toBe(2))
    expect(screen.getAllByText('Hi there')).toHaveLength(1)
  })

  it('shows the 409 conflict message', async () => {
    server.use(
      http.get('*/api/conversations/c1/messages', () => HttpResponse.json([])),
      http.post('*/api/conversations/c1/messages', () =>
        HttpResponse.json({ detail: 'A reply is already in progress' }, { status: 409 }),
      ),
    )
    renderPane()
    await typeAndSend('Hi there')
    expect(await screen.findByText('A reply is already in progress')).toBeVisible()
  })

  it('disables send while streaming', async () => {
    server.use(
      http.get('*/api/conversations/c1/messages', () => HttpResponse.json([])),
      http.post('*/api/conversations/c1/messages', () =>
        sseResponse(
          [
            { event: 'token', data: { text: 'Hel' } },
            { event: 'done', data: { content: 'Hel' } },
          ],
          150,
        ),
      ),
    )
    renderPane()
    const send = await screen.findByRole('button', { name: /send message/i })
    expect(send).toBeDisabled() // blank input

    await typeAndSend('Hi there')
    await userEvent.type(screen.getByRole('textbox', { name: /message/i }), 'next question')
    expect(send).toBeDisabled() // waiting
    expect(await screen.findByText('Hel')).toBeVisible()
    expect(send).toBeDisabled() // streaming

    await waitFor(() => expect(send).toBeEnabled())
  })

  it('inserts a newline on Shift+Enter instead of sending', async () => {
    let posts = 0
    server.use(
      http.get('*/api/conversations/c1/messages', () => HttpResponse.json([])),
      http.post('*/api/conversations/c1/messages', () => {
        posts += 1
        return sseResponse([{ event: 'done', data: { content: '' } }])
      }),
    )
    renderPane()
    const input = await screen.findByRole('textbox', { name: /message/i })
    await userEvent.type(input, 'line one{Shift>}{Enter}{/Shift}line two')
    expect(input).toHaveValue('line one\nline two')
    expect(posts).toBe(0)
  })

  it('sends initialMessage once and keeps it visible while the transcript refetches', async () => {
    let posts = 0
    server.use(
      // The server hasn't stored the message yet, so every refetch returns [].
      http.get('*/api/conversations/c1/messages', () => HttpResponse.json([])),
      http.post('*/api/conversations/c1/messages', () => {
        posts += 1
        return sseResponse([{ event: 'token', data: { text: 'Hel' } }], 200)
      }),
    )
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <StrictMode>
        <QueryClientProvider client={queryClient}>
          <ChatPane conversationId="c1" initialMessage="Hi there" />
        </QueryClientProvider>
      </StrictMode>,
    )
    expect(await screen.findByText('Thinking…')).toBeVisible()
    await queryClient.refetchQueries({ queryKey: ['messages', 'c1'] })
    expect(screen.getByText('Hi there')).toBeVisible()
    expect(posts).toBe(1)
  })
})
