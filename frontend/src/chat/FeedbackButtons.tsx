import { ThumbsDown, ThumbsUp } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useFeedback, type FeedbackValue } from './api'

type Props = {
  conversationId: string
  turnId: string
  value: FeedbackValue | null
}

/** Thumbs up/down under a saved assistant reply. Clicking the active one clears it. */
export default function FeedbackButtons({ conversationId, turnId, value }: Props) {
  const feedback = useFeedback(conversationId)

  function vote(next: FeedbackValue) {
    feedback.mutate({ turnId, value: value === next ? null : next })
  }

  return (
    <div className="mt-1 flex items-center gap-1">
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label="Good response"
        aria-pressed={value === 'up'}
        onClick={() => vote('up')}
        className="text-muted-foreground aria-pressed:text-foreground"
      >
        <ThumbsUp className={value === 'up' ? 'fill-current' : undefined} />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label="Bad response"
        aria-pressed={value === 'down'}
        onClick={() => vote('down')}
        className="text-muted-foreground aria-pressed:text-foreground"
      >
        <ThumbsDown className={value === 'down' ? 'fill-current' : undefined} />
      </Button>
      {feedback.isError && (
        <span role="alert" className="ml-1 text-xs text-destructive">
          Couldn't save feedback
        </span>
      )}
    </div>
  )
}
