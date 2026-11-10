import { useAuth } from '@/auth/AuthProvider'
import AuthForm from './AuthForm'

export default function RegisterPage() {
  const { register } = useAuth()
  return (
    <AuthForm
      title="Create your account"
      description="Start chatting in a few seconds"
      submitLabel="Create account"
      footerText="Already have an account?"
      footerLinkLabel="Sign in"
      footerLinkTo="/login"
      minPasswordLength={8}
      onSubmit={register}
    />
  )
}
