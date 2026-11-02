import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { api, configureAuth } from './api'
import { AuthContext } from './auth-context'
import { readStoredToken, storeToken } from './token'

// The API client reads the token from here. A plain variable (not React state)
// is updated synchronously, so even the very first request of a page has it.
let currentToken: string | null = readStoredToken()

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(currentToken)

  const save = useCallback((value: string | null) => {
    currentToken = value
    setToken(value)
    storeToken(value)
  }, [])

  const logout = useCallback(() => save(null), [save])

  // Any 401 from the API (expired or invalid token) signs the user out.
  configureAuth(() => currentToken, logout)

  const login = useCallback(
    async (email: string, password: string) => {
      const { access_token } = await api.login(email, password)
      save(access_token)
    },
    [save],
  )

  const register = useCallback(
    async (email: string, password: string) => {
      await api.register(email, password)
      await login(email, password)
    },
    [login],
  )

  // "Try the demo": a throwaway account with sample data, deleted after 24 hours.
  const startDemo = useCallback(async () => {
    const { access_token } = await api.startDemo()
    save(access_token)
  }, [save])

  const value = useMemo(
    () => ({ token, login, register, startDemo, logout }),
    [token, login, register, startDemo, logout],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
