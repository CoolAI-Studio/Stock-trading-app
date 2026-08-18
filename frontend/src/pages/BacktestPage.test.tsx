import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BacktestPage } from './BacktestPage'
import { ApiError, api } from '../lib/api'
import type {
  BacktestAssumptions,
  BacktestResult,
  BacktestRun,
  BacktestRunDetail,
  BacktestSummary,
  BacktestTrade,
  EquityPoint,
  Strategy,
} from '../lib/types'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

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
  name: 'ma5-cross',
  symbol: '2330.TW',
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

const ASSUMPTIONS: BacktestAssumptions = {
  fill_price_basis: 'next_open',
  commission_rate: '0.001425',
  slippage_rate: '0.0005',
  sell_tax_rate: '0.003',
  quantity: '100',
  initial_capital: '100000',
}

const SUMMARY: BacktestSummary = {
  bars_total: 260,
  bars_tested: 230,
  signals: 6,
  skipped_signals: 1,
  unfilled_signals: 0,
  trade_count: 2,
  wins: 1,
  losses: 1,
  win_rate_pct: '50',
  average_win: '820.5',
  average_loss: '-310.25',
  net_pnl: '510.25',
  total_costs: '618.4',
  total_return_pct: '0.5103',
  max_drawdown_pct: '3.2145',
  final_equity: '100510.25',
  open_quantity: '0',
  open_avg_entry_price: '0',
}

const TRADES: BacktestTrade[] = [
  {
    opened_at: '2026-01-08T00:00:00Z',
    closed_at: '2026-01-13T00:00:00Z',
    quantity: '100',
    entry_price: '103.45',
    exit_price: '111.655',
    pnl: '820.5',
    return_pct: '7.9313',
  },
  {
    opened_at: '2026-01-20T00:00:00Z',
    closed_at: '2026-01-27T00:00:00Z',
    quantity: '100',
    entry_price: '115.2',
    exit_price: '112.0975',
    pnl: '-310.25',
    return_pct: '-2.6931',
  },
]

const EQUITY_CURVE: EquityPoint[] = [
  { timestamp: '2026-01-05T00:00:00Z', close: '101', position_qty: '0', cash: '100000', equity: '100000' },
  { timestamp: '2026-01-13T00:00:00Z', close: '111', position_qty: '0', cash: '100820.5', equity: '100820.5' },
  { timestamp: '2026-01-27T00:00:00Z', close: '112', position_qty: '0', cash: '100510.25', equity: '100510.25' },
]

const RESULT: BacktestResult = {
  strategy_name: 'ma5-cross',
  symbol: '2330.TW',
  timeframe: '1d',
  entry_point: 'on_bar',
  warmup_bars: 30,
  first_bar_at: '2026-01-05T00:00:00Z',
  last_bar_at: '2026-01-27T00:00:00Z',
  assumptions: ASSUMPTIONS,
  assumption_notes: ['手續費：單邊 0.1425%，買進與賣出各收一次。'],
  notes: ['有 1 次買進訊號因為已有部位而略過（本回測一次只持有一個部位，不加碼）。'],
  trades: TRADES,
  equity_curve: EQUITY_CURVE,
  summary: SUMMARY,
}

const RUN: BacktestRunDetail = {
  id: 11,
  strategy_id: 1,
  strategy_name: 'ma5-cross',
  symbol: '2330.TW',
  timeframe: '1d',
  data_source: 'yfinance',
  range_start: '2025-03-01T00:00:00Z',
  range_end: '2026-03-01T23:59:59Z',
  created_at: '2026-03-02T01:00:00Z',
  assumptions: ASSUMPTIONS,
  summary: SUMMARY,
  source_code: 'class Strategy:\n    pass\n',
  code_hash: 'abc123',
  result: RESULT,
}

/** A run the engine completed but that never reached a single testable
 * candle: the 201 shape the owner meets when the symbol has no history, the
 * range is empty, or warm-up ate everything. */
