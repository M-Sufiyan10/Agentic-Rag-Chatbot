import type { EmailSecurity } from './api'

export type EmailPreset = {
  id: 'gmail' | 'outlook' | 'local'
  label: string
  imap_host: string
  imap_port: number
  imap_security: EmailSecurity
  smtp_host: string
  smtp_port: number
  smtp_security: EmailSecurity
}

export const PRESETS: EmailPreset[] = [
  {
    id: 'gmail',
    label: 'Gmail',
    imap_host: 'imap.gmail.com',
    imap_port: 993,
    imap_security: 'ssl',
    smtp_host: 'smtp.gmail.com',
    smtp_port: 465,
    smtp_security: 'ssl',
  },
  {
    id: 'outlook',
    label: 'Outlook',
    imap_host: 'outlook.office365.com',
    imap_port: 993,
    imap_security: 'ssl',
    smtp_host: 'smtp.office365.com',
    smtp_port: 587,
    smtp_security: 'starttls',
  },
  {
    id: 'local',
    label: 'Local test server',
    imap_host: 'greenmail',
    imap_port: 3143,
    imap_security: 'none',
    smtp_host: 'greenmail',
    smtp_port: 3025,
    smtp_security: 'none',
  },
]
