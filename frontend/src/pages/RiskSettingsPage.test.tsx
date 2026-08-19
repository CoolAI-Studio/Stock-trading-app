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
    expect(await screen.findByLabelText('本金')).toHaveValue('100000')
  })

  it('saves updated settings', async () => {
    vi.mocked(api.put).mockResolvedValue({ ...SETTINGS, capital: '500' } as never)
    const user = userEvent.setup()
    renderPage()

    const input = await screen.findByLabelText('本金')
    await user.clear(input)
    await user.type(input, '500')
    await user.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        '/api/risk-settings',
        expect.objectContaining({ capital: '500' }),
      ),
    )
  })
  it('shows the alert interval alongside the cooldown and explains what each throttles', async () => {
    renderPage()

    expect(await screen.findByLabelText('提醒間隔（秒）')).toHaveValue('900')
    expect(screen.getByLabelText('下單訊號冷卻時間（秒）')).toHaveValue('300')
    expect(screen.getByText(/在策略的門檻附近上下震盪/)).toBeInTheDocument()
  })

  it('frames these as the defaults every strategy inherits unless it overrides them', async () => {
    renderPage()

    expect(await screen.findByText(/這一頁是全域預設值/)).toBeInTheDocument()
    expect(screen.getByText(/沒有打開的策略一律沿用這裡/)).toBeInTheDocument()
  })

  it('warns that 本金 now rejects orders and points at the switch instead of 0', async () => {
    // 本金 was stored and displayed since v1 but enforced nowhere, so a
    // number typed in months ago is about to start blocking buys.
    renderPage()

    expect(await screen.findByText(/本金現在會真的擋單/)).toBeInTheDocument()
    expect(screen.getByText(/以前這個欄位只是存起來顯示/)).toBeInTheDocument()
    expect(screen.getByText(/不想讓本金擋單，就勾它旁邊的「不限制」/)).toBeInTheDocument()
  })

  it('saves the alert interval', async () => {
    vi.mocked(api.put).mockResolvedValue({ ...SETTINGS, alert_interval_sec: 0 } as never)
    const user = userEvent.setup()
    renderPage()

    const input = await screen.findByLabelText('提醒間隔（秒）')
    await user.clear(input)
    await user.type(input, '30')
    await user.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        '/api/risk-settings',
        expect.objectContaining({ alert_interval_sec: '30' }),
      ),
    )
  })

  it('loads a stored 0 as a ticked switch, not as a 0 sitting in a live box', async () => {
    // The whole point of the switch: nobody should have to know that the 0
    // they are looking at means "no ceiling" rather than "a ceiling of zero".
    renderPage()

    const box = await screen.findByLabelText('最大持倉數量')
    expect(screen.getByLabelText('最大持倉數量：不限制')).toBeChecked()
    expect(box).toBeDisabled()
    expect(box).toHaveValue('')
  })

  it('disables the number box and saves 0 when the switch goes on', async () => {
    vi.mocked(api.put).mockResolvedValue(SETTINGS as never)
    const user = userEvent.setup()
    renderPage()

    await screen.findByLabelText('本金')
    await user.click(screen.getByLabelText('本金：不限制'))

    expect(screen.getByLabelText('本金')).toBeDisabled()

    await user.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        '/api/risk-settings',
        expect.objectContaining({ capital: '0' }),
      ),
    )
  })

  it('gives the number box back, editable, when the switch goes off again', async () => {
    vi.mocked(api.put).mockResolvedValue(SETTINGS as never)
    const user = userEvent.setup()
    renderPage()

    // Loaded as 0, so it starts switched off.
    await screen.findByLabelText('最大持倉數量')
    await user.click(screen.getByLabelText('最大持倉數量：不限制'))

    const box = screen.getByLabelText('最大持倉數量')
    expect(box).toBeEnabled()
    await user.type(box, '500')
    await user.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        '/api/risk-settings',
        expect.objectContaining({ max_position_qty: '500' }),
      ),
    )
  })

  it('never calls switching a protection off 不限制', async () => {
    // 不限制 on a stop-loss reads as "relax a ceiling". What it does is leave
    // the position with nothing to close it.
    renderPage()

    expect(await screen.findByLabelText('停損百分比：不設停損')).toBeInTheDocument()
    expect(screen.getByLabelText('停利百分比：不設停利')).toBeInTheDocument()
    expect(screen.queryByLabelText('停損百分比：不限制')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('停利百分比：不限制')).not.toBeInTheDocument()
  })

  it('says out loud that a switched-off stop-loss never closes the position', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByLabelText('停損百分比')
    await user.click(screen.getByLabelText('停損百分比：不設停損'))

    expect(screen.getByText(/不管跌多少都不會自動賣出/)).toBeInTheDocument()
    expect(screen.getByText(/虧損沒有底線/)).toBeInTheDocument()
  })

  it('words the throttles as more traffic rather than less protection', async () => {
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByLabelText('下單訊號冷卻時間（秒）：不冷卻')).toBeInTheDocument()
    expect(screen.getByLabelText('提醒間隔（秒）：每次都通知')).toBeInTheDocument()

    await user.click(screen.getByLabelText('提醒間隔（秒）：每次都通知'))
    expect(screen.getByText(/每次訊號都通知你/)).toBeInTheDocument()
  })

  it('refuses to save a blank field instead of sending an empty number', async () => {
    // Turning a switch off leaves an empty box on purpose; saving it would
    // 422 at the API, and this page shows no error when a save fails.
    const user = userEvent.setup()
    renderPage()

    const box = await screen.findByLabelText('本金')
    await user.clear(box)

    expect(screen.getByRole('button', { name: '儲存' })).toBeDisabled()
    expect(screen.getByText(/還沒填數字：本金/)).toBeInTheDocument()
  })
})
