import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { StrategyScorecard } from './StrategyScorecard'
import { api } from '../lib/api'
import type { StrategyPerformance } from '../lib/types'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn() },
}))

const TRADED: StrategyPerformance = {
  total_orders: 4,
  filled_orders: 2,
  realized_pnl: '100000',
  open_quantity: '0',
  open_cost: '0',
  bought_value: '1000000',
  sold_value: '1100000',
  notes: ['這是毛損益，沒有扣手續費與證交稅。'],
}

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <StrategyScorecard strategyId={1} />
    </QueryClientProvider>,
  )
}

describe('StrategyScorecard', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows what the strategy actually made', async () => {
    vi.mocked(api.get).mockResolvedValue(TRADED as never)
    renderCard()
    expect(await screen.findByText('100000')).toBeInTheDocument()
  })

  it('says it has not traded rather than showing a zero', async () => {
    // "0 元" reads as "traded and broke even", which is a different and much
    // more informative statement than "has not traded".
    vi.mocked(api.get).mockResolvedValue({
      ...TRADED,
      total_orders: 0,
      filled_orders: 0,
      realized_pnl: null,
    } as never)
    renderCard()

    expect(await screen.findByText('還沒發出過任何訂單。')).toBeInTheDocument()
  })

  it('distinguishes "no signals" from "signals that never filled"', async () => {
    vi.mocked(api.get).mockResolvedValue({
      ...TRADED,
      total_orders: 6,
      filled_orders: 0,
      realized_pnl: null,
    } as never)
    renderCard()

    expect(await screen.findByText(/發過 6 筆訂單/)).toBeInTheDocument()
  })

  it('always says the figure excludes fees', async () => {
    // The backtest charges them and this does not; a number compared across
    // the two without that caveat is a wrong comparison.
    vi.mocked(api.get).mockResolvedValue(TRADED as never)
    renderCard()

    expect(await screen.findByText(/毛損益/)).toBeInTheDocument()
  })

  it('stays out of the way when the report cannot be loaded', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('nope'))
    const { container } = renderCard()
    await vi.waitFor(() => expect(api.get).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })
})
