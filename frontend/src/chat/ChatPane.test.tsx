import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/setup'
import ChatPane from './ChatPane'

type Frame = { event: string; data: unknown }

/** Holds a stream after its first `afterFrames` frames until the test calls `release()`. */
type Gate = { afterFrames: number; opened: Promise<void>; release: () => void }

function makeGate(afterFrames: number): Gate {
  let release = () => {}
  const opened = new Promise<void>((resolve) => {
    release = resolve
  })
  return { afterFrames, opened, release }
}

/**
 * An SSE response that emits each frame after a short pause, like a real model.
 * With a gate, the stream pauses after `gate.afterFrames` frames until released, so a
 * test can assert a transient state (such as a running tool chip) deterministically.
 */
function sseResponse(
  frames: Frame[],
  pauseMs = 30,
  gate?: Gate,
): HttpResponse<ReadableStream<Uint8Array>> {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      for (const [index, { event, data }] of frames.entries()) {
        if (gate && index === gate.afterFrames) await gate.opened
        await new Promise((resolve) => setTimeout(resolve, pauseMs))
        controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`))
      }
      controller.close()
    },
  })
  return new HttpResponse(body, {
    headers: { 'content-type': 'text/event-stream' },
  })
}

function renderPane() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
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
        return sseResponse(
          [
            { event: 'token', data: { text: 'Hel' } },
            { event: 'token', data: { text: 'lo' } },
            { event: 'done', data: { content: 'Hello' } },
          ],
          150,
        )
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
            data: {
              message: "The assistant couldn't respond. Please try again.",
            },
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
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
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

  it('shows a running tool chip, then marks it done, then streams the answer', async () => {
    const gate = makeGate(1)
    server.use(
      http.get('*/api/conversations/c1/messages', () => HttpResponse.json([])),
      http.post('*/api/conversations/c1/messages', () =>
        sseResponse(
          [
            {
              event: 'tool_start',
              data: { id: 'c1', name: 'web_search', input: { query: 'x' } },
            },
            {
              event: 'tool_end',
              data: { id: 'c1', name: 'web_search', output: 'r' },
            },
            { event: 'token', data: { text: 'Answer' } },
            { event: 'done', data: { content: 'Answer' } },
          ],
          150,
          gate,
        ),
      ),
    )
    renderPane()
    await typeAndSend('hi')

    expect(await screen.findByText('Searching the web…')).toBeVisible()
    gate.release()
    expect(await screen.findByText('Searched the web')).toBeVisible()
    expect(await screen.findByText('Answer')).toBeVisible()
  })

  it('shows tool chips on assistant messages loaded from history', async () => {
    server.use(
      http.get('*/api/conversations/c1/messages', () =>
        HttpResponse.json([
          { role: 'user', content: 'q', tools: [] },
          { role: 'assistant', content: 'a', tools: ['calculator'] },
        ]),
      ),
    )
    renderPane()
    expect(await screen.findByText('Calculated')).toBeVisible()
  })

  it('shows feedback buttons only on saved assistant messages with an id', async () => {
    server.use(
      http.get('*/api/conversations/c1/messages', () =>
        HttpResponse.json([
          { role: 'user', content: 'Q', id: null, feedback: null },
          {
            role: 'assistant',
            content: 'Saved answer',
            id: 't1',
            feedback: 'up',
          },
          {
            role: 'assistant',
            content: 'No id answer',
            id: null,
            feedback: null,
          },
        ]),
      ),
    )
    renderPane()
    await screen.findByText('Saved answer')
    expect(screen.getAllByRole('button', { name: 'Good response' })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: 'Bad response' })).toHaveLength(1)
  })

  it('shows no feedback buttons on the live streaming bubble', async () => {
    const gate = makeGate(1)
    server.use(
      http.get('*/api/conversations/c1/messages', () => HttpResponse.json([])),
      http.post('*/api/conversations/c1/messages', () =>
        sseResponse(
          [
            { event: 'token', data: { text: 'Streaming now' } },
            { event: 'done', data: { content: 'Streaming now' } },
          ],
          10,
          gate,
        ),
      ),
    )
    renderPane()
    await typeAndSend('Hi')
    await screen.findByText('Thinking…')
    gate.release()
    await screen.findByText('Streaming now')
    expect(screen.queryByRole('button', { name: 'Good response' })).not.toBeInTheDocument()
  })

  it('ignores malformed tool events', async () => {
    server.use(
      http.get('*/api/conversations/c1/messages', () => HttpResponse.json([])),
      http.post('*/api/conversations/c1/messages', () =>
        sseResponse([
          { event: 'tool_start', data: {} },
          { event: 'token', data: { text: 'Fine' } },
          { event: 'done', data: { content: 'Fine' } },
        ]),
      ),
    )
    renderPane()
    await typeAndSend('hi')

    expect(await screen.findByText('Fine')).toBeVisible()
    expect(screen.queryByText(/Calling/)).toBeNull()
  })

  const sources = [
    { document_id: 'd1', filename: 'handbook.pdf', chunk_index: 2, snippet: 'Vacation policy' },
  ]

  it('shows a documents chip and then Sources for a cited answer', async () => {
    const gate = makeGate(1)
    let history: unknown[] = []
    server.use(
      http.get('*/api/conversations/c1/messages', () => HttpResponse.json(history)),
      http.post('*/api/conversations/c1/messages', () => {
        history = [
          { role: 'user', content: 'q', tools: [], citations: [] },
          { role: 'assistant', content: 'Answer', tools: ['search_documents'], citations: sources },
        ]
        return sseResponse(
          [
            { event: 'tool_start', data: { id: 't1', name: 'search_documents', input: {} } },
            { event: 'tool_end', data: { id: 't1', name: 'search_documents', output: 'r' } },
            { event: 'token', data: { text: 'Answer' } },
            { event: 'citations', data: { items: sources } },
            { event: 'done', data: { content: 'Answer' } },
          ],
          150,
          gate,
        )
      }),
    )
    renderPane()
    await typeAndSend('q')

    expect(await screen.findByText('Searching your documents…')).toBeVisible()
    gate.release()
    expect(await screen.findByText('Sources')).toBeVisible()
    expect(screen.getByText('handbook.pdf')).toBeVisible()
    // After done, the saved transcript still shows them.
    await waitFor(() => expect(screen.queryByText('Thinking…')).not.toBeInTheDocument())
    expect(await screen.findByText('Searched your documents')).toBeVisible()
    expect(screen.getByText('Sources')).toBeVisible()
  })

  it('shows Sources on assistant messages loaded from history', async () => {
    server.use(
      http.get('*/api/conversations/c1/messages', () =>
        HttpResponse.json([
          { role: 'user', content: 'q', tools: [], citations: [] },
          { role: 'assistant', content: 'a', tools: [], citations: sources },
        ]),
      ),
    )
    renderPane()
    expect(await screen.findByText('Sources')).toBeVisible()
    expect(screen.getByText('handbook.pdf')).toBeVisible()
  })

  it('ignores a malformed citations event', async () => {
    server.use(
      http.get('*/api/conversations/c1/messages', () => HttpResponse.json([])),
      http.post('*/api/conversations/c1/messages', () =>
        sseResponse(
          [
            { event: 'token', data: { text: 'Fine' } },
            { event: 'citations', data: { items: [{ filename: 5 }] } },
            { event: 'done', data: { content: 'Fine' } },
          ],
          100,
        ),
      ),
    )
    renderPane()
    await typeAndSend('hi')

    expect(await screen.findByText('Fine')).toBeVisible()
    await new Promise((r) => setTimeout(r, 300))
    expect(screen.queryByText('Sources')).toBeNull()
  })
})
