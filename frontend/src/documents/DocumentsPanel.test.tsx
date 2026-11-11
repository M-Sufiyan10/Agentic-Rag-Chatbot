import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { vi } from 'vitest'
import { server } from '@/test/setup'
import DocumentsPanel from './DocumentsPanel'
import type { DocumentInfo } from './api'

function doc(over: Partial<DocumentInfo>): DocumentInfo {
  return {
    id: 'd1',
    filename: 'notes.txt',
    content_type: 'text/plain',
    size_bytes: 2048,
    status: 'ready',
    error: null,
    chunk_count: 3,
    created_at: '2026-01-01T00:00:00Z',
    ...over,
  }
}

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <DocumentsPanel open onOpenChange={() => {}} pollMs={20} />
    </QueryClientProvider>,
  )
}

describe('DocumentsPanel', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows the empty state', async () => {
    server.use(http.get('*/api/documents', () => HttpResponse.json([])))
    renderPanel()
    expect(
      await screen.findByText(
        'No documents yet. Upload a PDF, DOCX, TXT or MD file to ask questions about it.',
      ),
    ).toBeVisible()
  })

  it('lists documents with status badges and the error of a failed one', async () => {
    server.use(
      http.get('*/api/documents', () =>
        HttpResponse.json([
          doc({ id: 'a', filename: 'ready.pdf', size_bytes: 3 * 1024 * 1024, chunk_count: 5 }),
          doc({ id: 'b', filename: 'bad.docx', status: 'failed', error: 'Could not read it.' }),
          doc({ id: 'c', filename: 'busy.md', status: 'processing' }),
        ]),
      ),
    )
    renderPanel()
    expect(await screen.findByText('ready.pdf')).toBeVisible()
    expect(screen.getByText('3.0 MB')).toBeVisible()
    expect(screen.getByText('Ready · 5 chunks')).toBeVisible()
    expect(screen.getByText('Failed')).toBeVisible()
    expect(screen.getByText('Could not read it.')).toBeVisible()
    expect(screen.getByText('Processing…')).toBeVisible()
  })

  it('rejects an unsupported file client-side without a request', async () => {
    let posts = 0
    server.use(
      http.get('*/api/documents', () => HttpResponse.json([])),
      http.post('*/api/documents', () => {
        posts += 1
        return HttpResponse.json(doc({}), { status: 201 })
      }),
    )
    renderPanel()
    await screen.findByText(/No documents yet/)
    const input = screen.getByLabelText('Choose a file')
    await userEvent.upload(input, new File(['x'], 'virus.exe'), { applyAccept: false })
    expect(
      await screen.findByText('Unsupported file type. Upload a PDF, DOCX, TXT or MD file.'),
    ).toBeVisible()
    expect(posts).toBe(0)
  })

  it('rejects a file over 10 MB client-side', async () => {
    server.use(http.get('*/api/documents', () => HttpResponse.json([])))
    renderPanel()
    await screen.findByText(/No documents yet/)
    const big = new File([new Uint8Array(10 * 1024 * 1024 + 1)], 'big.txt')
    await userEvent.upload(screen.getByLabelText('Choose a file'), big)
    expect(await screen.findByText('File is larger than 10 MB.')).toBeVisible()
  })

  it('uploads a .txt as multipart and shows the new row', async () => {
    let stored: DocumentInfo[] = []
    server.use(http.get('*/api/documents', () => HttpResponse.json(stored)))
    // jsdom's FormData can't be serialised by Node's fetch, so intercept the
    // upload at fetch and inspect the FormData it was given.
    const realFetch = globalThis.fetch
    let sent: { body: BodyInit; headers: Headers } | null = null
    vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      if (init?.method === 'POST' && String(input).endsWith('/api/documents')) {
        sent = { body: init.body as BodyInit, headers: new Headers(init.headers) }
        stored = [doc({ filename: 'hello.txt' })]
        return Promise.resolve(Response.json(stored[0], { status: 201 }))
      }
      return realFetch(input, init)
    })
    renderPanel()
    await screen.findByText(/No documents yet/)
    await userEvent.upload(
      screen.getByLabelText('Choose a file'),
      new File(['hello'], 'hello.txt', { type: 'text/plain' }),
    )
    expect(await screen.findByText('hello.txt')).toBeVisible()
    expect(sent!.body).toBeInstanceOf(FormData)
    expect(((sent!.body as FormData).get('file') as File).name).toBe('hello.txt')
    expect(sent!.headers.has('Content-Type')).toBe(false)
  })

  it('shows the server error detail', async () => {
    server.use(
      http.get('*/api/documents', () => HttpResponse.json([])),
      http.post('*/api/documents', () =>
        HttpResponse.json({ detail: 'The file is empty.' }, { status: 400 }),
      ),
    )
    renderPanel()
    await screen.findByText(/No documents yet/)
    await userEvent.upload(screen.getByLabelText('Choose a file'), new File([''], 'e.txt'))
    expect(await screen.findByText('The file is empty.')).toBeVisible()
  })

  it('polls a processing document until it is ready', async () => {
    let calls = 0
    server.use(
      http.get('*/api/documents', () => {
        calls += 1
        return HttpResponse.json([
          calls < 3
            ? doc({ status: 'processing', chunk_count: 0 })
            : doc({ status: 'ready', chunk_count: 2 }),
        ])
      }),
    )
    renderPanel()
    expect(await screen.findByText('Processing…')).toBeVisible()
    expect(await screen.findByText('Ready · 2 chunks')).toBeVisible()
    const settled = calls
    await new Promise((r) => setTimeout(r, 80))
    expect(calls).toBe(settled) // polling stopped
  })

  it('deletes a document after confirming', async () => {
    let stored = [doc({ id: 'x', filename: 'gone.txt' })]
    server.use(
      http.get('*/api/documents', () => HttpResponse.json(stored)),
      http.delete('*/api/documents/x', () => {
        stored = []
        return new HttpResponse(null, { status: 204 })
      }),
    )
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: 'Delete gone.txt' }))
    const dialog = await screen.findByRole('alertdialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(screen.queryByText('gone.txt')).not.toBeInTheDocument())
  })
})
