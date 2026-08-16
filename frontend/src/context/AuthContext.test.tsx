import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from './AuthContext'
import { useAuth } from './useAuth'
import { api, getToken, setToken } from '../lib/api'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function Probe() {
  const { isAuthenticated, login, logout } = useAuth()
  return (
    <div>
      <span data-testid="status">{isAuthenticated ? 'in' : 'out'}</span>
      <button onClick={() => login('me@example.com', 'hunter2')}>login</button>
      <button onClick={logout}>logout</button>
    </div>
  )
}

describe('AuthContext', () => {
  beforeEach(() => {
    setToken(null)
    vi.stubGlobal('fetch', vi.fn())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    setToken(null)
  })

  it('starts unauthenticated when there is no stored token', () => {
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )
    expect(screen.getByTestId('status')).toHaveTextContent('out')
  })

  it('starts authenticated when a token is already stored', () => {
    setToken('existing-token')
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )
    expect(screen.getByTestId('status')).toHaveTextContent('in')
  })

  it('becomes authenticated after a successful login', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ access_token: 'fresh-token' }))
    const userEventInstance = userEvent.setup()

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )
    await userEventInstance.click(screen.getByText('login'))

    expect(screen.getByTestId('status')).toHaveTextContent('in')
    expect(getToken()).toBe('fresh-token')
  })

  it('clears the token on logout', async () => {
    setToken('existing-token')
    const userEventInstance = userEvent.setup()

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )
    await userEventInstance.click(screen.getByText('logout'))

    expect(screen.getByTestId('status')).toHaveTextContent('out')
    expect(getToken()).toBeNull()
  })

  it('drops to unauthenticated when a real API call gets a 401', async () => {
    setToken('stale-token')
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )
    expect(screen.getByTestId('status')).toHaveTextContent('in')

    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: 'expired' }, 401))
    await expect(api.get('/api/orders')).rejects.toThrow()

    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('out'))
  })
})
