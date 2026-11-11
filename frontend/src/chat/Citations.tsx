import { FileText } from 'lucide-react'
import type { Citation } from './api'

/** "Sources" under an assistant answer: one expandable row per cited chunk. */
export default function Citations({ items }: { items: Citation[] }) {
  if (items.length === 0) return null
  return (
    <div className="mt-3">
      <p className="mb-1.5 text-xs font-medium text-muted-foreground">Sources</p>
      <ul className="flex flex-col gap-1.5">
        {items.map((c) => (
          <li key={`${c.document_id}:${c.chunk_index}`}>
            <details className="rounded-lg border bg-background text-xs">
              <summary className="flex cursor-pointer list-none items-center gap-1.5 px-2.5 py-1.5 select-none">
                <FileText className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                <span className="min-w-0 truncate font-medium">{c.filename}</span>
                <span className="shrink-0 text-muted-foreground">· chunk {c.chunk_index + 1}</span>
              </summary>
              <p className="border-t px-2.5 py-2 break-words whitespace-pre-wrap text-muted-foreground">
                {c.snippet}
              </p>
            </details>
          </li>
        ))}
      </ul>
    </div>
  )
}
