import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { StrategiesPage } from './StrategiesPage'
import { ApiError, api } from '../lib/api'
import type { Strategy, StrategyAlert } from '../lib/types'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

const STRATEGY: Strategy = {
  id: 1,
  name: 'ma5-cross',
  symbol: 'AAPL',
  data_source: 'yfinance',
  is_active: false,
  alert_only: false,
  default_quantity: '1',
  warmup_bars: 30,
  last_signal: null,
  last_signal_at: null,
  last_run_at: null,
  last_error: null,
  consecutive_errors: 0,
}

const SAVED_SOURCE = 'class Strategy:\n    pass\n'
const GENERATED_SOURCE = 'class Strategy:\n    def __init__(self):\n        self.name = "TSMC_MA5"\n'
const AI_DESCRIPTION_LABEL = '想要的策略（用中文描述就可以）'
const ALERT_ONLY_LABEL = '只提醒，不產生訂單'

// The owner's own sentence, and the ambiguity inside it that the backend
// refuses to settle on their behalf.
const OWNER_DESCRIPTION =
  '台積電周線 RSI>80 後，等待 MACD 快慢線交叉向下後的第二根K線收盤時，快慢線沒收斂時觸發賣出警訊'
const CLARIFYING_QUESTION =
  '「快慢線沒收斂」有兩種讀法：（A）兩線的距離還在繼續擴大（B）只要兩線還沒交叉回來。你要哪一種？'

const CATALOGUE = {
  categories: [
    { name: 'trend', label: '趨勢', count: 1 },
    { name: 'momentum', label: '動能', count: 1 },
  ],
  indicators: [
    {
      name: 'macd',
      category: 'trend',
      title: '指數平滑異同移動平均 (MACD)',
      description: 'macd 是快線，signal 是慢線。',
      signature: 'macd(values, fast_period=12, slow_period=26, signal_period=9)',
      result: 'series_map',
      keys: ['macd', 'signal', 'histogram'],
      params: [],
    },
    {
      name: 'rsi',
      category: 'momentum',
      title: '相對強弱指標 (RSI)',
      description: '0~100 的動能指標。',
      signature: 'rsi(values, period=14)',
      result: 'series',
      keys: [],
      params: [],
    },
  ],
}

const ALERT: StrategyAlert = {
  id: 7,
  strategy_id: 1,
  symbol: 'AAPL',
  side: 'buy',
  price: '188.5',
  status: 'sent',
  error: null,
  created_at: '2026-08-18T01:00:00Z',
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <StrategiesPage />
    </QueryClientProvider>,
  )
}

