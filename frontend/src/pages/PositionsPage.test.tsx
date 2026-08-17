import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PositionsPage } from './PositionsPage'
import { api } from '../lib/api'
import type { Position } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: { get: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

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
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.get).mockResolvedValue([POSITION] as never)
  })

  it('lists open positions', async () => {
    renderPage()

    expect(await screen.findByText('AAPL')).toBeInTheDocument()
    expect(screen.getByText('150')).toBeInTheDocument()
    expect(screen.getByText('25.50')).toBeInTheDocument()
  })

  it('shows an empty state with no positions', async () => {
    vi.mocked(api.get).mockResolvedValue([] as never)
    renderPage()

    expect(await screen.findByText('目前沒有持有部位。')).toBeInTheDocument()
  })

  it('adjusts a position quantity and avg entry price', async () => {
    vi.mocked(api.patch).mockResolvedValue({ ...POSITION, quantity: '20' } as never)
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('AAPL')
    await user.click(screen.getByRole('button', { name: '調整' }))

    const qtyInput = screen.getByLabelText('數量')
    await user.clear(qtyInput)
    await user.type(qtyInput, '20')
    await user.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith('/api/positions/AAPL', {
        quantity: '20',
        avg_entry_price: '150',
      }),
    )
  })

  it('flattens a position after confirming', async () => {
    vi.mocked(api.delete).mockResolvedValue(undefined as never)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('AAPL')
    await user.click(screen.getByRole('button', { name: '出清' }))

    await waitFor(() => expect(api.delete).toHaveBeenCalledWith('/api/positions/AAPL'))
  })

  it('creates a new position', async () => {
    vi.mocked(api.patch).mockResolvedValue(POSITION as never)
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('AAPL')
    await user.click(screen.getByRole('button', { name: '新增部位' }))
    await user.type(screen.getByLabelText('代號'), 'tsla')
    await user.type(screen.getByLabelText('數量'), '5')
    await user.type(screen.getByLabelText('平均成本'), '200')
    await user.click(screen.getByRole('button', { name: '建立' }))

    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith('/api/positions/TSLA', {
        quantity: '5',
        avg_entry_price: '200',
      }),
    )
  })
})
