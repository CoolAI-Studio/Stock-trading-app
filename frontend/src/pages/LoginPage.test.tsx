import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LoginPage } from './LoginPage'
import { AuthProvider } from '../context/AuthContext'
import { setToken } from '../lib/api'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function renderLoginPage() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<div>home page</div>} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  )
}

describe('LoginPage', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    setToken(null)
  })

  it('navigates to / after a successful login', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ access_token: 'tok' }))
    const user = userEvent.setup()
    renderLoginPage()

    await user.type(screen.getByLabelText('電子信箱'), 'me@example.com')
    await user.type(screen.getByLabelText('密碼'), 'hunter2')
    await user.click(screen.getByRole('button', { name: '登入' }))

    await waitFor(() => expect(screen.getByText('home page')).toBeInTheDocument())
  })

  it('shows an error message on invalid credentials', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: 'Incorrect email or password' }, 401))
    const user = userEvent.setup()
    renderLoginPage()

    await user.type(screen.getByLabelText('電子信箱'), 'me@example.com')
    await user.type(screen.getByLabelText('密碼'), 'wrong')
    await user.click(screen.getByRole('button', { name: '登入' }))

    expect(await screen.findByText('Incorrect email or password')).toBeInTheDocument()
  })
})
