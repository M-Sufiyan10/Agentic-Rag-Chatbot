export type User = { id: string; email: string }

/** Body returned by register, login and refresh. */
export type AuthResponse = {
  access_token: string
  token_type: 'bearer'
  user: User
}
