import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { ApiError } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

type Props = {
  title: string
  description: string
  submitLabel: string
  footerText: string
  footerLinkLabel: string
  footerLinkTo: string
  minPasswordLength?: number
  onSubmit(email: string, password: string): Promise<void>
}

/** Shared card layout and form behaviour for the login and register pages. */
export default function AuthForm(props: Props) {
  const { minPasswordLength = 0 } = props
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (password.length < minPasswordLength) {
      setError(`Password must be at least ${minPasswordLength} characters`)
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      await props.onSubmit(email, password)
      void navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Something went wrong. Please try again.')
      setSubmitting(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-gradient-to-br from-muted via-background to-muted p-4">
      <Card className="w-full max-w-sm shadow-lg">
        <CardHeader className="text-center">
          <CardTitle className="text-2xl">{props.title}</CardTitle>
          <CardDescription>{props.description}</CardDescription>
        </CardHeader>
        <form onSubmit={handleSubmit} noValidate>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                placeholder="you@example.com"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete={minPasswordLength ? 'new-password' : 'current-password'}
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
          </CardContent>
          <CardFooter className="flex-col gap-4 pt-6">
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? 'Please wait...' : props.submitLabel}
            </Button>
            <p className="text-sm text-muted-foreground">
              {props.footerText}{' '}
              <Link to={props.footerLinkTo} className="font-medium text-foreground underline-offset-4 hover:underline">
                {props.footerLinkLabel}
              </Link>
            </p>
          </CardFooter>
        </form>
      </Card>
    </main>
  )
}
