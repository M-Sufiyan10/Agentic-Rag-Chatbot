/**
 * Email API calls and TanStack Query hooks. Keys: ["emailAccount"] and
 * ["emailDrafts", conversationId].
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, apiJson } from '@/api/client'

export type EmailSecurity = 'ssl' | 'starttls' | 'none'

export type EmailAccount = {
  address: string
  username: string
  imap_host: string
  imap_port: number
  imap_security: EmailSecurity
  smtp_host: string
  smtp_port: number
  smtp_security: EmailSecurity
  has_password: boolean
}

export type EmailAccountInput = {
  address: string
  username: string
  /** Omit to keep the stored password. */
  password?: string
  imap_host: string
  imap_port: number
  imap_security: EmailSecurity
  smtp_host: string
  smtp_port: number
  smtp_security: EmailSecurity
}

export type EmailTestResult = { ok: boolean; error: string | null }

export type EmailDraftStatus = 'pending' | 'sending' | 'sent' | 'cancelled' | 'failed'

export type EmailDraft = {
  id: string
  turn_id: string
  to_address: string
  subject: string
  body: string
  reply_to_uid: string | null
  status: EmailDraftStatus
  error: string | null
  created_at: string
  sent_at: string | null
}

export const emailAccountKey = ['emailAccount'] as const
export const emailDraftsKey = (cid: string) => ['emailDrafts', cid] as const

/** The account, `null` when none is saved, or `'disabled'` when the server has email off. */
export async function fetchEmailAccount(): Promise<EmailAccount | null | 'disabled'> {
  try {
    return await apiJson<EmailAccount>('/api/email/account')
  } catch (err) {
    if (err instanceof ApiError) {
      if (err.status === 404) return null
      if (err.status === 503) return 'disabled'
    }
    throw err
  }
}

export function useEmailAccount(enabled = true) {
  return useQuery({
    queryKey: emailAccountKey,
    queryFn: fetchEmailAccount,
    enabled,
  })
}

export function useSaveEmailAccount() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: EmailAccountInput) =>
      apiJson<EmailAccount>('/api/email/account', {
        method: 'PUT',
        body: JSON.stringify(input),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: emailAccountKey }),
  })
}

export function useTestEmailAccount() {
  return useMutation({
    mutationFn: (input: EmailAccountInput) =>
      apiJson<EmailTestResult>('/api/email/account/test', {
        method: 'POST',
        body: JSON.stringify(input),
      }),
  })
}

export function useDeleteEmailAccount() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiJson<void>('/api/email/account', { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: emailAccountKey }),
  })
}

export function useEmailDrafts(cid: string, enabled = true) {
  return useQuery({
    queryKey: emailDraftsKey(cid),
    queryFn: () => apiJson<EmailDraft[]>(`/api/conversations/${cid}/email-drafts`),
    enabled,
  })
}

export function useSendDraft(cid: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      apiJson<EmailDraft>(`/api/email/drafts/${id}/send`, { method: 'POST' }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: emailDraftsKey(cid) }),
  })
}

export function useCancelDraft(cid: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      apiJson<EmailDraft>(`/api/email/drafts/${id}/cancel`, { method: 'POST' }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: emailDraftsKey(cid) }),
  })
}
