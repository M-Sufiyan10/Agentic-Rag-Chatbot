/** Human-readable chip text for a tool call. */
export function toolLabel(name: string, status: 'running' | 'done'): string {
  const running = status === 'running'
  switch (name) {
    case 'web_search':
      return running ? 'Searching the web…' : 'Searched the web'
    case 'calculator':
      return running ? 'Calculating…' : 'Calculated'
    case 'current_datetime':
      return running ? 'Checking the date and time…' : 'Checked the date and time'
    case 'search_documents':
      return running ? 'Searching your documents…' : 'Searched your documents'
    default:
      return running ? `Calling ${name}…` : `Used ${name}`
  }
}
