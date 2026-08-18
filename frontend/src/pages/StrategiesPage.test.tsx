import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { StrategiesPage } from './StrategiesPage'
import { ApiError, api } from '../lib/api'
import type { Strategy } from '../lib/types'

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
})
