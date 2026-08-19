import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { OrdersPage } from './OrdersPage'
import { api } from '../lib/api'
import type { Order } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn() },
}))

const PENDING_ORDER: Order = {
  id: 1,
  strategy_id: null,
  source: 'manual',
  symbol: 'AAPL',
  side: 'buy',
  quantity: '10',
  signal_price: '150',
  status: 'pending',
  risk_notes: null,
  reject_reason: null,
  fill_price: null,
  filled_quantity: null,
  filled_at: null,
  decided_at: null,
  broker_ref: null,
  created_at: '2026-08-16T00:00:00Z',
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <OrdersPage />
    </QueryClientProvider>,
  )
}

describe('OrdersPage', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('status=pending')) return [PENDING_ORDER] as never
      return [] as never
    })
    vi.mocked(api.post).mockResolvedValue({ ...PENDING_ORDER, status: 'confirmed' } as never)
  })

  it('renders a pending order', async () => {
    renderPage()
    expect(await screen.findByText('AAPL')).toBeInTheDocument()
    expect(screen.getByText('買進')).toBeInTheDocument()
  })

  it('confirms an order with the entered fill price', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('AAPL')
    const fillPriceInput = screen.getByLabelText('成交價')
    await user.clear(fillPriceInput)
    await user.type(fillPriceInput, '151.25')
    await user.click(screen.getByRole('button', { name: '確認' }))

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/orders/1/confirm', { fill_price: '151.25' }),
    )
  })

  it('rejects an order', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('AAPL')
    await user.click(screen.getByRole('button', { name: '拒絕' }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/orders/1/reject', {}))
  })

  it('shows an empty state when there are no pending orders', async () => {
    vi.mocked(api.get).mockResolvedValue([] as never)
    renderPage()

    expect(await screen.findByText('目前沒有待確認訂單。')).toBeInTheDocument()
  })

  it('creates a manual order', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('AAPL')
    await user.click(screen.getByRole('button', { name: '新增訂單' }))
    await user.type(screen.getByLabelText('代號'), 'tsla')
    await user.click(screen.getByRole('radio', { name: '賣出' }))
    await user.type(screen.getByLabelText('數量'), '5')
    await user.type(screen.getByLabelText('預期價格（選填）'), '200')
    await user.click(screen.getByRole('button', { name: '送出' }))

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/orders', {
        symbol: 'tsla',
        side: 'sell',
        quantity: '5',
        signal_price: '200',
      }),
    )
  })
})

describe('a fill that came back smaller than the order', () => {
  const PARTIAL: Order = {
    ...PENDING_ORDER,
    id: 2,
    quantity: '10',
    status: 'confirmed',
    fill_price: '150',
    filled_quantity: '2',
    filled_at: '2026-08-16T00:05:00Z',
  }

  beforeEach(() => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('status=pending')) return [] as never
      return [PARTIAL] as never
    })
  })

  async function quantityCell(): Promise<HTMLElement> {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /歷史/ }))
    const row = (await screen.findByText('AAPL')).closest('tr') as HTMLElement
    // The whole row is no good to assert on: the timestamp column renders
    // 2026/8/16, so a bare "no slash" check passes or fails on the date.
    return row.querySelector('[data-cell="quantity"]') as HTMLElement
  }

  it('shows what filled, not just what was asked for', async () => {
    expect(await quantityCell()).toHaveTextContent('2 / 10')
  })

  it('does not clutter a full fill with a redundant fraction', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('status=pending')) return [] as never
      return [{ ...PARTIAL, filled_quantity: '10' }] as never
    })
    expect((await quantityCell()).textContent).toBe('10')
  })
})
