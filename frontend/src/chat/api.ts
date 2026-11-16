/**
 * Conversation and message API calls, plus the TanStack Query hooks and query
 * keys the chat UI uses. Keys:
 *   - ["conversations"]       the sidebar list
 *   - ["messages", id]        one conversation's transcript
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiJson } from '@/api/client'

export type Conversation = {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export type Citation = {
  document_id: string
  filename: string
  chunk_index: number
  snippet: string
}

export type FeedbackValue = 'up' | 'down'

export type ChatMessage = {
  /** Turn id of a saved assistant reply; null/absent for user messages. */
  id?: string | null
  feedback?: FeedbackValue | null
  role: 'user' | 'assistant'
  content: string
  tools?: string[]
  citations?: Citation[]
}

export const conversationsKey = ['conversations'] as const
export const messagesKey = (id: string) => ['messages', id] as const

export function listConversations(): Promise<Conversation[]> {
  return apiJson<Conversation[]>('/api/conversations')
}

export function createConversation(): Promise<Conversation> {
  return apiJson<Conversation>('/api/conversations', { method: 'POST', body: JSON.stringify({}) })
}

export function renameConversation(id: string, title: string): Promise<Conversation> {
  return apiJson<Conversation>(`/api/conversations/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  })
}

export function deleteConversation(id: string): Promise<void> {
  return apiJson<void>(`/api/conversations/${id}`, { method: 'DELETE' })
}

export function getMessages(id: string): Promise<ChatMessage[]> {
  return apiJson<ChatMessage[]>(`/api/conversations/${id}/messages`)
}

export function setFeedback(
  conversationId: string,
  turnId: string,
  value: FeedbackValue,
): Promise<{ value: FeedbackValue }> {
  return apiJson<{ value: FeedbackValue }>(
    `/api/conversations/${conversationId}/messages/${turnId}/feedback`,
    { method: 'PUT', body: JSON.stringify({ value }) },
  )
}

export function clearFeedback(conversationId: string, turnId: string): Promise<void> {
  return apiJson<void>(`/api/conversations/${conversationId}/messages/${turnId}/feedback`, {
    method: 'DELETE',
  })
}

/** Vote on a turn (`null` clears it). Updates the transcript cache optimistically. */
export function useFeedback(conversationId: string) {
  const queryClient = useQueryClient()
  const key = messagesKey(conversationId)
  return useMutation({
    mutationFn: ({ turnId, value }: { turnId: string; value: FeedbackValue | null }): Promise<unknown> =>
      value === null ? clearFeedback(conversationId, turnId) : setFeedback(conversationId, turnId, value),
    onMutate: async ({ turnId, value }) => {
      await queryClient.cancelQueries({ queryKey: key })
      const previous = queryClient.getQueryData<ChatMessage[]>(key)
      queryClient.setQueryData<ChatMessage[]>(key, (old) =>
        old?.map((m) => (m.id === turnId ? { ...m, feedback: value } : m)),
      )
      return { previous }
    },
    onError: (_err, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(key, context.previous)
    },
  })
}

export function useConversations() {
  return useQuery({ queryKey: conversationsKey, queryFn: listConversations })
}

export function useMessages(id: string) {
  return useQuery({ queryKey: messagesKey(id), queryFn: () => getMessages(id) })
}

export function useCreateConversation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: createConversation,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: conversationsKey }),
  })
}

export function useRenameConversation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => renameConversation(id, title),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: conversationsKey }),
  })
}

export function useDeleteConversation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: deleteConversation,
    onSuccess: (_data, id) => {
      queryClient.removeQueries({ queryKey: messagesKey(id) })
      return queryClient.invalidateQueries({ queryKey: conversationsKey })
    },
  })
}
