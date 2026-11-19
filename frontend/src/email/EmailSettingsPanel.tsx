import { useState } from 'react'
import type { FormEvent } from 'react'
import { AlertCircle, CheckCircle2, Loader2 } from 'lucide-react'
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
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import {
  useDeleteEmailAccount,
  useEmailAccount,
  useSaveEmailAccount,
  useTestEmailAccount,
} from './api'
import type { EmailAccount, EmailAccountInput, EmailSecurity } from './api'
import { PRESETS } from './presets'

type Props = {
  open: boolean
  onOpenChange(open: boolean): void
}

type FormState = {
  address: string
  username: string
  password: string
  imap_host: string
  imap_port: string
  imap_security: EmailSecurity
  smtp_host: string
  smtp_port: string
  smtp_security: EmailSecurity
}

function initialState(a: EmailAccount | null): FormState {
  return {
    address: a?.address ?? '',
    username: a?.username ?? '',
    password: '',
    imap_host: a?.imap_host ?? '',
    imap_port: String(a?.imap_port ?? 993),
    imap_security: a?.imap_security ?? 'ssl',
    smtp_host: a?.smtp_host ?? '',
    smtp_port: String(a?.smtp_port ?? 465),
    smtp_security: a?.smtp_security ?? 'ssl',
  }
}

function toInput(f: FormState): EmailAccountInput {
  const address = f.address.trim()
  const input: EmailAccountInput = {
    address,
    username: f.username.trim() || address,
    imap_host: f.imap_host.trim(),
    imap_port: Number(f.imap_port),
    imap_security: f.imap_security,
    smtp_host: f.smtp_host.trim(),
    smtp_port: Number(f.smtp_port),
    smtp_security: f.smtp_security,
  }
  if (f.password) input.password = f.password
  return input
}

function errorText(err: unknown, fallback: string): string {
  if (!(err instanceof ApiError)) return fallback
  // Pydantic validation errors arrive as a JSON array, not a sentence.
  if (err.detail.startsWith('[') || err.detail.startsWith('{')) {
    return 'Check the highlighted details and try again.'
  }
  return err.detail || fallback
}

