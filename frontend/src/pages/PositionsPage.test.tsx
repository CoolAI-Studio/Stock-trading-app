import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PositionsPage } from './PositionsPage'
import { ApiError, api } from '../lib/api'
import type { Position } from '../lib/types'

// The page reads ApiError to decide how to word a failure, so the mock has to
// carry the real class -- a stubbed one would never match `instanceof`.
vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

const POSITION: Position = {
  symbol: 'AAPL',
  quantity: '10',
  avg_entry_price: '150',
  realized_pnl: '25.50',
  opened_at: '2026-08-16T00:00:00Z',
  strategy_id: null,
  current_price: null,
  market_value: null,
  unrealized_pnl: null,
  unrealized_pnl_pct: null,
  quote_time: null,
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

  it("shows whose risk settings each position's stop-loss runs on", async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/positions') return [{ ...POSITION, strategy_id: 7 }] as never
      if (path === '/api/strategies') return [{ id: 7, name: 'ma5-cross' }] as never
      return [] as never
    })
    renderPage()

    const row = (await screen.findByText('AAPL')).closest('tr')
    expect(within(row as HTMLElement).getByText('ma5-cross')).toBeInTheDocument()
  })

  it('shows 全域 for a position no strategy opened', async () => {
    // Manual orders and TradingView fills are unattributed and run on the
    // global thresholds.
    renderPage()

    const row = (await screen.findByText('AAPL')).closest('tr')
    expect(within(row as HTMLElement).getByText('全域')).toBeInTheDocument()
  })

  it('explains that the strategy which opened a position keeps it', async () => {
    // The owner accepted first-opener-wins only on condition it is visible.
    renderPage()

    expect(await screen.findByText(/由誰先建立部位就跟誰/)).toBeInTheDocument()
  })
})

describe('what the position is worth now', () => {
  const VALUED: Position = {
    ...POSITION,
    quantity: '1000',
    avg_entry_price: '1000',
    current_price: '1050',
    market_value: '1050000',
    unrealized_pnl: '50000',
    unrealized_pnl_pct: '5',
    quote_time: '2026-08-19T01:30:00Z',
  }

  it('shows the live price and the unrealized gain', async () => {
    vi.mocked(api.get).mockResolvedValue([VALUED] as never)
    renderPage()

    const row = (await screen.findByText('AAPL')).closest('tr') as HTMLElement
    expect(within(row).getByTestId('current-price')).toHaveTextContent('1050')
    expect(within(row).getByTestId('unrealized')).toHaveTextContent('50000')
    expect(within(row).getByTestId('unrealized')).toHaveTextContent('5')
  })

  it('colours a loss differently from a gain', async () => {
    vi.mocked(api.get).mockResolvedValue([
      { ...VALUED, current_price: '900', unrealized_pnl: '-100000', unrealized_pnl_pct: '-10' },
    ] as never)
    renderPage()

    const row = (await screen.findByText('AAPL')).closest('tr') as HTMLElement
    expect(within(row).getByTestId('unrealized').className).toContain('red')
  })

  it('says the price has not arrived rather than showing a zero', async () => {
    // Zero would read as "flat" -- a much more reassuring statement than
    // "the price feed has not reached this symbol".
    vi.mocked(api.get).mockResolvedValue([POSITION] as never)
    renderPage()

    const row = (await screen.findByText('AAPL')).closest('tr') as HTMLElement
    expect(within(row).getByTestId('unrealized')).toHaveTextContent('—')
    expect(within(row).getByTestId('unrealized')).not.toHaveTextContent('0')
  })
})

describe('when an action fails', () => {
  it('says so instead of leaving the row looking untouched', async () => {
    vi.mocked(api.get).mockResolvedValue([POSITION] as never)
    vi.mocked(api.delete).mockRejectedValue(new ApiError(409, '這檔還有未確認的委託'))
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '出清' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('這檔還有未確認的委託')
  })
})
