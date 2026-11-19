import { Mail } from 'lucide-react'
import { ApiError } from '@/api/client'
import { Button } from '@/components/ui/button'
import { useCancelDraft, useSendDraft, type EmailDraft } from './api'

type Props = {
  conversationId: string
  draft: EmailDraft
}

/** An email the assistant drafted. The user sends or cancels it; the model never can. */
export default function DraftCard({ conversationId, draft }: Props) {
  const send = useSendDraft(conversationId)
  const cancel = useCancelDraft(conversationId)
  const busy = send.isPending || cancel.isPending
  const mutationError = send.error ?? cancel.error
  const errorText =
    mutationError instanceof ApiError
      ? mutationError.detail
      : mutationError
        ? "Couldn't reach the server."
        : null

  return (
    <div className="mt-2 rounded-xl border bg-background px-4 py-3 text-sm dark:bg-input/20">
      <div className="mb-2 flex items-center gap-2 font-medium">
        <Mail className="size-4 text-muted-foreground" />
        Draft email
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-muted-foreground">To</dt>
        <dd className="min-w-0 break-words">{draft.to_address}</dd>
        <dt className="text-muted-foreground">Subject</dt>
        <dd className="min-w-0 break-words">{draft.subject}</dd>
      </dl>
      <div className="mt-2 rounded-lg bg-muted/50 px-3 py-2 break-words whitespace-pre-wrap">
        {draft.body}
      </div>
      {draft.status === 'pending' && (
        <div className="mt-3 flex items-center justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => cancel.mutate(draft.id)}
          >
            Cancel
          </Button>
          <Button type="button" size="sm" disabled={busy} onClick={() => send.mutate(draft.id)}>
            Send
          </Button>
        </div>
      )}
      {draft.status === 'sending' && <p className="mt-3 text-muted-foreground">Sending…</p>}
      {draft.status === 'sent' && (
        <p className="mt-3 text-muted-foreground">
          Sent ✓{draft.sent_at ? ` ${new Date(draft.sent_at).toLocaleString()}` : ''}
        </p>
      )}
      {draft.status === 'cancelled' && <p className="mt-3 text-muted-foreground">Cancelled</p>}
      {draft.status === 'failed' && (
        <p className="mt-3 text-destructive">Failed: {draft.error ?? 'unknown error'}</p>
      )}
      {errorText && (
        <p role="alert" className="mt-3 text-destructive">
          {errorText}
        </p>
      )}
    </div>
  )
}
