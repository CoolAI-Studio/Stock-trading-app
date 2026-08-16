import { useEffect, useState, type ReactNode } from 'react'
import { getToken, login as apiLogin, setToken, setUnauthorizedHandler } from '../lib/api'
import { AuthContext } from './authContextInstance'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(() => getToken())

  useEffect(() => {
    setUnauthorizedHandler(() => setTokenState(null))
  }, [])

  async function login(email: string, password: string): Promise<void> {
    const newToken = await apiLogin(email, password)
    setTokenState(newToken)
  }

  function logout(): void {
    setToken(null)
    setTokenState(null)
  }

  return (
    <AuthContext.Provider value={{ isAuthenticated: token !== null, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}
