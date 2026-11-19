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
    case 'get_weather':
      return running ? 'Checking the weather…' : 'Checked the weather'
    case 'convert_units':
      return running ? 'Converting units…' : 'Converted units'
    case 'convert_time':
      return running ? 'Converting time zones…' : 'Converted time zones'
    case 'fetch':
      return running ? 'Reading the web page…' : 'Read the web page'
    case 'list_emails':
      return running ? 'Checking your inbox…' : 'Checked your inbox'
    case 'read_email':
      return running ? 'Reading an email…' : 'Read an email'
    case 'draft_email':
      return running ? 'Drafting an email…' : 'Drafted an email'
    default:
      return running ? `Calling ${name}…` : `Used ${name}`
  }
}
