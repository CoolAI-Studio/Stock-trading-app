import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DashboardPage } from './DashboardPage'
import { api } from '../lib/api'
import type { Order, Position, Quote, Strategy } from '../lib/types'

vi.mock('../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }))
vi.mock('../lib/useWebSocket', () => ({ useWebSocket: vi.fn() }))

// Inherits every risk knob from the global settings, like every strategy
// written before overrides existed.
const NO_OVERRIDES = {
  capital: null,
  stop_loss_pct: null,
  take_profit_pct: null,
  max_position_qty: null,
  max_order_notional: null,
  max_pending_orders_per_symbol: null,
  signal_cooldown_sec: null,
  alert_interval_sec: null,
}

const STRATEGY: Strategy = {
  ...NO_OVERRIDES,
  id: 1,
  name: 's',
  symbol: 'AAPL',
  data_source: 'yfinance',
  is_active: true,
  alert_only: false,
  default_quantity: '1',
  warmup_bars: 30,
  last_signal: null,
  last_signal_at: null,
  last_run_at: null,
  last_error: null,
  consecutive_errors: 0,
}

const POSITION: Position = {
  symbol: 'AAPL',
  quantity: '10',
  avg_entry_price: '150',
  realized_pnl: '0',
  opened_at: null,
  strategy_id: null,
}

const PENDING_ORDER: Order = {
  id: 1,
  strategy_id: null,
  source: 'manual',
  symbol: 'AAPL',
  side: 'buy',
  quantity: '1',
  signal_price: null,
  status: 'pending',
  risk_notes: null,
  reject_reason: null,
  fill_price: null,
  filled_at: null,
  decided_at: null,
  broker_ref: null,
  created_at: '2026-08-16T00:00:00Z',
}

const QUOTE: Quote = {
  symbol: 'AAPL',
  data_source: 'yfinance',
  price: '150.25',
  prev_close: '149',
  change_pct: '0.84',
  volume: null,
  quote_time: null,
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <DashboardPage />
    </QueryClientProvider>,
  )
}

describe('DashboardPage', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/strategies') return [STRATEGY] as never
      if (path === '/api/positions') return [POSITION] as never
      if (path.startsWith('/api/orders')) return [PENDING_ORDER] as never
      if (path.startsWith('/api/market/quote')) return [QUOTE] as never
      return [] as never
    })
  })

  it('shows summary stat counts', async () => {
    renderPage()
    const pendingCard = (await screen.findByText('待確認訂單')).closest('div')!
    expect(await within(pendingCard).findByText('1')).toBeInTheDocument()

    const positionsCard = screen.getByText('持有部位').closest('div')!
    expect(await within(positionsCard).findByText('1')).toBeInTheDocument()
  })

  it('lists live quotes for watched symbols', async () => {
    renderPage()
    expect(await screen.findByText('150.25')).toBeInTheDocument()
  })

  it('adds a searched symbol to the watchlist and queries its quote', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('150.25')
    await user.type(screen.getByLabelText('查詢代號'), 'tsla')
    await user.click(screen.getByRole('button', { name: '加入自選' }))

    expect(api.get).toHaveBeenCalledWith(expect.stringContaining('TSLA'))
    expect(JSON.parse(localStorage.getItem('trading_app_watchlist')!)).toEqual(['TSLA'])
  })

  it('removes a watchlist symbol', async () => {
    localStorage.setItem('trading_app_watchlist', JSON.stringify(['AAPL']))
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('150.25')
    await user.click(screen.getByRole('button', { name: '移除 AAPL' }))

    expect(JSON.parse(localStorage.getItem('trading_app_watchlist')!)).toEqual([])
  })


  it('surfaces a backend failure instead of rendering it as an empty account', async () => {
    // Regression: with every query failing, the page used to fall back to
    // `?? 0` / `?? []` and render "0 待確認訂單" -- indistinguishable from a
    // quiet account, so a pending order could sit unnoticed until it expired.
    vi.mocked(api.get).mockRejectedValue(new Error('Service Unavailable'))
    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('無法讀取資料')
  })
})