function emptyRun(note: string): BacktestRunDetail {
  return {
    ...RUN,
    summary: { ...SUMMARY, bars_total: 0, bars_tested: 0, trade_count: 0, wins: 0, losses: 0 },
    result: {
      ...RESULT,
      notes: [note],
      trades: [],
      equity_curve: [],
      summary: { ...SUMMARY, bars_total: 0, bars_tested: 0, trade_count: 0, wins: 0, losses: 0 },
    },
  }
}

const HISTORY_ROW: BacktestRun = {
  id: 11,
  strategy_id: 1,
  strategy_name: 'ma5-cross',
  symbol: '2330.TW',
  timeframe: '1d',
  data_source: 'yfinance',
  range_start: '2025-03-01T00:00:00Z',
  range_end: '2026-03-01T23:59:59Z',
  created_at: '2026-03-02T01:00:00Z',
  assumptions: ASSUMPTIONS,
  summary: SUMMARY,
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <BacktestPage />
    </QueryClientProvider>,
  )
}

function statCard(label: string): HTMLElement {
  return screen.getByText(label).closest('div') as HTMLElement
}

async function run(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole('option', { name: /ma5-cross/ })
  await user.click(screen.getByRole('button', { name: '開始回測' }))
}

function setRange(start: string, end: string) {
  // type="date" inputs do not accept keystrokes reliably under jsdom, and the
  // value is what this test is about, not the typing.
  fireEvent.change(screen.getByLabelText('開始日期'), { target: { value: start } })
  fireEvent.change(screen.getByLabelText('結束日期'), { target: { value: end } })
}

