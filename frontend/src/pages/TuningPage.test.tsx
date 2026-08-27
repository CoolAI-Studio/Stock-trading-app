/**
 * 調參頁：參數掃描，加上「這組參數在它沒看過的資料上還成立嗎」。
 *
 * #34 的後端做完了，但三個端點對這個使用者等於不存在——他不會去打 API。這一頁是
 * 那三件事唯一到得了他手上的路。
 *
 * ＊ 這一頁最容易做壞的地方，是把它做成一張「最佳參數」表。
 *
 * 後端在每一次掃描的結果裡都附了一句：在整個網格上挑最高的那一格，挑到的通常是雜
 * 訊。那句話如果沒有出現在畫面上，這一頁就是在教使用者做錯的事——而他不是工程師，
 * 他會相信排在第一列的那一組。
 *
 * 所以這裡有一條測試專門守那句話，而且守的是**顯示出來**，不是後端有回。
 *
 * ＊ 第二個容易做壞的地方：把「沒跑完」畫成 0。
 *
 * 那會讓它在排序時沉到最底下，看起來像一個結論——而它其實是「沒有答案」。後端已經
 * 分開了這兩件事，前端不可以又把它們合起來。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TuningPage } from './TuningPage'
import { api } from '../lib/api'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

const STRATEGY = {
  id: 1,
  name: 'ma-cross',
  symbol: '2330.TW',
  is_active: true,
  source_code: 'class Strategy:\n    pass\n',
}

const DECLARED = {
  ok: true,
  declared_params: { window: 5, threshold: 1.5 },
  entry_point: 'on_bar',
}

function summary(netPnl: string) {
  return {
    bars_total: 100,
    bars_tested: 100,
    signals: 4,
    skipped_signals: 0,
    unfilled_signals: 0,
    trade_count: 2,
    wins: 1,
    losses: 1,
    stop_loss_exits: 0,
    take_profit_exits: 0,
    ambiguous_exit_bars: 0,
    win_rate_pct: '50.00',
    average_win: '10.00',
    average_loss: '-5.00',
    net_pnl: netPnl,
    total_costs: '0.00',
    total_return_pct: '1.00',
    max_drawdown_pct: '0.00',
    final_equity: '100000.00',
    open_quantity: '0',
    open_avg_entry_price: '0.00',
    buy_and_hold_return_pct: null,
    excess_return_pct: null,
    profit_factor: null,
    exposure_pct: null,
  }
}

const SWEEP = {
  symbol: '2330.TW',
  timeframe: '1d',
  bars_total: 100,
  first_bar_at: '2026-01-05T00:00:00Z',
  last_bar_at: '2026-04-15T00:00:00Z',
  rows: [
    { params: { window: 5 }, summary: summary('500.00'), error: null },
    { params: { window: 10 }, summary: summary('-100.00'), error: null },
    { params: { window: 20 }, summary: null, error: '這一組跑不完，已經中止' },
  ],
  notes: ['**在一整個網格上挑最高的那一格，挑到的通常是雜訊。** 真的要用之前，拿它去跑一次滾動前進。'],
  truncated_note: null,
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <TuningPage />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path === '/api/strategies') return [STRATEGY] as never
    if (path === '/api/strategies/1') return STRATEGY as never
    return [] as never
  })
  vi.mocked(api.post).mockImplementation(async (path: string) => {
    if (path === '/api/strategies/validate') return DECLARED as never
    if (path === '/api/backtests/sweep') return SWEEP as never
    return {} as never
  })
})

describe('調參頁', () => {
  it('照策略宣告的參數畫欄位 —— 不是叫他自己想名字', async () => {
    // 一個不是工程師的人不會知道 self.params 裡有哪幾個鍵。他打錯一個字，後端會
    // 拒絕，而他看到的是一句他不知道怎麼修的錯誤訊息。
    renderPage()

    expect(await screen.findByLabelText(/window/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/threshold/i)).toBeInTheDocument()
  })

  it('掃描結果排出來，而且「沒跑完」不是 0', async () => {
    // 畫成 0 的話它會沉到最底下，看起來像一個結論——而它其實是沒有答案。後端已經
    // 分開了這兩件事，前端不可以又把它們合起來。
    const user = userEvent.setup()
    renderPage()

    await user.type(await screen.findByLabelText(/window/i), '5, 10, 20')
    await user.click(screen.getByRole('button', { name: /開始掃描/ }))

    const row = await screen.findByRole('row', { name: /20/ })
    expect(row).toHaveTextContent(/沒跑完|跑不完/)
    expect(row).not.toHaveTextContent(/^0\.00$/)
  })

  it('那句「挑最高的通常是雜訊」要顯示出來', async () => {
    // 這一頁最容易做壞的地方就是把它做成一張「最佳參數」表。後端每次都附了那句
    // 話，前端如果不顯示，這一頁就是在教他做錯的事——而他會相信第一列。
    const user = userEvent.setup()
    renderPage()

    await user.type(await screen.findByLabelText(/window/i), '5, 10')
    await user.click(screen.getByRole('button', { name: /開始掃描/ }))

    expect(await screen.findByText(/雜訊/)).toBeInTheDocument()
  })

  it('空白的參數不會被送出去 —— 只掃他真的填了的那幾個', async () => {
    // 全部送出去的話，沒填的那個會變成空陣列，而後端會拒絕整個網格。使用者只想調
    // 一個參數是常態。
    const user = userEvent.setup()
    renderPage()

    await user.type(await screen.findByLabelText(/window/i), '5, 10')
    await user.click(screen.getByRole('button', { name: /開始掃描/ }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/backtests/sweep', expect.anything()))
    const body = vi.mocked(api.post).mock.calls.find((call) => call[0] === '/api/backtests/sweep')?.[1] as {
      grid: Record<string, unknown[]>
    }
    expect(Object.keys(body.grid)).toEqual(['window'])
    expect(body.grid.window).toEqual([5, 10])
  })

  it('一個參數都沒填就按下去，要說清楚而不是送一個空網格', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByLabelText(/window/i)
    await user.click(screen.getByRole('button', { name: /開始掃描/ }))

    expect(await screen.findByRole('status')).toHaveTextContent(/至少|填/)
    expect(api.post).not.toHaveBeenCalledWith('/api/backtests/sweep', expect.anything())
  })
})
