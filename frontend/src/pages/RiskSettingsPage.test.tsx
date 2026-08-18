import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { RiskSettingsPage } from './RiskSettingsPage'
import { api } from '../lib/api'
import type { RiskSettings } from '../lib/types'

vi.mock('../lib/api', () => ({ api: { get: vi.fn(), put: vi.fn() } }))

const SETTINGS: RiskSettings = {
  capital: '100000',
  stop_loss_pct: '0.05',
  take_profit_pct: '0.1',
  max_position_qty: '0',
  max_order_notional: '0',
  max_pending_orders_per_symbol: 3,
  signal_cooldown_sec: 300,
  alert_interval_sec: 900,
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <RiskSettingsPage />
    </QueryClientProvider>,
  )
}

describe('RiskSettingsPage', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockResolvedValue(SETTINGS as never)
  })

  it('loads and displays current settings', async () => {
    renderPage()
    expect(await screen.findByLabelText('最大持倉數量')).toHaveValue('0')
  })

  it('saves updated settings', async () => {
    vi.mocked(api.put).mockResolvedValue({ ...SETTINGS, max_position_qty: '500' } as never)
    const user = userEvent.setup()
    renderPage()

    const input = await screen.findByLabelText('最大持倉數量')
    await user.clear(input)
    await user.type(input, '500')
    await user.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        '/api/risk-settings',
        expect.objectContaining({ max_position_qty: '500' }),
      ),
    )
  })
  it('shows the alert interval alongside the cooldown and explains what each throttles', async () => {
    renderPage()

    expect(await screen.findByLabelText('提醒間隔（秒）')).toHaveValue('900')
    expect(screen.getByLabelText('下單訊號冷卻時間（秒）')).toHaveValue('300')
    expect(screen.getByText(/在策略的門檻附近上下震盪/)).toBeInTheDocument()
    expect(screen.getByText(/填 0 表示每次訊號都通知/)).toBeInTheDocument()
  })

  it('frames these as the defaults every strategy inherits unless it overrides them', async () => {
    renderPage()

    expect(await screen.findByText(/這一頁是全域預設值/)).toBeInTheDocument()
    expect(screen.getByText(/沒有打開的策略一律沿用這裡/)).toBeInTheDocument()
  })

  it('warns that 本金 now rejects orders and that 0 means unlimited', async () => {
    // 本金 was stored and displayed since v1 but enforced nowhere, so a
    // number typed in months ago is about to start blocking buys.
    renderPage()

    expect(await screen.findByText(/本金現在會真的擋單/)).toBeInTheDocument()
    expect(screen.getByText(/以前這個欄位只是存起來顯示/)).toBeInTheDocument()
    expect(screen.getAllByText(/填 0 表示不限制/).length).toBeGreaterThan(0)
  })

  it('saves the alert interval', async () => {
    vi.mocked(api.put).mockResolvedValue({ ...SETTINGS, alert_interval_sec: 0 } as never)
    const user = userEvent.setup()
    renderPage()

    const input = await screen.findByLabelText('提醒間隔（秒）')
    await user.clear(input)
    await user.type(input, '0')
    await user.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        '/api/risk-settings',
        expect.objectContaining({ alert_interval_sec: '0' }),
      ),
    )
  })
})
