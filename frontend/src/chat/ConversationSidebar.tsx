import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import {
  FileText,
  LogOut,
  MessageSquare,
  MoreHorizontal,
  Pencil,
  SquarePen,
  Trash2,
} from 'lucide-react'
import { Link, useNavigate } from 'react-router'
import { useAuth } from '@/auth/AuthProvider'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import DocumentsPanel from '@/documents/DocumentsPanel'
import { cn } from '@/lib/utils'
import { useConversations, useDeleteConversation, useRenameConversation } from './api'
import type { Conversation } from './api'

type Props = {
  activeId?: string
  /** Called after the user picks a destination (closes the mobile drawer). */
  onNavigate?(): void
}

/** Left-hand panel: new chat, the conversation list, and the account footer. */
export default function ConversationSidebar({ activeId, onNavigate }: Props) {
  const navigate = useNavigate()
  const conversations = useConversations()
  const deleteConversation = useDeleteConversation()
  const [editingId, setEditingId] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<Conversation | null>(null)
  const [documentsOpen, setDocumentsOpen] = useState(false)

  function confirmDelete() {
    if (!pendingDelete) return
    const { id } = pendingDelete
    deleteConversation.mutate(id, {
      onSuccess: () => {
        if (id === activeId) void navigate('/', { replace: true })
      },
    })
    setPendingDelete(null)
  }

  return (
    <div className="flex h-full flex-col bg-sidebar text-sidebar-foreground">
      <div className="flex h-14 shrink-0 items-center gap-2 px-3">
        <Link
          to="/"
          onClick={onNavigate}
          className="flex items-center gap-2 rounded-md px-1.5 py-1 text-sm font-semibold tracking-tight"
        >
          <span className="flex size-6 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <MessageSquare className="size-3.5" />
          </span>
          Agentic Chatbot
        </Link>
      </div>
      <div className="px-3 pb-2">
        <Button asChild variant="outline" className="w-full justify-start gap-2 bg-background">
          <Link to="/" onClick={onNavigate}>
            <SquarePen />
            New chat
          </Link>
        </Button>
      </div>

      <nav aria-label="Conversations" className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {conversations.isPending && <ListSkeleton />}
        {conversations.isError && (
          <p className="px-3 py-2 text-sm text-muted-foreground">Couldn't load conversations.</p>
        )}
        {conversations.data?.length === 0 && (
          <p className="px-3 py-2 text-sm text-muted-foreground">No conversations yet.</p>
        )}
        {groupByRecency(conversations.data ?? []).map(({ label, items }) => (
          <section key={label} className="mt-3">
            <h2 className="px-3 pb-1 text-xs font-medium text-muted-foreground">{label}</h2>
            <ul className="space-y-0.5">
              {items.map((c) => (
                <li key={c.id}>
                  {editingId === c.id ? (
                    <RenameField conversation={c} onDone={() => setEditingId(null)} />
                  ) : (
                    <ConversationRow
                      conversation={c}
                      active={c.id === activeId}
                      onNavigate={onNavigate}
                      onRename={() => setEditingId(c.id)}
                      onDelete={() => setPendingDelete(c)}
                    />
                  )}
                </li>
              ))}
            </ul>
          </section>
        ))}
      </nav>

      <div className="px-3 pb-2">
        <Button
          variant="ghost"
          className="w-full justify-start gap-2"
          onClick={() => setDocumentsOpen(true)}
        >
          <FileText />
          Documents
        </Button>
      </div>
      <DocumentsPanel open={documentsOpen} onOpenChange={setDocumentsOpen} />

      <AccountFooter />

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete conversation?</AlertDialogTitle>
            <AlertDialogDescription>
              “{pendingDelete?.title}” and all of its messages will be permanently deleted.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={confirmDelete}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

type RowProps = {
  conversation: Conversation
  active: boolean
  onNavigate?(): void
  onRename(): void
  onDelete(): void
}

function ConversationRow({ conversation, active, onNavigate, onRename, onDelete }: RowProps) {
  return (
    <div
      className={cn(
        'group relative flex items-center rounded-lg text-sm transition-colors hover:bg-sidebar-accent',
        active && 'bg-sidebar-accent font-medium text-sidebar-accent-foreground',
      )}
    >
      <Link
        to={`/c/${conversation.id}`}
        onClick={onNavigate}
        aria-current={active ? 'page' : undefined}
        title={conversation.title}
        className="min-w-0 flex-1 truncate rounded-lg py-2 pr-9 pl-3 outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
      >
        {conversation.title}
      </Link>
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={`Options for ${conversation.title}`}
            className="absolute right-1.5 md:opacity-0 md:group-hover:opacity-100 md:focus-visible:opacity-100 md:aria-expanded:opacity-100"
          >
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        {/* Don't return focus to the trigger, so the rename input keeps it. */}
        <DropdownMenuContent
          align="start"
          className="w-36"
          onCloseAutoFocus={(e) => e.preventDefault()}
        >
          <DropdownMenuItem onSelect={onRename}>
            <Pencil />
            Rename
          </DropdownMenuItem>
          <DropdownMenuItem variant="destructive" onSelect={onDelete}>
            <Trash2 />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}

/** Inline title editor: Enter or blur saves, Escape cancels. */
function RenameField({ conversation, onDone }: { conversation: Conversation; onDone(): void }) {
  const rename = useRenameConversation()
  const [title, setTitle] = useState(conversation.title)
  // Set once the edit ends, so a blur that follows Enter/Escape is ignored.
  const finished = useRef(false)

  function save(e?: FormEvent) {
    e?.preventDefault()
    if (finished.current) return
    finished.current = true
    const next = title.trim()
    if (next && next !== conversation.title) rename.mutate({ id: conversation.id, title: next })
    onDone()
  }

  return (
    <form onSubmit={save}>
      <Input
        autoFocus
        aria-label="Conversation title"
        value={title}
        maxLength={200}
        onChange={(e) => setTitle(e.target.value)}
        onFocus={(e) => e.target.select()}
        onBlur={() => save()}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            finished.current = true
            onDone()
          }
        }}
        className="h-9 bg-background"
      />
    </form>
  )
}

