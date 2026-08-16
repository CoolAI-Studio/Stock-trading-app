import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { StrategiesPage } from './StrategiesPage'
import { api } from '../lib/api'
import type { Strategy } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn() },
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
})
