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
    expect(await screen.findByLabelText(/max position/i)).toHaveValue('0')
  })

  it('saves updated settings', async () => {
    vi.mocked(api.put).mockResolvedValue({ ...SETTINGS, max_position_qty: '500' } as never)
    const user = userEvent.setup()
    renderPage()

    const input = await screen.findByLabelText(/max position/i)
    await user.clear(input)
    await user.type(input, '500')
    await user.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        '/api/risk-settings',
        expect.objectContaining({ max_position_qty: '500' }),
      ),
    )
  })
})
