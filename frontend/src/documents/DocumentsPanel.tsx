import { useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import { AlertCircle, FileText, Loader2, Trash2, Upload } from 'lucide-react'
import { ApiError } from '@/api/client'
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
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import {
  ALLOWED_EXTENSIONS,
  formatSize,
  useDeleteDocument,
  useDocuments,
  useUploadDocument,
  validateUpload,
} from './api'
import type { DocumentInfo } from './api'

type Props = {
  open: boolean
  onOpenChange(open: boolean): void
  /** How often to re-check processing documents; tests shorten this. */
  pollMs?: number
}

/** Right-hand sheet to upload, review and delete the user's documents. */
export default function DocumentsPanel({ open, onOpenChange, pollMs }: Props) {
  const documents = useDocuments(pollMs, open)
  const upload = useUploadDocument()
  const remove = useDeleteDocument()
  const inputRef = useRef<HTMLInputElement>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<DocumentInfo | null>(null)

  function onPick(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = '' // allow picking the same file again
    if (!file) return
    const problem = validateUpload(file)
    if (problem) {
      setUploadError(problem)
      return
    }
    setUploadError(null)
    upload.mutate(file, {
      onError: (err) =>
        setUploadError(err instanceof ApiError ? err.detail : "Couldn't upload the file."),
    })
  }

  function confirmDelete() {
    if (pendingDelete) remove.mutate(pendingDelete.id)
    setPendingDelete(null)
  }

  const items = documents.data ?? []

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Documents</SheetTitle>
          <SheetDescription>
            Files you upload here can be searched by the assistant. They're private to you.
          </SheetDescription>
        </SheetHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-3 px-4 pb-4">
          <input
            ref={inputRef}
            type="file"
            hidden
            aria-label="Choose a file"
            accept={ALLOWED_EXTENSIONS.join(',')}
            onChange={onPick}
          />
          <Button
            variant="outline"
            className="w-full justify-center gap-2"
            disabled={upload.isPending}
            onClick={() => inputRef.current?.click()}
          >
            {upload.isPending ? <Loader2 className="animate-spin" /> : <Upload />}
            {upload.isPending ? 'Uploading…' : 'Upload a file'}
          </Button>

          {uploadError && (
            <div
              role="alert"
              className="flex items-start gap-2.5 rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2.5 text-sm dark:bg-destructive/10"
            >
              <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" />
              <p className="min-w-0 flex-1">{uploadError}</p>
            </div>
          )}

          <div className="min-h-0 flex-1 overflow-y-auto">
            {documents.isPending && (
              <p className="py-2 text-sm text-muted-foreground">Loading documents…</p>
            )}
            {documents.isError && (
              <p className="py-2 text-sm text-muted-foreground">Couldn't load documents.</p>
            )}
            {documents.data && items.length === 0 && (
              <p className="py-2 text-sm text-muted-foreground">
                No documents yet. Upload a PDF, DOCX, TXT or MD file to ask questions about it.
              </p>
            )}
            <ul className="space-y-2">
              {items.map((d) => (
                <DocumentRow key={d.id} document={d} onDelete={() => setPendingDelete(d)} />
              ))}
            </ul>
          </div>
        </div>

        <AlertDialog
          open={pendingDelete !== null}
          onOpenChange={(o) => !o && setPendingDelete(null)}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Delete document?</AlertDialogTitle>
              <AlertDialogDescription>
                “{pendingDelete?.filename}” will be permanently deleted and the assistant will no
                longer be able to search it.
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
      </SheetContent>
    </Sheet>
  )
}

function DocumentRow({ document: d, onDelete }: { document: DocumentInfo; onDelete(): void }) {
  return (
    <li className="flex items-start gap-3 rounded-xl border bg-background p-3 text-sm">
      <FileText className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium" title={d.filename}>
          {d.filename}
        </p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
          <span>{formatSize(d.size_bytes)}</span>
          <StatusBadge document={d} />
        </p>
        {d.status === 'failed' && d.error && (
          <p className="mt-1.5 text-xs text-destructive">{d.error}</p>
        )}
      </div>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label={`Delete ${d.filename}`}
        title="Delete"
        onClick={onDelete}
      >
        <Trash2 />
      </Button>
    </li>
  )
}

function StatusBadge({ document: d }: { document: DocumentInfo }) {
  if (d.status === 'processing') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5">
        <Loader2 className="size-3 animate-spin" aria-hidden />
        Processing…
      </span>
    )
  }
  if (d.status === 'failed') {
    return (
      <span className="rounded-full bg-destructive/10 px-2 py-0.5 text-destructive">Failed</span>
    )
  }
  return (
    <span className="rounded-full bg-muted px-2 py-0.5">
      Ready · {d.chunk_count} {d.chunk_count === 1 ? 'chunk' : 'chunks'}
    </span>
  )
}
