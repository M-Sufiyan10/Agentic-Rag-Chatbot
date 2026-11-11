/**
 * Sends a chat message and follows the streamed reply.
 *
 * Flow for one `send(content)`:
 *   1. status → "waiting"; the user's message becomes `pendingMessage` so the
 *      UI can show it straight away (optimistic update). It is kept in hook
 *      state rather than written into the query cache, so a background
 *      refetch of the transcript can't wipe it out mid-stream.
 *   2. POST the message. The response body is an SSE stream (see `parseSSE`).
 *   3. Each `token` event appends to `streamingText`; the first one moves the
 *      status to "streaming".
 *   4. On `done`, the server has saved both messages, so we refetch the
 *      transcript and the sidebar (the title is set from the first message),
 *      then clear `pendingMessage` and `streamingText` and go back to "idle".
 *   5. An `error` event, a non-2xx response or a dropped connection sets
 *      status "error" with a user-facing message. `pendingMessage` stays, so
 *      the user still sees what they sent (and can retry it).
 *
 * `tool_start` / `tool_end` events track tool activity for the live chips.
 * Events this milestone doesn't know about (later: `citations`) are ignored, so the backend can add them without breaking us.
 */
import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useRef, useState } from 'react'
import { apiFetch } from '@/api/client'
import { parseSSE } from '@/api/sse'
import { conversationsKey, messagesKey } from './api'

export type ChatStreamStatus = 'idle' | 'waiting' | 'streaming' | 'error'

export type ToolActivity = {
  id: string
  name: string
  input: unknown
  output?: string
  status: 'running' | 'done'
}

export type ChatStream = {
  streamingText: string
  /** Tools the assistant has used (or is using) for the current reply. */
  tools: ToolActivity[]
  /** The message being sent, shown until the saved transcript includes it. */
  pendingMessage: string | null
  status: ChatStreamStatus
  error: string | null
  send(content: string): Promise<void>
}

const CONFLICT_MESSAGE = 'A reply is already in progress'
const GENERIC_MESSAGE = "Couldn't reach the assistant. Check your connection and try again."
const INCOMPLETE_MESSAGE = 'The reply was interrupted. Please try again.'

export function useChatStream(conversationId: string): ChatStream {
  const queryClient = useQueryClient()
  const [streamingText, setStreamingText] = useState('')
  const [tools, setTools] = useState<ToolActivity[]>([])
  const [pendingMessage, setPendingMessage] = useState<string | null>(null)
  const [status, setStatus] = useState<ChatStreamStatus>('idle')
  const [error, setError] = useState<string | null>(null)
  // A ref (not state) so two sends in the same tick can't both get through.
  const busy = useRef(false)

  const send = useCallback(
    async (content: string) => {
      if (busy.current) return
      busy.current = true
      setError(null)
      setStreamingText('')
      setTools([])
      setPendingMessage(content)
      setStatus('waiting')

      const fail = (message: string) => {
        setStreamingText('')
        setTools([])
        setError(message)
        setStatus('error')
      }

      try {
        const res = await apiFetch(`/api/conversations/${conversationId}/messages`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
          },
          body: JSON.stringify({ content }),
        })
        if (!res.ok || !res.body) {
          fail(res.status === 409 ? CONFLICT_MESSAGE : await errorDetail(res))
          return
        }

        let text = ''
        for await (const { event, data } of parseSSE(res.body)) {
          if (event === 'token') {
            text += (data as { text: string }).text
            setStreamingText(text)
            setStatus('streaming')
          } else if (event === 'tool_start') {
            const d = data as { id?: unknown; name?: unknown; input?: unknown }
            if (typeof d?.id !== 'string' || typeof d.name !== 'string') continue
            const activity: ToolActivity = {
              id: d.id,
              name: d.name,
              input: d.input,
              status: 'running',
            }
            // Text before a tool call is preamble; the backend drops it too.
            text = ''
            setStreamingText('')
            setTools((prev) => [...prev, activity])
            setStatus('streaming')
          } else if (event === 'tool_end') {
            const d = data as {
              id?: unknown
              name?: unknown
              output?: unknown
            }
            if (typeof d?.id !== 'string' || typeof d.name !== 'string') continue
            const output = typeof d.output === 'string' ? d.output : ''
            setTools((prev) =>
              prev.map((t) => (t.id === d.id ? { ...t, output, status: 'done' } : t)),
            )
          } else if (event === 'done') {
            // Keep showing the final text until the saved transcript arrives,
            // so the reply never blinks out between the two.
            setStreamingText((data as { content: string }).content)
            setStatus('streaming')
            await Promise.all([
              queryClient.invalidateQueries({
                queryKey: messagesKey(conversationId),
              }),
              queryClient.invalidateQueries({ queryKey: conversationsKey }),
            ])
            setStreamingText('')
            setTools([])
            setPendingMessage(null)
            setStatus('idle')
            return
          } else if (event === 'error') {
            fail((data as { message: string }).message)
            return
          }
        }
        fail(INCOMPLETE_MESSAGE)
      } catch {
        fail(GENERIC_MESSAGE)
      } finally {
        busy.current = false
      }
    },
    [conversationId, queryClient],
  )

  return { streamingText, tools, pendingMessage, status, error, send }
}

async function errorDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // non-JSON body
  }
  return GENERIC_MESSAGE
}
