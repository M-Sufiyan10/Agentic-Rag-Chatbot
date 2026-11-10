import { parseSSE } from './sse'

/** Build a byte stream that delivers each string as its own chunk. */
function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
}

async function collect(chunks: string[]) {
  const events = []
  for await (const event of parseSSE(streamOf(chunks))) events.push(event)
  return events
}

describe('parseSSE', () => {
  it('handles an event split across chunks', async () => {
    expect(await collect(['event: tok', 'en\ndata: {"text":"Hi"}\n', '\n'])).toEqual([
      { event: 'token', data: { text: 'Hi' } },
    ])
  })

  it('handles two events in one chunk and CRLF', async () => {
    expect(
      await collect([
        'event: token\r\ndata: {"text":"a"}\r\n\r\nevent: done\r\ndata: {"content":"a"}\r\n\r\n',
      ]),
    ).toEqual([
      { event: 'token', data: { text: 'a' } },
      { event: 'done', data: { content: 'a' } },
    ])
  })

  it('handles a CRLF pair split across chunks', async () => {
    expect(await collect(['event: token\r', '\ndata: {"text":"b"}\r\n\r', '\n'])).toEqual([
      { event: 'token', data: { text: 'b' } },
    ])
  })

  it('ignores comment lines and defaults the event name to message', async () => {
    expect(await collect([': ping\n\ndata: {"x":1}\n\n'])).toEqual([
      { event: 'message', data: { x: 1 } },
    ])
  })

  it('handles a stream that ends on a lone CR', async () => {
    expect(await collect(['event: done\rdata: {"content":"d"}\r'])).toEqual([
      { event: 'done', data: { content: 'd' } },
    ])
  })

  it('flushes a final event that lacks the trailing blank line', async () => {
    expect(await collect(['event: done\ndata: {"content":"c"}'])).toEqual([
      { event: 'done', data: { content: 'c' } },
    ])
  })
})