/** Right-hand sheet to connect, test and disconnect the user's mailbox. */
export default function EmailSettingsPanel({ open, onOpenChange }: Props) {
  const account = useEmailAccount(open)

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Email</SheetTitle>
          <SheetDescription>
            Connect a mailbox so the assistant can read mail and draft replies. It never sends
            anything until you approve it.
          </SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
          {account.isPending && (
            <p className="py-2 text-sm text-muted-foreground">Loading email settings…</p>
          )}
          {account.isError && (
            <p className="py-2 text-sm text-muted-foreground">Couldn't load email settings.</p>
          )}
          {account.data === 'disabled' && (
            <p className="py-2 text-sm text-muted-foreground">
              Email isn't enabled on this server.
            </p>
          )}
          {account.data !== undefined && account.data !== 'disabled' && (
            <AccountForm key={account.data?.address ?? 'new'} account={account.data} />
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}

function Notice({ tone, children }: { tone: 'error' | 'ok'; children: React.ReactNode }) {
  const Icon = tone === 'ok' ? CheckCircle2 : AlertCircle
  return (
    <div
      role={tone === 'ok' ? 'status' : 'alert'}
      className="flex items-start gap-2.5 rounded-xl border px-3 py-2.5 text-sm"
    >
      <Icon
        className={`mt-0.5 size-4 shrink-0 ${tone === 'ok' ? 'text-green-600' : 'text-destructive'}`}
      />
      <p className="min-w-0 flex-1">{children}</p>
    </div>
  )
}

function AccountForm({ account }: { account: EmailAccount | null }) {
  const [form, setForm] = useState<FormState>(() => initialState(account))
  const [confirming, setConfirming] = useState(false)
  const [saved, setSaved] = useState(false)
  const save = useSaveEmailAccount()
  const test = useTestEmailAccount()
  const remove = useDeleteEmailAccount()

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }))
    setSaved(false)
  }

  function applyPreset(id: string) {
    const p = PRESETS.find((x) => x.id === id)
    if (!p) return
    setForm((f) => ({
      ...f,
      imap_host: p.imap_host,
      imap_port: String(p.imap_port),
      imap_security: p.imap_security,
      smtp_host: p.smtp_host,
      smtp_port: String(p.smtp_port),
      smtp_security: p.smtp_security,
    }))
    setSaved(false)
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    test.reset()
    setSaved(false)
    save.mutate(toInput(form), { onSuccess: () => setSaved(true) })
  }

  function onTest() {
    save.reset()
    setSaved(false)
    test.mutate(toInput(form))
  }

  function confirmDisconnect() {
    setConfirming(false)
    remove.mutate()
  }

  const hasPassword = account?.has_password === true
  const busy = save.isPending || test.isPending || remove.isPending

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2" role="group" aria-label="Presets">
        {PRESETS.map((p) => (
          <Button
            key={p.id}
            type="button"
            variant="outline"
            size="sm"
            onClick={() => applyPreset(p.id)}
          >
            {p.label}
          </Button>
        ))}
      </div>

      <Field id="email-address" label="Email address">
        <Input
          id="email-address"
          type="email"
          required
          value={form.address}
          onChange={(e) => set('address', e.target.value)}
        />
      </Field>
      <Field id="email-username" label="Username">
        <Input
          id="email-username"
          placeholder="Same as the address"
          value={form.username}
          onChange={(e) => set('username', e.target.value)}
        />
      </Field>
      <Field id="email-password" label="App password">
        <Input
          id="email-password"
          type="password"
          autoComplete="new-password"
          placeholder={hasPassword ? '•••••• saved' : ''}
          value={form.password}
          onChange={(e) => set('password', e.target.value)}
        />
        <p className="text-xs text-muted-foreground">
          Use an app password, not your normal password. Gmail: Google Account, Security, App
          passwords. Outlook: Microsoft account, Security, App passwords.
        </p>
      </Field>

      <ServerFields
        title="IMAP (reading)"
        prefix="IMAP"
        host={form.imap_host}
        port={form.imap_port}
        security={form.imap_security}
        onHost={(v) => set('imap_host', v)}
        onPort={(v) => set('imap_port', v)}
        onSecurity={(v) => set('imap_security', v)}
      />
      <ServerFields
        title="SMTP (sending)"
        prefix="SMTP"
        host={form.smtp_host}
        port={form.smtp_port}
        security={form.smtp_security}
        onHost={(v) => set('smtp_host', v)}
        onPort={(v) => set('smtp_port', v)}
        onSecurity={(v) => set('smtp_security', v)}
      />

      {test.data?.ok && <Notice tone="ok">Connection works</Notice>}
      {test.data && !test.data.ok && (
        <Notice tone="error">{test.data.error || "Couldn't connect."}</Notice>
      )}
      {test.isError && (
        <Notice tone="error">{errorText(test.error, "Couldn't test the connection.")}</Notice>
      )}
      {saved && <Notice tone="ok">Saved</Notice>}
      {save.isError && <Notice tone="error">{errorText(save.error, "Couldn't save.")}</Notice>}
      {remove.isError && (
        <Notice tone="error">{errorText(remove.error, "Couldn't disconnect.")}</Notice>
      )}

      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" disabled={busy} onClick={onTest}>
          {test.isPending && <Loader2 className="animate-spin" />}
          Test connection
        </Button>
        <Button type="submit" disabled={busy}>
          {save.isPending && <Loader2 className="animate-spin" />}
          Save
        </Button>
        {account && (
          <Button
            type="button"
            variant="ghost"
            className="text-destructive"
            disabled={busy}
            onClick={() => setConfirming(true)}
          >
            Disconnect
          </Button>
        )}
      </div>

      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Disconnect email?</AlertDialogTitle>
            <AlertDialogDescription>
              The saved mailbox and password will be deleted. The assistant won't be able to read or
              draft email until you connect again.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={confirmDisconnect}>
              Disconnect
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </form>
  )
}

function Field({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
    </div>
  )
}

type ServerProps = {
  title: string
  prefix: 'IMAP' | 'SMTP'
  host: string
  port: string
  security: EmailSecurity
  onHost(v: string): void
  onPort(v: string): void
  onSecurity(v: EmailSecurity): void
}

function ServerFields({
  title,
  prefix,
  host,
  port,
  security,
  onHost,
  onPort,
  onSecurity,
}: ServerProps) {
  const id = prefix.toLowerCase()
  return (
    <fieldset className="flex flex-col gap-2 rounded-xl border p-3">
      <legend className="px-1 text-xs font-medium text-muted-foreground">{title}</legend>
      <Field id={`${id}-host`} label={`${prefix} host`}>
        <Input id={`${id}-host`} required value={host} onChange={(e) => onHost(e.target.value)} />
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field id={`${id}-port`} label={`${prefix} port`}>
          <Input
            id={`${id}-port`}
            type="number"
            required
            min={1}
            max={65535}
            value={port}
            onChange={(e) => onPort(e.target.value)}
          />
        </Field>
        <Field id={`${id}-security`} label={`${prefix} security`}>
          <select
            id={`${id}-security`}
            className="h-9 rounded-md border border-input bg-transparent px-2 text-sm"
            value={security}
            onChange={(e) => onSecurity(e.target.value as EmailSecurity)}
          >
            <option value="ssl">SSL/TLS</option>
            <option value="starttls">STARTTLS</option>
            <option value="none">None</option>
          </select>
        </Field>
      </div>
    </fieldset>
  )
}
