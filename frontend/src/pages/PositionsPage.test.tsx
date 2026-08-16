import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PositionsPage } from './PositionsPage'
import { api } from '../lib/api'
import type { Position } from '../lib/types'

vi.mock('../lib/api', () => ({ api: { get: vi.fn() } }))

const POSITION: Position = {
  symbol: 'AAPL',
  quantity: '10',
  avg_entry_price: '150',
  realized_pnl: '25.50',
  opened_at: '2026-08-16T00:00:00Z',
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <PositionsPage />
    </QueryClientProvider>,
  )
}

describe('PositionsPage', () => {
  it('lists open positions', async () => {
    vi.mocked(api.get).mockResolvedValue([POSITION] as never)
    renderPage()

    expect(await screen.findByText('AAPL')).toBeInTheDocument()
    expect(screen.getByText('150')).toBeInTheDocument()
    expect(screen.getByText('25.50')).toBeInTheDocument()
  })

  it('shows an empty state with no positions', async () => {
    vi.mocked(api.get).mockResolvedValue([] as never)
    renderPage()

    expect(await screen.findByText(/no open positions/i)).toBeInTheDocument()
  })
})
