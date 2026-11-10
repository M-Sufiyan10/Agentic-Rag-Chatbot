import { useState } from 'react'
import { Menu } from 'lucide-react'
import { useLocation, useNavigate, useParams } from 'react-router'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from '@/components/ui/sheet'
import { useConversations, useCreateConversation } from '@/chat/api'
import ChatPane, { Composer, Welcome } from '@/chat/ChatPane'
import ConversationSidebar from '@/chat/ConversationSidebar'

/** Router state carried from the new-chat screen to the new conversation. */
type ChatLocationState = { initialMessage?: string } | null

/**
 * The main app screen, mounted at `/` (new chat) and `/c/:conversationId`.
 * Sidebar on the left from 768px up; below that it lives in a slide-out drawer.
 */
export default function ChatPage() {
  const { conversationId } = useParams()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const conversations = useConversations()
  const title = conversations.data?.find((c) => c.id === conversationId)?.title

  return (
    <div className="flex h-dvh overflow-hidden bg-background">
      <aside className="hidden w-72 shrink-0 border-r md:block">
        <ConversationSidebar activeId={conversationId} />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-2 border-b px-3 md:px-6">
          <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="md:hidden" aria-label="Open sidebar">
                <Menu />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-72 gap-0 p-0" aria-describedby={undefined}>
              <SheetTitle className="sr-only">Conversations</SheetTitle>
              <ConversationSidebar
                activeId={conversationId}
                onNavigate={() => setDrawerOpen(false)}
              />
            </SheetContent>
          </Sheet>
          <h1 className="truncate text-sm font-medium">
            {conversationId ? (title ?? '') : 'New chat'}
          </h1>
        </header>

        <main className="min-h-0 flex-1">
          {conversationId ? (
            <ConversationView key={conversationId} conversationId={conversationId} />
          ) : (
            <NewChat />
          )}
        </main>
      </div>
    </div>
  )
}

function ConversationView({ conversationId }: { conversationId: string }) {
  const location = useLocation()
  const navigate = useNavigate()
  const state = location.state as ChatLocationState

  return (
    <ChatPane
      conversationId={conversationId}
      initialMessage={state?.initialMessage}
      // Drop the message from history state so a reload doesn't resend it.
      onInitialMessageSent={() => void navigate(location.pathname, { replace: true, state: null })}
    />
  )
}

/**
 * The `/` screen. Conversations are created lazily: only when the first
 * message is sent, so "New chat" never leaves empty chats behind.
 */
function NewChat() {
  const navigate = useNavigate()
  const create = useCreateConversation()

  async function start(content: string) {
    try {
      const conversation = await create.mutateAsync()
      const state: ChatLocationState = { initialMessage: content }
      void navigate(`/c/${conversation.id}`, { state })
    } catch {
      // The error is shown below from `create.isError`.
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <Welcome onPick={(s) => void start(s)} />
      </div>
      <div className="mx-auto w-full max-w-3xl px-4 pb-4 sm:px-6">
        {create.isError && (
          <p role="alert" className="mb-2 text-center text-sm text-destructive">
            Couldn't start a new chat. Please try again.
          </p>
        )}
        <Composer onSend={(s) => void start(s)} disabled={create.isPending} autoFocus />
      </div>
    </div>
  )
}
