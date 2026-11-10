import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router'
import { AuthProvider, useAuth } from '@/auth/AuthProvider'
import RequireAuth from '@/auth/RequireAuth'
import { Button } from '@/components/ui/button'
import LoginPage from '@/pages/LoginPage'
import RegisterPage from '@/pages/RegisterPage'

const queryClient = new QueryClient()

/** Placeholder home; Task 9 replaces this with the chat UI. */
function Home() {
  const { user, logout } = useAuth()
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4">
      <p className="text-muted-foreground">Signed in as {user?.email}</p>
      <Button variant="outline" onClick={() => void logout()}>
        Sign out
      </Button>
    </main>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
            <Route element={<RequireAuth />}>
              <Route path="/" element={<Home />} />
            </Route>
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
