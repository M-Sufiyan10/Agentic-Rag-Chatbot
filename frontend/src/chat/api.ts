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

export type ChatMessage = { role: 'user' | 'assistant'; content: string }

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
