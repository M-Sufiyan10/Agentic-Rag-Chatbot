/**
 * Document API calls and TanStack Query hooks. Key: ["documents"] (the list).
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, apiFetch, apiJson } from '@/api/client'

export type DocumentStatus = 'processing' | 'ready' | 'failed'

export type DocumentInfo = {
  id: string
  filename: string
  content_type: string
  size_bytes: number
  status: DocumentStatus
  error: string | null
  chunk_count: number
  created_at: string
}

export const documentsKey = ['documents'] as const

export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024
export const ALLOWED_EXTENSIONS = ['.pdf', '.docx', '.txt', '.md']
export const UNSUPPORTED_MESSAGE = 'Unsupported file type. Upload a PDF, DOCX, TXT or MD file.'
export const TOO_LARGE_MESSAGE = 'File is larger than 10 MB.'

/** The same checks the server makes, so obvious mistakes never leave the browser. */
export function validateUpload(file: File): string | null {
  const name = file.name.toLowerCase()
  if (!ALLOWED_EXTENSIONS.some((ext) => name.endsWith(ext))) return UNSUPPORTED_MESSAGE
  if (file.size > MAX_UPLOAD_BYTES) return TOO_LARGE_MESSAGE
  return null
}

export function listDocuments(): Promise<DocumentInfo[]> {
  return apiJson<DocumentInfo[]>('/api/documents')
}

export async function uploadDocument(file: File): Promise<DocumentInfo> {
  const body = new FormData()
  body.append('file', file)
  // No Content-Type: the browser adds the multipart boundary itself.
  const res = await apiFetch('/api/documents', { method: 'POST', body })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const err = (await res.json()) as { detail?: unknown }
      if (typeof err.detail === 'string') detail = err.detail
    } catch {
      // non-JSON error body: keep statusText
    }
    throw new ApiError(res.status, detail)
  }
  return (await res.json()) as DocumentInfo
}

export function deleteDocument(id: string): Promise<void> {
  return apiJson<void>(`/api/documents/${id}`, { method: 'DELETE' })
}

/** Polls while any document is still being processed. `enabled` defers the fetch. */
export function useDocuments(pollMs = 2000, enabled = true) {
  return useQuery({
    queryKey: documentsKey,
    queryFn: listDocuments,
    enabled,
    refetchInterval: (q) => (q.state.data?.some((d) => d.status === 'processing') ? pollMs : false),
  })
}

export function useUploadDocument() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: uploadDocument,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: documentsKey }),
  })
}

export function useDeleteDocument() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: deleteDocument,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: documentsKey }),
  })
}

export function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
