import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { Layout } from './Layout'
import { useWebSocket } from '../lib/useWebSocket'

vi.mock('../lib/useWebSocket', () => ({ useWebSocket: vi.fn() }))
vi.mock('../context/useAuth', () => ({ useAuth: () => ({ logout: vi.fn() }) }))
// The layout now carries the worker-health banner, which is a query.
vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn().mockResolvedValue({ status: 'ok', checks: {} }) },
}))

function renderLayout() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Layout />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Layout', () => {
  it('opens the live connection for every signed-in page, not just the dashboard', () => {
    renderLayout()
    expect(useWebSocket).toHaveBeenCalledWith(true)
  })

  it('renders the navigation', () => {
    renderLayout()
    expect(screen.getByText('儀表板')).toBeInTheDocument()
    expect(screen.getByText('訂單')).toBeInTheDocument()
  })
})