describe('StrategiesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/strategies') return [STRATEGY] as never
      if (path === '/api/strategies/samples') return [] as never
      if (path === '/api/strategies/1') return { ...STRATEGY, source_code: SAVED_SOURCE } as never
      return [] as never
    })
  })

  it('lists existing strategies', async () => {
    renderPage()
    expect(await screen.findByText('ma5-cross')).toBeInTheDocument()
    expect(screen.getByText('AAPL')).toBeInTheDocument()
  })

  it('activates a strategy', async () => {
    vi.mocked(api.post).mockResolvedValue({ ...STRATEGY, is_active: true } as never)
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('ma5-cross')
    await user.click(screen.getByRole('button', { name: '啟用' }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/strategies/1/activate'))
  })

  it('validates draft code before creating', async () => {
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path === '/api/strategies/validate') {
        return { ok: true, detected_name: 'n', detected_symbol: 'TSLA', sample_signals: ['HOLD'] } as never
      }
      return STRATEGY as never
    })
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))
    await user.type(screen.getByLabelText('名稱'), 'my-strategy')
    await user.type(screen.getByLabelText('股票代號'), 'TSLA')
    await user.type(screen.getByLabelText('原始碼'), 'class Strategy: pass')
    await user.click(screen.getByRole('button', { name: '驗證' }))

    expect(await screen.findByText('偵測到：n（TSLA）')).toBeInTheDocument()
  })

  it('loads a sample strategy into the form and auto-fills detected name/symbol', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/strategies') return [STRATEGY] as never
      if (path === '/api/strategies/samples') {
        return [{ filename: 'ma5_cross.py', source_code: 'class Strategy:\n    pass\n' }] as never
      }
      return [] as never
    })
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path === '/api/strategies/validate') {
        return {
          ok: true,
          detected_name: 'AAPL_MA5_Trend',
          detected_symbol: 'AAPL',
          sample_signals: ['HOLD'],
        } as never
      }
      return STRATEGY as never
    })
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))
    await user.click(await screen.findByRole('button', { name: '5 日均線交叉' }))

    expect(screen.getByLabelText('原始碼')).toHaveValue('class Strategy:\n    pass\n')

    await user.click(screen.getByRole('button', { name: '驗證' }))

    expect(await screen.findByLabelText('名稱')).toHaveValue('AAPL_MA5_Trend')
    expect(screen.getByLabelText('股票代號')).toHaveValue('AAPL')
  })

  it('generates a strategy from a plain-language description and fills the form', async () => {
    vi.mocked(api.post).mockImplementation(async (path: string) => {
      if (path === '/api/strategies/generate') {
        return {
          ok: true,
          error: null,
          source_code: GENERATED_SOURCE,
          detected_name: 'TSMC_MA5',
          detected_symbol: '2330.TW',
          sample_signals: ['HOLD', 'BUY'],
        } as never
      }
      return STRATEGY as never
    })
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))
    await user.type(screen.getByLabelText(AI_DESCRIPTION_LABEL), '台積電五日均線向上就買進')
    await user.click(screen.getByRole('button', { name: 'AI 產生策略' }))

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/strategies/generate', {
        description: '台積電五日均線向上就買進',
        symbol: null,
      }),
    )
    expect(await screen.findByText('偵測到：TSMC_MA5（2330.TW）')).toBeInTheDocument()
    expect(screen.getByLabelText('原始碼')).toHaveValue(GENERATED_SOURCE)
    expect(screen.getByLabelText('名稱')).toHaveValue('TSMC_MA5')
    expect(screen.getByLabelText('股票代號')).toHaveValue('2330.TW')
  })

  it('sends the symbol already typed in the form as the generation target', async () => {
    vi.mocked(api.post).mockResolvedValue({
      ok: true,
      error: null,
      source_code: GENERATED_SOURCE,
      detected_name: 'TSMC_MA5',
      detected_symbol: '2330.TW',
      sample_signals: ['HOLD'],
    } as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))
    await user.type(screen.getByLabelText('股票代號'), '2330.TW')
    await user.type(screen.getByLabelText(AI_DESCRIPTION_LABEL), '五日均線向上就買進')
    await user.click(screen.getByRole('button', { name: 'AI 產生策略' }))

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/strategies/generate', {
        description: '五日均線向上就買進',
        symbol: '2330.TW',
      }),
    )
  })

  it('shows a readable error when generation fails', async () => {
    vi.mocked(api.post).mockResolvedValue({
      ok: false,
      error: 'AI_API_KEY 尚未設定，請先填好金鑰再試一次。',
      source_code: null,
      detected_name: null,
      detected_symbol: null,
      sample_signals: null,
    } as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))
    await user.type(screen.getByLabelText(AI_DESCRIPTION_LABEL), '隨便給我一個策略')
    await user.click(screen.getByRole('button', { name: 'AI 產生策略' }))

    expect(await screen.findByText(/AI_API_KEY 尚未設定/)).toBeInTheDocument()
    expect(screen.getByLabelText('原始碼')).toHaveValue('')
  })

  it('shows a network failure as text rather than a blank panel', async () => {
    vi.mocked(api.post).mockRejectedValue(new ApiError(503, 'Service Unavailable'))
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))
    await user.type(screen.getByLabelText(AI_DESCRIPTION_LABEL), '隨便給我一個策略')
    await user.click(screen.getByRole('button', { name: 'AI 產生策略' }))

    expect(await screen.findByText(/Service Unavailable/)).toBeInTheDocument()
  })

  it('fills in code that failed validation together with the reason', async () => {
    // The backend hands back the rejected code on purpose -- the owner can
    // read and fix it, which beats being told only that something went wrong.
    vi.mocked(api.post).mockResolvedValue({
      ok: false,
      error: "AI 產生的程式碼無法通過驗證：import of module 'pandas' is not allowed",
      source_code: GENERATED_SOURCE,
      detected_name: null,
      detected_symbol: null,
      sample_signals: null,
    } as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))
    await user.type(screen.getByLabelText(AI_DESCRIPTION_LABEL), '用 pandas 算均線')
    await user.click(screen.getByRole('button', { name: 'AI 產生策略' }))

    expect(await screen.findByText(/pandas' is not allowed/)).toBeInTheDocument()
    expect(screen.getByLabelText('原始碼')).toHaveValue(GENERATED_SOURCE)
  })

  it('disables the generate button and shows a pending state while the AI works', async () => {
    vi.mocked(api.post).mockReturnValue(new Promise(() => {}) as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))
    await user.type(screen.getByLabelText(AI_DESCRIPTION_LABEL), '五日均線策略')
    await user.click(screen.getByRole('button', { name: 'AI 產生策略' }))

    const pending = await screen.findByRole('button', { name: '產生中…' })
    expect(pending).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'AI 產生策略' })).not.toBeInTheDocument()
  })

  it('locks 驗證 and 建立 while the AI is still writing', async () => {
    // 建立 closes the form on success, so firing it mid-generation throws
    // away an answer the daily quota has already been spent on.
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(api.post).mockReturnValue(new Promise(() => {}) as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))
    await user.type(screen.getByLabelText('名稱'), '我的策略')
    await user.type(screen.getByLabelText('股票代號'), '2330.TW')
    await user.type(screen.getByLabelText('原始碼'), 'class Strategy: pass')
    await user.type(screen.getByLabelText(AI_DESCRIPTION_LABEL), '五日均線策略')
    await user.click(screen.getByRole('button', { name: 'AI 產生策略' }))

    await screen.findByRole('button', { name: '產生中…' })
    expect(screen.getByRole('button', { name: '建立' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '驗證' })).toBeDisabled()
  })

  it('does not overwrite existing source code when the confirmation is cancelled', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))
    await user.type(screen.getByLabelText('原始碼'), 'my own work')
    await user.type(screen.getByLabelText(AI_DESCRIPTION_LABEL), '五日均線策略')
    await user.click(screen.getByRole('button', { name: 'AI 產生策略' }))

    expect(window.confirm).toHaveBeenCalled()
    expect(api.post).not.toHaveBeenCalled()
    expect(screen.getByLabelText('原始碼')).toHaveValue('my own work')
  })

  it('overwrites existing source code once the confirmation is accepted', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(api.post).mockResolvedValue({
      ok: true,
      error: null,
      source_code: GENERATED_SOURCE,
      detected_name: 'TSMC_MA5',
      detected_symbol: '2330.TW',
      sample_signals: ['HOLD'],
    } as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))
    await user.type(screen.getByLabelText('原始碼'), 'my own work')
    await user.type(screen.getByLabelText(AI_DESCRIPTION_LABEL), '五日均線策略')
    await user.click(screen.getByRole('button', { name: 'AI 產生策略' }))

    await waitFor(() => expect(screen.getByLabelText('原始碼')).toHaveValue(GENERATED_SOURCE))
  })

  it('always shows the read-before-you-activate warning', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))

    expect(screen.getByText(/自己讀過、看懂/)).toBeInTheDocument()
    expect(screen.getByText(/不是投資建議/)).toBeInTheDocument()
  })

  it('prefills the editor with the saved source code', async () => {
    // Regression: the editor opened blank because the list response omits
    // source_code and nothing fetched it. That is indistinguishable from the
    // code having been lost -- and saving from that state used to be the only
    // thing standing between the user and actually losing it.
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('ma5-cross')
    await user.click(screen.getByRole('button', { name: '編輯' }))

    await waitFor(() => expect(screen.getByLabelText('原始碼')).toHaveValue(SAVED_SOURCE))
  })

  it('edits a strategy and resends its source code unchanged', async () => {
    vi.mocked(api.patch).mockResolvedValue({ ...STRATEGY, name: 'renamed' } as never)
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('ma5-cross')
    await user.click(screen.getByRole('button', { name: '編輯' }))
    await waitFor(() => expect(screen.getByLabelText('原始碼')).toHaveValue(SAVED_SOURCE))

    const nameInput = screen.getByLabelText('名稱')
    await user.clear(nameInput)
    await user.type(nameInput, 'renamed')
    await user.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith('/api/strategies/1', {
        name: 'renamed',
        symbol: 'AAPL',
        alert_only: false,
        source_code: SAVED_SOURCE,
      }),
    )
  })

  it('deletes a strategy after confirming', async () => {
    vi.mocked(api.delete).mockResolvedValue(undefined as never)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('ma5-cross')
    await user.click(screen.getByRole('button', { name: '刪除' }))

    expect(window.confirm).toHaveBeenCalled()
    await waitFor(() => expect(api.delete).toHaveBeenCalledWith('/api/strategies/1'))
  })

  it('does not delete a strategy when the confirmation is cancelled', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('ma5-cross')
    await user.click(screen.getByRole('button', { name: '刪除' }))

    expect(api.delete).not.toHaveBeenCalled()
  })
  it('creates a watch-only strategy when 只提醒 is ticked', async () => {
    vi.mocked(api.post).mockResolvedValue(STRATEGY as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))
    await user.type(screen.getByLabelText('名稱'), 'watcher')
    await user.type(screen.getByLabelText('股票代號'), 'TSLA')
    await user.type(screen.getByLabelText('原始碼'), 'class Strategy: pass')
    await user.click(screen.getByLabelText(ALERT_ONLY_LABEL))
    await user.click(screen.getByRole('button', { name: '建立' }))

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/strategies', {
        name: 'watcher',
        symbol: 'TSLA',
        source_code: 'class Strategy: pass',
        alert_only: true,
      }),
    )
  })

  it('spells out that a watch-only strategy never produces an order to confirm', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))

    expect(screen.getByText(/只會發通知給你，不會產生需要確認的訂單/)).toBeInTheDocument()
  })

  it('turns an existing strategy into a watch-only one from the edit form', async () => {
    vi.mocked(api.patch).mockResolvedValue({ ...STRATEGY, alert_only: true } as never)
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('ma5-cross')
    await user.click(screen.getByRole('button', { name: '編輯' }))
    await waitFor(() => expect(screen.getByLabelText('原始碼')).toHaveValue(SAVED_SOURCE))

    await user.click(screen.getByLabelText(ALERT_ONLY_LABEL))
    await user.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith('/api/strategies/1', {
        name: 'ma5-cross',
        symbol: 'AAPL',
        alert_only: true,
        source_code: SAVED_SOURCE,
      }),
    )
  })

  it('shows the edit form already ticked for a watch-only strategy', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/strategies') return [{ ...STRATEGY, alert_only: true }] as never
      if (path === '/api/strategies/1') return { ...STRATEGY, alert_only: true, source_code: SAVED_SOURCE } as never
      return [] as never
    })
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('ma5-cross')
    await user.click(screen.getByRole('button', { name: '編輯' }))

    expect(screen.getByLabelText(ALERT_ONLY_LABEL)).toBeChecked()
  })

  it('marks in the list which strategies place orders and which only notify', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/strategies') {
        return [STRATEGY, { ...STRATEGY, id: 2, name: 'watcher', alert_only: true }] as never
      }
      return [] as never
    })
    renderPage()

    const tradingRow = (await screen.findByText('ma5-cross')).closest('tr')
    expect(within(tradingRow as HTMLElement).getByText('會下單')).toBeInTheDocument()

    const watchingRow = screen.getByText('watcher').closest('tr')
    expect(within(watchingRow as HTMLElement).getByText('只提醒')).toBeInTheDocument()
  })

  it('lists the alert history newest first', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/strategies') return [STRATEGY] as never
      if (path === '/api/alerts') {
        return [
          { ...ALERT, id: 8, side: 'sell', price: '190', created_at: '2026-08-18T02:00:00Z' },
          ALERT,
        ] as never
      }
      return [] as never
    })
    renderPage()

    const table = await screen.findByRole('table', { name: '提醒紀錄' })
    const rows = within(table).getAllByRole('row')
    expect(within(rows[1]).getByText('賣出')).toBeInTheDocument()
    expect(within(rows[1]).getByText('190')).toBeInTheDocument()
    expect(within(rows[2]).getByText('買進')).toBeInTheDocument()
    expect(within(rows[2]).getByText('188.5')).toBeInTheDocument()
    expect(within(rows[2]).getByText('ma5-cross')).toBeInTheDocument()
  })

  it('marks an alert the owner never actually received', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/strategies') return [STRATEGY] as never
      if (path === '/api/alerts') return [{ ...ALERT, status: 'failed', error: 'timeout' }] as never
      return [] as never
    })
    renderPage()

    const table = await screen.findByRole('table', { name: '提醒紀錄' })
    expect(within(table).getByText('未送達')).toBeInTheDocument()
  })

  it('shows an empty state when no alert has fired yet', async () => {
    renderPage()

    expect(await screen.findByText('目前沒有提醒紀錄。')).toBeInTheDocument()
  })

  it('shows which candle a generated strategy decided to work in', async () => {
    // 「周線」 was the owner's own word. A strategy that quietly came back
    // daily reads identically in the source box, so the box cannot be the
    // only place this is visible.
    vi.mocked(api.post).mockResolvedValue({
      ok: true,
      error: null,
      source_code: GENERATED_SOURCE,
      detected_name: 'TSMC_WEEKLY',
      detected_symbol: '2330.TW',
      entry_point: 'on_bar',
      timeframe: '1wk',
      question: null,
      sample_signals: ['HOLD'],
    } as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))
    await user.type(screen.getByLabelText(AI_DESCRIPTION_LABEL), OWNER_DESCRIPTION)
    await user.click(screen.getByRole('button', { name: 'AI 產生策略' }))

    expect(
      await screen.findByText('偵測到：TSMC_WEEKLY（2330.TW）・每根週線收盤時判斷'),
    ).toBeInTheDocument()
  })

  it('says a tick strategy reacts to every quote rather than to a candle', async () => {
    vi.mocked(api.post).mockResolvedValue({
      ok: true,
      error: null,
      source_code: GENERATED_SOURCE,
      detected_name: 'TSMC_MA5',
      detected_symbol: '2330.TW',
      entry_point: 'on_tick',
      timeframe: null,
      question: null,
      sample_signals: ['HOLD'],
    } as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))
    await user.type(screen.getByLabelText(AI_DESCRIPTION_LABEL), '五日均線向上就買進')
    await user.click(screen.getByRole('button', { name: 'AI 產生策略' }))

    expect(
      await screen.findByText('偵測到：TSMC_MA5（2330.TW）・每次報價更新時判斷'),
    ).toBeInTheDocument()
  })

  it('surfaces a clarifying question instead of a guessed strategy', async () => {
    vi.mocked(api.post).mockResolvedValue({
      ok: false,
      error: null,
      source_code: null,
      detected_name: null,
      detected_symbol: null,
      entry_point: null,
      timeframe: null,
      question: CLARIFYING_QUESTION,
      sample_signals: null,
    } as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))
    await user.type(screen.getByLabelText(AI_DESCRIPTION_LABEL), OWNER_DESCRIPTION)
    await user.click(screen.getByRole('button', { name: 'AI 產生策略' }))

    expect(await screen.findByText(CLARIFYING_QUESTION)).toBeInTheDocument()
    // Nothing may land in the code box: a guess there would look finished.
    expect(screen.getByLabelText('原始碼')).toHaveValue('')
    // And it is a question, not a failure -- the generic failure text would
    // train the owner to ignore it.
    expect(screen.queryByText(/產生策略失敗/)).not.toBeInTheDocument()
  })

  it('sends the answer back with the question and then fills in the strategy', async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce({
        ok: false,
        error: null,
        source_code: null,
        detected_name: null,
        detected_symbol: null,
        entry_point: null,
        timeframe: null,
        question: CLARIFYING_QUESTION,
        sample_signals: null,
      } as never)
      .mockResolvedValueOnce({
        ok: true,
        error: null,
        source_code: GENERATED_SOURCE,
        detected_name: 'TSMC_WEEKLY',
        detected_symbol: '2330.TW',
        entry_point: 'on_bar',
        timeframe: '1wk',
        question: null,
        sample_signals: ['HOLD'],
      } as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))
    await user.type(screen.getByLabelText(AI_DESCRIPTION_LABEL), OWNER_DESCRIPTION)
    await user.click(screen.getByRole('button', { name: 'AI 產生策略' }))

    await screen.findByText(CLARIFYING_QUESTION)
    await user.type(screen.getByLabelText('你的回答'), '（A）兩線的距離還在繼續擴大')
    await user.click(screen.getByRole('button', { name: '回答並重新產生' }))

    // The question travels back with the answer: the model is single-turn, so
    // "（A）" on its own answers nothing.
    await waitFor(() =>
      expect(api.post).toHaveBeenLastCalledWith('/api/strategies/generate', {
        description: OWNER_DESCRIPTION,
        symbol: null,
        question: CLARIFYING_QUESTION,
        answer: '（A）兩線的距離還在繼續擴大',
      }),
    )
    await waitFor(() => expect(screen.getByLabelText('原始碼')).toHaveValue(GENERATED_SOURCE))
    expect(screen.queryByText(CLARIFYING_QUESTION)).not.toBeInTheDocument()
  })

  it('lets the owner browse the indicators the runtime already provides', async () => {
    // Otherwise "which indicators do I have?" is answered by guessing, and a
    // description that names one that does not exist comes back hand-rolled.
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/strategies') return [STRATEGY] as never
      if (path === '/api/indicators') return CATALOGUE as never
      return [] as never
    })
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))
    await user.click(screen.getByRole('button', { name: '看看有哪些指標可以用' }))

    expect(await screen.findByText('rsi(values, period=14)')).toBeInTheDocument()
    expect(screen.getByText('相對強弱指標 (RSI)')).toBeInTheDocument()
    expect(screen.getByText('動能（1）')).toBeInTheDocument()
    expect(screen.getByText('趨勢（1）')).toBeInTheDocument()
  })

  it('does not fetch the indicator catalogue until it is asked for', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增策略' }))

    expect(api.get).not.toHaveBeenCalledWith('/api/indicators')
  })
})
