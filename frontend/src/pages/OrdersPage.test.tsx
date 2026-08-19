import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { OrdersPage } from './OrdersPage'
import { ApiError, api } from '../lib/api'
import type { Order } from '../lib/types'

// The row reads ApiError to word a failure, so the mock has to carry the
// real class -- a stub would never match `instanceof`.
vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
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
      expect(api.post).toHaveBeenCalledWith('/api/orders/1/confirm', {
        fill_price: '151.25',
        quantity: '10',
      }),
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

describe('confirming a fill that came back smaller than the order', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('status=pending')) return [PENDING_ORDER] as never
      return [] as never
    })
  })

  it('sends the quantity that actually filled', async () => {
    // The backend has honoured a partial fill all along -- it is what reaches
    // the position, and it is what the capital gate is billed for. The UI
    // never offered the box, so every partial fill was booked as a full one.
    vi.mocked(api.post).mockResolvedValue({} as never)
    const user = userEvent.setup()
    renderPage()

    const price = await screen.findByLabelText('成交價')
    await user.clear(price)
    await user.type(price, '151.25')
    const qty = screen.getByLabelText('成交數量')
    await user.clear(qty)
    await user.type(qty, '4')
    await user.click(screen.getByRole('button', { name: '確認' }))

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/orders/1/confirm', {
        fill_price: '151.25',
        quantity: '4',
      }),
    )
  })

  it('defaults to the whole order, so the common case needs no typing', async () => {
    vi.mocked(api.post).mockResolvedValue({} as never)
    renderPage()

    expect(await screen.findByLabelText('成交數量')).toHaveValue('10')
  })

  it('will not send a quantity larger than the order', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.type(await screen.findByLabelText('成交價'), '151.25')
    const qty = screen.getByLabelText('成交數量')
    await user.clear(qty)
    await user.type(qty, '11')

    expect(screen.getByRole('button', { name: '確認' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('不能超過委託數量')
  })
})

describe('when confirming or rejecting fails', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('status=pending')) return [PENDING_ORDER] as never
      return [] as never
    })
  })

  it('says why, instead of leaving the row looking untouched', async () => {
    // Worst case is real: on a broker failure the backend commits the order as
    // FAILED and then returns 422, so the row went on rendering 待確認 while
    // the database said otherwise -- and pressing again is the natural move.
    vi.mocked(api.post).mockRejectedValue(new ApiError(422, '持倉不足，無法賣出 10 股'))
    const user = userEvent.setup()
    renderPage()

    await user.type(await screen.findByLabelText('成交價'), '151.25')
    await user.click(screen.getByRole('button', { name: '確認' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('持倉不足，無法賣出 10 股')
  })

  it('reports a failed rejection too', async () => {
    vi.mocked(api.post).mockRejectedValue(new ApiError(409, 'Order is already confirmed'))
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: '拒絕' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('already confirmed')
  })
})

describe('deleting from the order history', () => {
  function historyOf(status: Order['status']) {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('status=pending')) return [] as never
      return [{ ...PENDING_ORDER, id: 9, status }] as never
    })
  }

  async function openHistory() {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /歷史/ }))
    return user
  }

  it('offers delete on a rejected order', async () => {
    historyOf('rejected')
    await openHistory()
    expect(await screen.findByRole('button', { name: '刪除' })).toBeInTheDocument()
  })

  it('does not offer delete on a confirmed order', async () => {
    // The backend refuses it: a confirmed order moved a position and is
    // counted by the per-strategy capital gate. Better not to offer a button
    // whose only outcome is an error.
    historyOf('confirmed')
    await openHistory()
    await screen.findByText('AAPL')
    expect(screen.queryByRole('button', { name: '刪除' })).not.toBeInTheDocument()
  })

  it('deletes after confirming', async () => {
    historyOf('expired')
    vi.mocked(api.delete).mockResolvedValue(undefined as never)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const user = await openHistory()
    await user.click(await screen.findByRole('button', { name: '刪除' }))

    await waitFor(() => expect(api.delete).toHaveBeenCalledWith('/api/orders/9'))
  })
})

describe('why this sell order exists', () => {
  it('marks a stop-loss exit apart from an ordinary strategy signal', async () => {
    // Two very different levels of urgency that used to look identical in the
    // pending list. The backend has stamped the trigger on the order since
    // the exit scan was written; nothing rendered it.
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('status=pending'))
        return [
          {
            ...PENDING_ORDER,
            side: 'sell',
            source: 'strategy',
            risk_notes: { trigger: 'stop_loss' },
          },
        ] as never
      return [] as never
    })
    renderPage()

    const row = (await screen.findByText('AAPL')).closest('tr') as HTMLElement
    expect(row).toHaveTextContent('停損')
  })

  it('marks a take-profit exit too', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('status=pending'))
        return [
          {
            ...PENDING_ORDER,
            side: 'sell',
            source: 'strategy',
            risk_notes: { trigger: 'take_profit' },
          },
        ] as never
      return [] as never
    })
    renderPage()

    const row = (await screen.findByText('AAPL')).closest('tr') as HTMLElement
    expect(row).toHaveTextContent('停利')
  })

  it('says nothing extra for an ordinary signal', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('status=pending')) return [PENDING_ORDER] as never
      return [] as never
    })
    renderPage()

    const row = (await screen.findByText('AAPL')).closest('tr') as HTMLElement
    expect(row).not.toHaveTextContent('停損')
  })

  it('explains a rejection in the history rather than just saying 已拒絕', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('status=pending')) return [] as never
      return [
        { ...PENDING_ORDER, status: 'rejected', reject_reason: '買進後的總持倉成本會超過本金上限' },
      ] as never
    })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /歷史/ }))

    const row = (await screen.findByText('AAPL')).closest('tr') as HTMLElement
    expect(row).toHaveTextContent('本金上限')
  })
})

describe('reaching older orders', () => {
  function pageOf(count: number, startId = 100) {
    return Array.from({ length: count }, (_, i) => ({
      ...PENDING_ORDER,
      id: startId + i,
      status: 'confirmed' as const,
      symbol: `SYM${i}`,
    }))
  }

  it('asks the backend for a page rather than taking whatever comes', async () => {
    // The list fetched a fixed first page and said nothing about it, so after
    // a few weeks the history simply stopped at fifty.
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('status=pending')) return [] as never
      return pageOf(50) as never
    })
    renderPage()

    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith(expect.stringContaining('limit=50&offset=0')),
    )
  })

  it('steps to the next page of history', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('status=pending')) return [] as never
      return pageOf(50) as never
    })
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: /歷史/ }))
    await user.click(await screen.findByRole('button', { name: '下一頁' }))

    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith(expect.stringContaining('offset=50')),
    )
  })

  it('filters by symbol on the backend, not by hiding rows locally', async () => {
    // Hiding rows locally would filter one page and call it the answer.
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('status=pending')) return [] as never
      return pageOf(3) as never
    })
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: /歷史/ }))
    await user.type(screen.getByLabelText('只看某一檔'), '2330.tw')

    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith(expect.stringContaining('symbol=2330.TW')),
    )
  })

  it('goes back to the first page when the filter changes', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('status=pending')) return [] as never
      return pageOf(50) as never
    })
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: /歷史/ }))
    await user.click(await screen.findByRole('button', { name: '下一頁' }))
    await user.type(screen.getByLabelText('只看某一檔'), 'A')

    // An unchanged offset would show an empty page 2 of a one-page result.
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith(expect.stringContaining('offset=0&symbol=A')),
    )
  })
})
