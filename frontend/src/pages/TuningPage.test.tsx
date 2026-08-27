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

const WALK_FORWARD = {
  symbol: '2330.TW',
  timeframe: '1d',
  bars_total: 400,
  folds: [
    {
      index: 0,
      train_from: 0,
      train_to: 250,
      test_from: 250,
      test_to: 310,
      chosen_params: { window: 5 },
      train_summary: summary('900.00'),
      test_summary: summary('-200.00'),
      note: null,
    },
    {
      index: 1,
      train_from: 60,
      train_to: 310,
      test_from: 310,
      test_to: 370,
      chosen_params: { window: 20 },
      train_summary: summary('800.00'),
      test_summary: summary('100.00'),
      note: null,
    },
  ],
  notes: [
    '**看的是訓練段和驗證段之間的落差，不是任何一個單獨的數字。**',
    '**每一段挑出來的參數不一樣。** 那代表這個參數沒有一個穩定的最佳值。',
  ],
}

describe('調參頁：滾動前進', () => {
  it('把訓練段和驗證段的成績擺在同一列', async () => {
    // 兩個數字分開看都沒有意義。擺在一起，它們回答的才是那個真正的問題：在訓練段
    // 上好看多少，在沒看過的資料上還剩多少。分成兩張表就等於沒做。
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path === '/api/strategies/validate') return DECLARED as never
      if (path === '/api/backtests/walk-forward') return WALK_FORWARD as never
      return {} as never
    })
    const user = userEvent.setup()
    renderPage()

    await user.type(await screen.findByLabelText(/window/i), '5, 20')
    await user.click(screen.getByRole('button', { name: /滾動前進|沒看過/ }))

    const row = await screen.findByRole('row', { name: /window=5/ })
    expect(row).toHaveTextContent('900.00')
    expect(row).toHaveTextContent('-200.00')
  })

  it('每段挑的參數不一樣的時候，那句話要看得到', async () => {
    // 這是使用者判斷「這支策略到底穩不穩」的主要線索，而且它比任何一段的分數都
    // 重要——只給分數會把它藏起來。
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path === '/api/strategies/validate') return DECLARED as never
      if (path === '/api/backtests/walk-forward') return WALK_FORWARD as never
      return {} as never
    })
    const user = userEvent.setup()
    renderPage()

    await user.type(await screen.findByLabelText(/window/i), '5, 20')
    await user.click(screen.getByRole('button', { name: /滾動前進|沒看過/ }))

    expect(await screen.findByText(/沒有一個穩定的最佳值/)).toBeInTheDocument()
  })

  it('兩顆按鈕送到不同的端點 —— 掃描不是滾動前進', async () => {
    // 兩者問的是不同的問題，而它們的結果長得很像。送錯端點的話，使用者會拿一張
    // 「這段歷史上最好」的表當成「沒看過的資料上也成立」的證據。
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path === '/api/strategies/validate') return DECLARED as never
      if (path === '/api/backtests/sweep') return SWEEP as never
      if (path === '/api/backtests/walk-forward') return WALK_FORWARD as never
      return {} as never
    })
    const user = userEvent.setup()
    renderPage()

    await user.type(await screen.findByLabelText(/window/i), '5, 20')
    await user.click(screen.getByRole('button', { name: /開始掃描/ }))
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/backtests/sweep', expect.anything()),
    )

    await user.click(screen.getByRole('button', { name: /滾動前進|沒看過/ }))
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/backtests/walk-forward', expect.anything()),
    )
  })
})

const PORTFOLIO = {
  timeframe: '1d',
  legs: [
    {
      symbol: 'AAA',
      summary: summary('500.00'),
      opened: 2,
      skipped_for_cash: 0,
      note: null,
    },
    {
      symbol: 'BBB',
      summary: summary('300.00'),
      opened: 0,
      skipped_for_cash: 3,
      note: null,
    },
    { symbol: 'GONE', summary: null, opened: 0, skipped_for_cash: 0, note: '抓不到這一支的歷史資料。' },
  ],
  equity_curve: [
    { timestamp: '2026-01-05T00:00:00Z', cash: '90000.00', equity: '100000.00', stale_symbols: [] },
    { timestamp: '2026-01-06T00:00:00Z', cash: '90000.00', equity: '100500.00', stale_symbols: ['BBB'] },
  ],
  summary: summary('800.00'),
  notes: [
    '**同一天多支都要買而錢不夠的時候，按你列出來的順序先到先得。**',
    '**有幾天某些持股沒有報價**，那幾天是用它最後一次的收盤價入帳的。',
  ],
}

describe('調參頁：投資組合', () => {
  function mockPortfolio() {
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path === '/api/strategies/validate') return DECLARED as never
      if (path === '/api/backtests/portfolio') return PORTFOLIO as never
      return {} as never
    })
  }

  it('「因為錢不夠而沒買到」要單獨看得到', async () => {
    // 沒有這個數字的話，一個因為排在後面而幾乎沒買到的代號，看起來會像一支訊號很
    // 少的爛策略——而使用者要做的決定正是「哪一支該拿掉」。這是共用錢包**唯一**
    // 新增的資訊，藏起來就等於沒做這個功能。
    mockPortfolio()
    const user = userEvent.setup()
    renderPage()

    await user.type(await screen.findByLabelText(/代號/), 'AAA, BBB')
    await user.click(screen.getByRole('button', { name: /一起跑|投組/ }))

    const row = await screen.findByRole('row', { name: /BBB/ })
    expect(row).toHaveTextContent('3')
    expect(row).toHaveTextContent(/錢不夠|沒買到/)
  })

  it('抓不到歷史的代號要留在表上，不是安靜地少一支', async () => {
    mockPortfolio()
    const user = userEvent.setup()
    renderPage()

    await user.type(await screen.findByLabelText(/代號/), 'AAA, BBB, GONE')
    await user.click(screen.getByRole('button', { name: /一起跑|投組/ }))

    const row = await screen.findByRole('row', { name: /GONE/ })
    expect(row).toHaveTextContent(/抓不到/)
  })

  it('那句「按你列的順序先到先得」要顯示出來', async () => {
    // 順序是武斷的，所以它必須看得見。不說的話，使用者換一次排列就得到不同的績
    // 效，而他會以為那是策略的差別。
    mockPortfolio()
    const user = userEvent.setup()
    renderPage()

    await user.type(await screen.findByLabelText(/代號/), 'AAA, BBB')
    await user.click(screen.getByRole('button', { name: /一起跑|投組/ }))

    expect(await screen.findByText(/先到先得/)).toBeInTheDocument()
  })

  it('代號照他打的順序送出去 —— 順序有意義', async () => {
    mockPortfolio()
    const user = userEvent.setup()
    renderPage()

    await user.type(await screen.findByLabelText(/代號/), 'BBB, AAA')
    await user.click(screen.getByRole('button', { name: /一起跑|投組/ }))

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/backtests/portfolio', expect.anything()),
    )
    const body = vi
      .mocked(api.post)
      .mock.calls.find((call) => call[0] === '/api/backtests/portfolio')?.[1] as {
      symbols: string[]
    }
    expect(body.symbols).toEqual(['BBB', 'AAA'])
  })
})
