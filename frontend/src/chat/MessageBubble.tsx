import type { ReactNode } from 'react'
import { Sparkles } from 'lucide-react'
import Markdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { cn } from '@/lib/utils'

type Props = {
  role: 'user' | 'assistant'
  content: string
  /** True while the reply is still arriving; shows a blinking caret. */
  streaming?: boolean
  /**
   * Extra content rendered under an assistant reply. Later milestones put
   * tool-status chips and citations here.
   */
  children?: ReactNode
}

/** Links in model output open in a new tab and never get window.opener. */
const markdownComponents: Components = {
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
}

/**
 * One chat message. Content is rendered as Markdown (GitHub-flavoured). Raw
 * HTML in the text is *not* rendered (no `rehype-raw`), so model output can't
 * inject elements or scripts into the page.
 */
export default function MessageBubble({ role, content, streaming = false, children }: Props) {
  if (role === 'user') {
    return (
      <div className="flex justify-end" data-role="user">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-secondary px-4 py-2.5 text-secondary-foreground sm:max-w-[75%]">
          <p className="text-[0.95rem] leading-relaxed break-words whitespace-pre-wrap">
            {content}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex gap-3 sm:gap-4" data-role="assistant">
      <AssistantAvatar />
      <div className="min-w-0 flex-1 pt-0.5">
        <div className={cn('markdown', streaming && 'markdown-streaming')}>
          <Markdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
            {content}
          </Markdown>
        </div>
        {children}
      </div>
    </div>
  )
}

export function AssistantAvatar({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn(
        'flex size-7 shrink-0 items-center justify-center rounded-full border bg-background text-foreground shadow-xs dark:bg-input/30',
        className,
      )}
    >
      <Sparkles className="size-3.5" />
    </div>
  )
}
