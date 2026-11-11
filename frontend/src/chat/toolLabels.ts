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
    default:
      return running ? `Calling ${name}…` : `Used ${name}`
  }
}
