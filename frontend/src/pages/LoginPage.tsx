import { useAuth } from '@/auth/AuthProvider'
import AuthForm from './AuthForm'

export default function LoginPage() {
  const { login } = useAuth()
  return (
    <AuthForm
      title="Welcome back"
      description="Sign in to continue to your chats"
      submitLabel="Sign in"
      footerText="Don't have an account?"
      footerLinkLabel="Create one"
      footerLinkTo="/register"
      onSubmit={login}
    />
  )
}
