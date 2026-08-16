import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import { ProtectedRoute } from './ProtectedRoute'
import { AuthProvider } from '../context/AuthContext'
import { setToken } from '../lib/api'

function renderAt(path: string) {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/login" element={<div>login page</div>} />
          <Route element={<ProtectedRoute />}>
            <Route path="/dashboard" element={<div>dashboard page</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  )
}

describe('ProtectedRoute', () => {
  afterEach(() => setToken(null))

  it('redirects to /login when not authenticated', () => {
    renderAt('/dashboard')
    expect(screen.getByText('login page')).toBeInTheDocument()
  })

  it('renders the protected content when authenticated', () => {
    setToken('a-token')
    renderAt('/dashboard')
    expect(screen.getByText('dashboard page')).toBeInTheDocument()
  })
})
