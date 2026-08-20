import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DashboardPage } from './DashboardPage'
import { api } from '../lib/api'
import type { Order, Position, Quote, Strategy } from '../lib/types'

vi.mock('../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))
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
  last_blocked_reason: null,
  last_blocked_at: null,
}

const POSITION: Position = {
  symbol: 'AAPL',
  quantity: '10',
  avg_entry_price: '150',
  realized_pnl: '0',
  opened_at: null,
  strategy_id: null,
  current_price: null,
  market_value: null,
  unrealized_pnl: null,
  unrealized_pnl_pct: null,
  quote_time: null,
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
  filled_quantity: null,
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
  currency: 'USD',
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
    vi.clearAllMocks()
    vi.mocked(api.post).mockResolvedValue({} as never)
    vi.mocked(api.delete).mockResolvedValue(undefined as never)
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/watchlist') return [] as never
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

  it('saves a searched symbol to the account, not the browser', async () => {
    // It used to live in localStorage, so the list was empty on the phone and
    // gone after clearing browsing data.
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('150.25')
    await user.type(screen.getByLabelText('查詢代號'), 'tsla')
    await user.click(screen.getByRole('button', { name: '加入自選' }))

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/watchlist', { symbol: 'TSLA' }),
    )
  })

  it('removes a watched symbol through the API', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/watchlist')
        return [
          { id: 1, symbol: 'AAPL', data_source: 'yfinance', created_at: '2026-08-19T00:00:00Z' },
        ] as never
      if (path === '/api/strategies') return [] as never
      if (path === '/api/positions') return [] as never
      if (path.startsWith('/api/orders')) return [] as never
      return [QUOTE] as never
    })
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: '移除 AAPL' }))

    await waitFor(() => expect(api.delete).toHaveBeenCalledWith('/api/watchlist/AAPL'))
  })

  it('moves a list left over in the browser into the account, once', async () => {
    // Without this the owner's existing watch list simply vanishes on the
    // deploy that moves it into the database.
    localStorage.setItem('trading_app_watchlist', JSON.stringify(['2330.TW']))
    renderPage()

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/watchlist', { symbol: '2330.TW' }),
    )
    expect(localStorage.getItem('trading_app_watchlist')).toBeNull()
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

// --- picking a symbol previews it ------------------------------------------

describe('用中文找股票並確認是不是同一家', () => {
  it('選了候選就直接把圖表換過去，讓人在加入前先看一眼', async () => {
    // The point of previewing: the whole feature exists because the owner
    // searched by a Chinese name, and a returned number is not something they
    // can verify by reading it. The chart is.
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.startsWith('/api/symbols/search'))
        return {
          query: '台積電',
          matches: [
            {
              symbol: '2330.TW',
              name: '台積電',
              detail: '上市 · 台灣積體電路製造股份有限公司',
              market: '台股',
              data_source: 'yfinance',
              verified: true,
            },
          ],
          listings_generated_at: '2026-08-19',
        } as never
      if (path === '/api/watchlist') return [] as never
      return [] as never
    })
    const user = userEvent.setup()
    renderPage()

    await user.type(await screen.findByLabelText('查詢代號'), '台積電')
    const list = await screen.findByRole('listbox', { name: '搜尋結果' })
    await user.click(within(list).getByText('2330.TW'))

    // TradingView spells it TWSE:2330; the widget is what proves the whole
    // chain (search -> our symbol -> TradingView symbol) actually connects.
    // The container stamps the resolved symbol on itself -- see
    // TradingViewWidget's dataset.tvSymbol guard.
    await waitFor(() => {
      const container = document.querySelector('.tradingview-widget-container') as HTMLElement
      expect(container?.dataset.tvSymbol).toBe('TWSE:2330')
    })
  })
})

// --- a symbol going into a URL ----------------------------------------------
//
// Symbols were interpolated into paths and query strings raw. Everything the
// app can create today is ASCII, so it works -- but rows created before the
// symbol validation existed can hold 「台積電」, and a `#` or `&` in a query
// string does not fail, it silently truncates: `?symbols=A#B,AAPL` asks for
// one symbol and gets an answer that looks perfectly normal.
//
// SymbolInput already encodes its query. These are the paths that did not.

describe('代號進到網址裡', () => {
  const LEGACY = { id: 1, symbol: '台積電', data_source: 'yfinance', created_at: '2026-08-19T00:00:00Z' }
  const LEGACY_QUOTE: Quote = { ...QUOTE, symbol: '台積電' }

  function mockWith(watchlist: unknown[], quotes: Quote[]) {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/watchlist') return watchlist as never
      if (path === '/api/strategies') return [] as never
      if (path === '/api/positions') return [] as never
      if (path.startsWith('/api/orders')) return [] as never
      if (path.startsWith('/api/market/quote')) return quotes as never
      return [] as never
    })
  }

  function quoteUrl(): string | undefined {
    return vi
      .mocked(api.get)
      .mock.calls.map((call) => call[0] as string)
      .find((path) => path.startsWith('/api/market/quote'))
  }

  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    vi.mocked(api.post).mockResolvedValue({} as never)
    vi.mocked(api.delete).mockResolvedValue(undefined as never)
    mockWith([LEGACY], [LEGACY_QUOTE])
  })

  it('刪除一筆非 ASCII 的舊資料時要編碼', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: '移除 台積電' }))

    await waitFor(() =>
      expect(api.delete).toHaveBeenCalledWith(`/api/watchlist/${encodeURIComponent('台積電')}`),
    )
  })

  it('報價查詢的每個代號都要各自編碼，逗號才還是分隔符', async () => {
    renderPage()

    await waitFor(() =>
      expect(quoteUrl()).toBe(`/api/market/quote?symbols=${encodeURIComponent('台積電')}`),
    )
  })

  it('一般代號的網址不要被改得認不出來', async () => {
    mockWith([{ ...LEGACY, symbol: 'AAPL' }], [QUOTE])
    renderPage()

    await waitFor(() => expect(quoteUrl()).toBe('/api/market/quote?symbols=AAPL'))
  })

  it('多個代號還是用逗號分隔，不是被編碼成一整串', async () => {
    // encodeURIComponent escapes a comma, so encoding the joined string would
    // turn the separator into %2C and the backend would see one long symbol.
    mockWith(
      [
        { ...LEGACY, id: 1, symbol: 'AAPL' },
        { ...LEGACY, id: 2, symbol: '2330.TW' },
      ],
      [QUOTE],
    )
    renderPage()

    await waitFor(() => expect(quoteUrl()).toContain('AAPL,2330.TW'))
  })
})
