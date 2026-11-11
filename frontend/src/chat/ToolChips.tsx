import { Check, Loader2 } from 'lucide-react'
import { toolLabel } from './toolLabels'
import type { ToolActivity } from './useChatStream'

const chipClass =
  'inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground'

type Props = { tools: ToolActivity[]; names?: undefined } | { names: string[]; tools?: undefined }

/**
 * Tool-status chips. `tools` are live activities (expandable details);
 * `names` are tools remembered from history (done, no details).
 */
export default function ToolChips(props: Props) {
  const items = props.tools
    ? props.tools.map((t) => <LiveChip key={t.id} tool={t} />)
    : props.names.map((name, i) => (
        <span key={i} role="status" className={chipClass}>
          <Check className="size-3" aria-hidden />
          {toolLabel(name, 'done')}
        </span>
      ))
  if (items.length === 0) return null
  return <div className="mt-2 flex flex-col items-start gap-1.5">{items}</div>
}

function LiveChip({ tool }: { tool: ToolActivity }) {
  const label = toolLabel(tool.name, tool.status)
  return (
    <details
      role="status"
      aria-label={tool.status === 'running' ? label : undefined}
      className="max-w-full"
    >
      <summary className={`${chipClass} cursor-pointer list-none select-none`}>
        {tool.status === 'running' ? (
          <Loader2 className="size-3 animate-spin" aria-hidden />
        ) : (
          <Check className="size-3" aria-hidden />
        )}
        {label}
      </summary>
      <div className="mt-1.5 space-y-1.5 rounded-lg border bg-background p-2.5 text-xs">
        <p className="font-medium">Input</p>
        <pre className="overflow-x-auto whitespace-pre-wrap break-words">
          {JSON.stringify(tool.input, null, 2)}
        </pre>
        {tool.output !== undefined && (
          <>
            <p className="font-medium">Output</p>
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words">
              {tool.output}
            </pre>
          </>
        )}
      </div>
    </details>
  )
}
