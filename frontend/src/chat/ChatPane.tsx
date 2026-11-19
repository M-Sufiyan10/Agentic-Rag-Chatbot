import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import { AlertCircle, ArrowUp, RotateCcw } from 'lucide-react'
import { ApiError } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import DraftCard from '@/email/DraftCard'
import { useEmailDrafts } from '@/email/api'
import { useMessages } from './api'
import MessageBubble, { AssistantAvatar } from './MessageBubble'
import Citations from './Citations'
import FeedbackButtons from './FeedbackButtons'
import ToolChips from './ToolChips'
import { useChatStream } from './useChatStream'

type Props = {
  conversationId: string
  /** A message to send as soon as the pane mounts (from the new-chat screen). */
  initialMessage?: string
  /** Called once `initialMessage` has been sent, so the caller can forget it. */
  onInitialMessageSent?(): void
}

/** The transcript and composer for one conversation. */
export default function ChatPane({ conversationId, initialMessage, onInitialMessageSent }: Props) {
  const messages = useMessages(conversationId)
  const drafts = useEmailDrafts(conversationId).data ?? []
  const { streamingText, citations, tools, pendingMessage, status, error, send } =
    useChatStream(conversationId)
  const [lastSent, setLastSent] = useState<string | null>(null)
  const busy = status === 'waiting' || status === 'streaming'
  const toolRunning = tools.some((t) => t.status === 'running')

  function sendMessage(content: string) {
    setLastSent(content)
    void send(content)
  }

  // Send the message typed on the new-chat screen exactly once. The ref also
  // survives React StrictMode's double effect run in development.
  const initialSent = useRef(false)
  useEffect(() => {
    if (!initialMessage || initialSent.current) return
    initialSent.current = true
    sendMessage(initialMessage)
    onInitialMessageSent?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once on mount
  }, [])

  const history = messages.data ?? []
  // Hide the optimistic copy once a refetch already contains the message.
  const last = history.at(-1)
  const showPending =
    pendingMessage !== null && !(last?.role === 'user' && last.content === pendingMessage)
  const scroll = useAutoScroll([history.length, streamingText, tools, status])

  if (messages.isError && !busy) {
    const notFound = messages.error instanceof ApiError && messages.error.status === 404
    return (
      <CenteredNotice>
        {notFound
          ? "This conversation doesn't exist or was deleted."
          : "Couldn't load this conversation."}
      </CenteredNotice>
    )
  }

  const isEmpty = !messages.isPending && history.length === 0 && status === 'idle'

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div
        ref={scroll.containerRef}
        onScroll={scroll.onScroll}
        className="min-h-0 flex-1 overflow-y-auto"
      >
        {isEmpty ? (
          <Welcome onPick={sendMessage} />
        ) : (
          <div
            className="mx-auto flex w-full max-w-3xl flex-col gap-7 px-4 pt-8 pb-10 sm:px-6"
            aria-live="polite"
            aria-busy={busy}
          >
            {messages.isPending && <TranscriptSkeleton />}
            {history.map((m, i) => (
              <MessageBubble key={i} role={m.role} content={m.content}>
                {m.tools?.length ? <ToolChips names={m.tools} /> : null}
                {m.citations?.length ? <Citations items={m.citations} /> : null}
                {m.role === 'assistant' && m.id ? (
                  <FeedbackButtons
                    conversationId={conversationId}
                    turnId={m.id}
                    value={m.feedback ?? null}
                  />
                ) : null}
                {m.role === 'assistant' && m.id
                  ? drafts
                      .filter((d) => d.turn_id === m.id)
                      .map((d) => (
                        <DraftCard key={d.id} conversationId={conversationId} draft={d} />
                      ))
                  : null}
              </MessageBubble>
            ))}
            {showPending && <MessageBubble role="user" content={pendingMessage} />}
            {busy && tools.length > 0 && (
              <div className="pl-10 sm:pl-11">
                <ToolChips tools={tools} />
              </div>
            )}
            {busy && !streamingText && !toolRunning && <ThinkingIndicator />}
            {status === 'streaming' && streamingText && (
              <MessageBubble role="assistant" content={streamingText} streaming>
                <Citations items={citations} />
              </MessageBubble>
            )}
            {status === 'error' && error && (
              <ErrorNotice
                message={error}
                onRetry={lastSent === null ? undefined : () => sendMessage(lastSent)}
              />
            )}
          </div>
        )}
      </div>
      <div className="mx-auto w-full max-w-3xl px-4 pb-4 sm:px-6">
        <Composer onSend={sendMessage} disabled={busy} autoFocus />
      </div>
    </div>
  )
}

/**
 * Keeps the transcript pinned to the bottom as content grows (e.g. while
 * tokens stream in), unless the reader has scrolled up to read history.
 */
