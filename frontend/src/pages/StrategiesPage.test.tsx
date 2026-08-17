import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { StrategiesPage } from './StrategiesPage'
import { api } from '../lib/api'
import type { Strategy } from '../lib/types'

vi.mock('../lib/api', () => ({
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

  it('edits a strategy without touching its source code', async () => {
    vi.mocked(api.patch).mockResolvedValue({ ...STRATEGY, name: 'renamed' } as never)
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('ma5-cross')
    await user.click(screen.getByRole('button', { name: '編輯' }))

    const nameInput = screen.getByLabelText('名稱')
    await user.clear(nameInput)
    await user.type(nameInput, 'renamed')
    await user.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith('/api/strategies/1', { name: 'renamed', symbol: 'AAPL' }),
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
