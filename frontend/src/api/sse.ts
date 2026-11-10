/**
 * A minimal Server-Sent Events parser for `fetch` response bodies.
 *
 * The browser's built-in `EventSource` only supports GET, but sending a chat
 * message is a POST, so we read the stream ourselves. The wire format is a
 * sequence of lines; an empty line ends an event:
 *
 *     event: token
 *     data: {"text": "Hel"}
 *
 *     event: done
 *     data: {"content": "Hello"}
 *
 * Network chunks do not respect these boundaries: one chunk can hold several
 * events, and a line (even a `\r\n` pair) can be split across two chunks. So
 * we decode bytes into a text buffer, take only *complete* lines from it, and
 * keep the incomplete tail until the next chunk arrives.
 */

export type SSEEvent = { event: string; data: unknown }

/** Matches one line terminator: `\r\n`, `\n` or a lone `\r`. */
const LINE_BREAK = /\r\n|\n|\r/

export async function* parseSSE(body: ReadableStream<Uint8Array>): AsyncGenerator<SSEEvent> {
  const reader = body.getReader()
  // `stream: true` keeps a multi-byte character split across chunks intact.
  const decoder = new TextDecoder()
  let buffer = ''
  let eventName = ''
  let dataLines: string[] = []

  /** Build the event collected so far (if any) and reset for the next one. */
  function takeEvent(): SSEEvent | null {
    const ready = dataLines.length > 0
    const event = ready
      ? { event: eventName || 'message', data: parseData(dataLines.join('\n')) }
      : null
    eventName = ''
    dataLines = []
    return event
  }

  /** Apply one line to the event being built; returns an event on a blank line. */
  function handleLine(line: string): SSEEvent | null {
    if (line === '') return takeEvent()
    if (line.startsWith(':')) return null // comment / keep-alive
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') eventName = value
    else if (field === 'data') dataLines.push(value)
    // `id` and `retry` are not used by our backend, so they are ignored.
    return null
  }

  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      for (;;) {
        const match = LINE_BREAK.exec(buffer)
        if (!match) break
        // A `\r` at the very end might be the first half of a `\r\n` that the
        // next chunk completes; wait for more data before deciding.
        if (match[0] === '\r' && match.index === buffer.length - 1) break
        const line = buffer.slice(0, match.index)
        buffer = buffer.slice(match.index + match[0].length)
        const event = handleLine(line)
        if (event) yield event
      }
    }
    // The stream ended: treat any leftover text as a final, unterminated line.
    buffer += decoder.decode()
    const tail = buffer === '' ? null : handleLine(buffer.replace(/\r$/, ''))
    const last = tail ?? takeEvent()
    if (last) yield last
  } finally {
    // Runs on normal completion and when the consumer stops early (e.g. after
    // `done`): cancelling closes the underlying HTTP stream either way.
    await reader.cancel().catch(() => {})
  }
}

/** Our backend always sends JSON; fall back to the raw string otherwise. */
function parseData(raw: string): unknown {
  try {
    return JSON.parse(raw) as unknown
  } catch {
    return raw
  }
}