function useAutoScroll(deps: unknown[]) {
  const containerRef = useRef<HTMLDivElement>(null)
  const pinned = useRef(true)

  function onScroll() {
    const el = containerRef.current
    if (!el) return
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  useLayoutEffect(() => {
    const el = containerRef.current
    if (el && pinned.current) el.scrollTop = el.scrollHeight
    // eslint-disable-next-line react-hooks/exhaustive-deps -- caller supplies deps
  }, deps)

  return { containerRef, onScroll }
}

type ComposerProps = {
  onSend(content: string): void
  /** Blocks sending (typing is still allowed), e.g. while a reply streams. */
  disabled?: boolean
  autoFocus?: boolean
}

/** Message input. Enter sends; Shift+Enter inserts a newline. */
export function Composer({ onSend, disabled = false, autoFocus = false }: ComposerProps) {
  const [value, setValue] = useState('')
  const canSend = !disabled && value.trim() !== ''

  function submit() {
    if (!canSend) return
    onSend(value.trim())
    setValue('')
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
    >
      <div className="relative rounded-2xl border bg-background shadow-sm transition-shadow focus-within:border-ring/60 focus-within:shadow-md dark:bg-input/20">
        <Textarea
          aria-label="Message"
          placeholder="Message the assistant…"
          rows={1}
          autoFocus={autoFocus}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          className="max-h-52 min-h-[3.25rem] resize-none border-0 bg-transparent py-3.5 pr-14 pl-4 text-[0.95rem] shadow-none focus-visible:ring-0 dark:bg-transparent"
        />
        <Button
          type="submit"
          size="icon"
          aria-label="Send message"
          disabled={!canSend}
          className="absolute right-2.5 bottom-2.5 rounded-full"
        >
          <ArrowUp />
        </Button>
      </div>
      <p className="mt-2 hidden text-center text-xs text-muted-foreground sm:block">
        Enter to send · Shift + Enter for a new line
      </p>
    </form>
  )
}

const SUGGESTIONS = [
  "What's today's date in Tokyo?",
  'Search the web for the latest LangGraph release',
  'What is 18% of 2,450, plus 75?',
]

/** Empty-state greeting with a few starter prompts. */
export function Welcome({ onPick }: { onPick(content: string): void }) {
  return (
    <div className="mx-auto flex h-full w-full max-w-2xl flex-col items-center justify-center gap-8 px-4 py-12 text-center">
      <div className="flex flex-col items-center gap-3">
        <AssistantAvatar className="size-11 rounded-2xl [&_svg]:size-5" />
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">How can I help today?</h1>
        <p className="text-sm text-muted-foreground">
          Ask anything. Replies stream in as they're written.
        </p>
      </div>
      <div className="grid w-full gap-2 sm:grid-cols-3">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onPick(s)}
            className="rounded-xl border bg-background px-3.5 py-3 text-left text-sm text-muted-foreground transition-colors outline-none hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/20 dark:hover:bg-input/40"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}

function ThinkingIndicator() {
  return (
    <div className="flex items-center gap-3 sm:gap-4" role="status">
      <AssistantAvatar />
      <span className="flex items-center gap-2.5 text-sm text-muted-foreground">
        <span className="flex gap-1" aria-hidden>
          <span className="size-1.5 animate-bounce rounded-full bg-muted-foreground/70 [animation-delay:-0.3s]" />
          <span className="size-1.5 animate-bounce rounded-full bg-muted-foreground/70 [animation-delay:-0.15s]" />
          <span className="size-1.5 animate-bounce rounded-full bg-muted-foreground/70" />
        </span>
        Thinking…
      </span>
    </div>
  )
}

function ErrorNotice({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm dark:bg-destructive/10"
    >
      <AlertCircle className="size-4 shrink-0 text-destructive" />
      <p className="min-w-0 flex-1 text-foreground">{message}</p>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RotateCcw />
          Retry
        </Button>
      )}
    </div>
  )
}

function TranscriptSkeleton() {
  return (
    <div className="flex flex-col gap-7" aria-label="Loading messages">
      <div className="ml-auto h-10 w-2/5 animate-pulse rounded-2xl bg-muted" />
      <div className="flex gap-4">
        <div className="size-7 shrink-0 animate-pulse rounded-full bg-muted" />
        <div className="flex-1 space-y-2.5 pt-1">
          <div className="h-3.5 w-11/12 animate-pulse rounded bg-muted" />
          <div className="h-3.5 w-4/5 animate-pulse rounded bg-muted" />
          <div className="h-3.5 w-3/5 animate-pulse rounded bg-muted" />
        </div>
      </div>
    </div>
  )
}

function CenteredNotice({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground">
      {children}
    </div>
  )
}