describe('BacktestPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/strategies') return [STRATEGY] as never
      if (path === '/api/backtests') return [] as never
      return [] as never
    })
  })

  // --- the form ------------------------------------------------------------

  it('pre-fills the standard Taiwan trading costs so the owner can just press go', async () => {
    renderPage()

    await screen.findByRole('option', { name: /ma5-cross/ })
    expect(screen.getByLabelText('成交價基準')).toHaveValue('next_open')
    expect(screen.getByLabelText('手續費率（單邊）')).toHaveValue('0.001425')
    expect(screen.getByLabelText('滑價率')).toHaveValue('0.0005')
    expect(screen.getByLabelText('賣出交易稅率')).toHaveValue('0')
    expect(screen.getByLabelText('每次下單數量')).toHaveValue('1')
    expect(screen.getByLabelText('起始本金')).toHaveValue('100000')
  })

  it('says what each cost rate means as a percentage, since 0.001425 is not a readable number', async () => {
    renderPage()

    await screen.findByRole('option', { name: /ma5-cross/ })
    expect(screen.getByText('＝ 0.1425%')).toBeInTheDocument()
    expect(screen.getByText('＝ 0.05%')).toBeInTheDocument()
  })

  it('defaults the range to the last year', async () => {
    renderPage()

    await screen.findByRole('option', { name: /ma5-cross/ })
    const day = (date: Date) =>
      `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
    const today = new Date()
    const lastYear = new Date(today)
    lastYear.setFullYear(lastYear.getFullYear() - 1)

    expect(screen.getByLabelText('結束日期')).toHaveValue(day(today))
    expect(screen.getByLabelText('開始日期')).toHaveValue(day(lastYear))
  })

  it('runs the selected strategy over the range with the costs on screen', async () => {
    vi.mocked(api.post).mockResolvedValue(RUN as never)
    const user = userEvent.setup()
    renderPage()

    await screen.findByRole('option', { name: /ma5-cross/ })
    setRange('2025-03-01', '2026-03-01')
    const tax = screen.getByLabelText('賣出交易稅率')
    await user.clear(tax)
    await user.type(tax, '0.003')
    await user.click(screen.getByRole('button', { name: '開始回測' }))

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/backtests', {
        strategy_id: 1,
        // Whole days, so the last day's candle is inside the range rather than
        // cut off at its own midnight.
        start: '2025-03-01T00:00:00Z',
        end: '2026-03-01T23:59:59Z',
        fill_price_basis: 'next_open',
        commission_rate: '0.001425',
        slippage_rate: '0.0005',
        sell_tax_rate: '0.003',
        quantity: '1',
        initial_capital: '100000',
      }),
    )
  })

  it('locks the button and says the wait is expected while bars are replayed', async () => {
    // Replaying years of candles is not instant, and a button that looks idle
    // invites a second, third and fourth run.
    vi.mocked(api.post).mockReturnValue(new Promise(() => {}) as never)
    const user = userEvent.setup()
    renderPage()

    await run(user)

    const pending = await screen.findByRole('button', { name: '回測中…' })
    expect(pending).toBeDisabled()
    expect(screen.getByText(/回放整段歷史/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '開始回測' })).not.toBeInTheDocument()
  })

  it('has nothing to run, and says why, before the first strategy exists', async () => {
    vi.mocked(api.get).mockResolvedValue([] as never)
    renderPage()

    expect(await screen.findByText(/還沒有任何策略/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '開始回測' })).toBeDisabled()
  })

  // --- reading the result --------------------------------------------------

  it('shows the headline numbers a non-programmer can judge the strategy on', async () => {
    vi.mocked(api.post).mockResolvedValue(RUN as never)
    const user = userEvent.setup()
    renderPage()
    await run(user)

    await screen.findByRole('region', { name: '績效總覽' })
    expect(within(statCard('總報酬率')).getByText('+0.51%')).toBeInTheDocument()
    expect(within(statCard('最終權益')).getByText('100,510.25')).toBeInTheDocument()
    expect(within(statCard('已實現損益')).getByText('+510.25')).toBeInTheDocument()
    expect(within(statCard('最大回撤')).getByText('-3.21%')).toBeInTheDocument()
    expect(within(statCard('交易次數')).getByText('2')).toBeInTheDocument()
    expect(within(statCard('勝率')).getByText('50%')).toBeInTheDocument()
  })

  it('colours a gain and a loss differently in the headline', async () => {
    vi.mocked(api.post).mockResolvedValue({
      ...RUN,
      result: {
        ...RESULT,
        summary: { ...SUMMARY, total_return_pct: '-12.3456', net_pnl: '-12345.6' },
      },
    } as never)
    const user = userEvent.setup()
    renderPage()
    await run(user)

    await screen.findByRole('region', { name: '績效總覽' })
    expect(within(statCard('總報酬率')).getByText('-12.35%')).toHaveClass('text-red-400')
    expect(within(statCard('已實現損益')).getByText('-12,345.6')).toHaveClass('text-red-400')
  })

  it('says 尚未成交 rather than 0% when the strategy never opened a position', async () => {
    // A "0% 勝率" reads as a strategy that lost every trade, which is a
    // different -- and much worse -- thing than never having traded.
    vi.mocked(api.post).mockResolvedValue({
      ...RUN,
      result: {
        ...RESULT,
        trades: [],
        summary: { ...SUMMARY, trade_count: 0, wins: 0, losses: 0, win_rate_pct: null },
      },
    } as never)
    const user = userEvent.setup()
    renderPage()
    await run(user)

    await screen.findByRole('region', { name: '績效總覽' })
    expect(within(statCard('勝率')).getByText('沒有交易')).toBeInTheDocument()
  })

  it('puts the costs the result was computed under right next to the numbers', async () => {
    // A 40% return computed with zero fees must not be readable as a 40%
    // return, so the assumptions travel with the figures rather than living
    // on some settings page.
    vi.mocked(api.post).mockResolvedValue(RUN as never)
    const user = userEvent.setup()
    renderPage()
    await run(user)

    const box = await screen.findByRole('region', { name: '這次回測的假設' })
    expect(within(box).getByText('0.1425%')).toBeInTheDocument()
    expect(within(box).getByText('0.05%')).toBeInTheDocument()
    expect(within(box).getByText('0.3%')).toBeInTheDocument()
    expect(within(box).getByText('下一根 K 棒開盤價')).toBeInTheDocument()
    expect(within(box).getByText(/單邊 0.1425%，買進與賣出各收一次/)).toBeInTheDocument()
  })

  it('draws the equity curve', async () => {
    vi.mocked(api.post).mockResolvedValue(RUN as never)
    const user = userEvent.setup()
    renderPage()
    await run(user)

    expect(await screen.findByRole('img', { name: /權益曲線/ })).toBeInTheDocument()
  })

  it('lists the trades with losses marked apart from gains', async () => {
    vi.mocked(api.post).mockResolvedValue(RUN as never)
    const user = userEvent.setup()
    renderPage()
    await run(user)

    const table = await screen.findByRole('table', { name: '交易明細' })
    const rows = within(table).getAllByRole('row')
    expect(within(rows[1]).getByText('獲利')).toBeInTheDocument()
    expect(within(rows[1]).getByText('+820.5')).toHaveClass('text-emerald-400')
    expect(within(rows[2]).getByText('虧損')).toBeInTheDocument()
    expect(within(rows[2]).getByText('-310.25')).toHaveClass('text-red-400')
  })

  it('surfaces the notes about signals the run could not act on', async () => {
    vi.mocked(api.post).mockResolvedValue(RUN as never)
    const user = userEvent.setup()
    renderPage()
    await run(user)

    expect(await screen.findByText(/有 1 次買進訊號因為已有部位而略過/)).toBeInTheDocument()
  })

  // --- the honest failures -------------------------------------------------

  it('does not let a run that tested nothing read as a flat 0% result', async () => {
    vi.mocked(api.post).mockResolvedValue(
      emptyRun('這個區間沒有取得任何已收盤的 K 棒。可能是代號打錯、資料來源沒有這段歷史，或區間全都落在未來。') as never,
    )
    const user = userEvent.setup()
    renderPage()
    await run(user)

    expect(await screen.findByText(/這次回測沒有測到任何一根 K 棒/)).toBeInTheDocument()
    expect(screen.getByText(/可能是代號打錯/)).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '績效總覽' })).not.toBeInTheDocument()
  })

  it('passes through the reason when warm-up ate the whole range', async () => {
    vi.mocked(api.post).mockResolvedValue(
      emptyRun('只取得 12 根 K 棒，少於暖身需要的 30 根，所以沒有任何一根進入測試。請把區間拉長，或在策略裡調低 warmup_bars。') as never,
    )
    const user = userEvent.setup()
    renderPage()
    await run(user)

    expect(await screen.findByText(/少於暖身需要的 30 根/)).toBeInTheDocument()
  })

  it('shows the backend refusal verbatim when the range is too large', async () => {
    vi.mocked(api.post).mockRejectedValue(
      new ApiError(422, '回測區間太長：1m K 棒在這段期間大約有 525600 根，超過單次回測上限 5000 根。請縮短區間，或改用比較大的 K 棒週期。'),
    )
    const user = userEvent.setup()
    renderPage()
    await run(user)

    expect(await screen.findByText(/超過單次回測上限 5000 根/)).toBeInTheDocument()
  })

  it('translates a strategy that no longer exists out of English', async () => {
    vi.mocked(api.post).mockRejectedValue(new ApiError(404, 'Strategy not found'))
    const user = userEvent.setup()
    renderPage()
    await run(user)

    expect(await screen.findByText(/找不到這個策略/)).toBeInTheDocument()
    expect(screen.queryByText('Strategy not found')).not.toBeInTheDocument()
  })

  it('shows an unexpected server failure as readable text rather than a blank panel', async () => {
    vi.mocked(api.post).mockRejectedValue(new ApiError(500, 'Internal Server Error'))
    const user = userEvent.setup()
    renderPage()
    await run(user)

    expect(await screen.findByText(/回測失敗（500）/)).toBeInTheDocument()
  })

  // --- history -------------------------------------------------------------

  it('reopens a past run, so a result is not lost the moment the next one starts', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/strategies') return [STRATEGY] as never
      if (path === '/api/backtests') return [HISTORY_ROW] as never
      if (path === '/api/backtests/11') return RUN as never
      return [] as never
    })
    const user = userEvent.setup()
    renderPage()

    const table = await screen.findByRole('table', { name: '過去的回測' })
    await user.click(within(table).getByRole('button', { name: '查看' }))

    expect(await screen.findByRole('region', { name: '績效總覽' })).toBeInTheDocument()
    expect(api.get).toHaveBeenCalledWith('/api/backtests/11')
  })
})