function AccountFooter() {
  const { user, logout } = useAuth()
  const email = user?.email ?? ''

  return (
    <div className="flex items-center gap-2.5 border-t px-3 py-3">
      <span
        aria-hidden
        className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground uppercase"
      >
        {email.charAt(0)}
      </span>
      <span className="min-w-0 flex-1 truncate text-sm" title={email}>
        {email}
      </span>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label="Log out"
        title="Log out"
        onClick={() => void logout()}
      >
        <LogOut />
      </Button>
    </div>
  )
}

function ListSkeleton() {
  return (
    <div className="mt-3 space-y-2 px-3" aria-label="Loading conversations">
      {[80, 65, 72, 50].map((w) => (
        <div
          key={w}
          className="h-4 animate-pulse rounded bg-sidebar-accent"
          style={{ width: `${w}%` }}
        />
      ))}
    </div>
  )
}

/** Bucket conversations (already newest-first) into Today / Previous 7 days / Older. */
function groupByRecency(items: Conversation[]): { label: string; items: Conversation[] }[] {
  const startOfToday = new Date()
  startOfToday.setHours(0, 0, 0, 0)
  const weekAgo = startOfToday.getTime() - 7 * 24 * 60 * 60 * 1000
  const groups = [
    { label: 'Today', items: [] as Conversation[] },
    { label: 'Previous 7 days', items: [] as Conversation[] },
    { label: 'Older', items: [] as Conversation[] },
  ]
  for (const c of items) {
    const t = new Date(c.updated_at).getTime()
    const index = t >= startOfToday.getTime() ? 0 : t >= weekAgo ? 1 : 2
    groups[index].items.push(c)
  }
  return groups.filter((g) => g.items.length > 0)
}
