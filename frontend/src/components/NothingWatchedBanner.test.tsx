import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NothingWatchedBanner } from './NothingWatchedBanner'
import { api } from '../lib/api'
import type { Position, Strategy } from '../lib/types'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn() },
}))

function strategy(overrides: Partial<Strategy> = {}): Strategy {
  return {
    id: 1,
    name: '均線交叉',
    symbol: '2330.TW',
    is_active: true,
    alert_only: false,
    ...overrides,
  } as Strategy
}

function position(overrides: Partial<Position> = {}): Position {
  return {
    symbol: '2330.TW',
    quantity: '1000',
    avg_entry_price: '600',
    realized_pnl: '0',
    opened_at: null,
    ...overrides,
  } as Position
}

function answer({ strategies, positions }: { strategies: Strategy[]; positions: Position[] }) {
  vi.mocked(api.get).mockImplementation((path: string) => {
    if (path.startsWith('/api/strategies')) return Promise.resolve(strategies) as never
    if (path.startsWith('/api/positions')) return Promise.resolve(positions) as never
    throw new Error(`沒有預期到的請求：${path}`)
  })
}

function renderBanner() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <NothingWatchedBanner />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/**
 * 「什麼都沒在盯」跟「壞掉」在畫面上長得一模一樣：全綠。
 *
 * 盯盤迴圈要看的東西是**啟用中的策略**加上**有部位的持股**（market_loop 的
 * `_watched_symbols`），兩者都空的時候它每一輪都無事可做——不會有任何價格提醒。
 *
 * 量出來的（維護者自己那一份，2026-09-06 到 09-08，連續 33.6 小時）：
 *
 *     累計 SQL 12.2 句/小時 = 每 30 分鐘一輪 × 6 句
 *
 * 也就是跨過整個台股盤中和美股夜盤，迴圈一次都沒有走過「開盤」那條快路。而同一段時間
 * 裡，`/healthz` 的每一格都是 ok、worker 心跳正常、資料庫漂亮地睡著。
 *
 * 這正是 NoChannelBanner 那一句話的另一半：那邊是「提醒沒有地方可以送」，這邊是「沒有
 * 東西會產生提醒」。後果一樣，而且都不會有任何東西變紅。
 */
describe('沒有東西被盯著的時候', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset()
  })

  it('說出來——因為畫面上其他每一格都是正常的', async () => {
    answer({ strategies: [], positions: [] })

    renderBanner()

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('不會有任何提醒')
    })
  })

  it('停用中的策略不算數', async () => {
    answer({ strategies: [strategy({ is_active: false })], positions: [] })

    renderBanner()

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
  })

  it('有一支在跑就不要吵他', async () => {
    answer({ strategies: [strategy({ is_active: true })], positions: [] })

    renderBanner()

    await waitFor(() => expect(api.get).toHaveBeenCalled())
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('沒有策略但有持股也不算——停損還在看著它', async () => {
    answer({ strategies: [], positions: [position()] })

    renderBanner()

    await waitFor(() => expect(api.get).toHaveBeenCalled())
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('數量是 0 的持股不算，那是一筆平掉的紀錄', async () => {
    answer({ strategies: [], positions: [position({ quantity: '0' })] })

    renderBanner()

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
  })

  it('問不到的時候閉嘴，不要把「不知道」喊成「你什麼都沒設」', async () => {
    // 後端掛掉本來就有 WorkerHealthBanner 在講，這裡再喊一次只是把這句話變成雜訊——
    // 而一句被學會忽略的警告，跟沒有那句話是同一件事。
    vi.mocked(api.get).mockRejectedValue(new Error('後端沒回應'))

    renderBanner()

    await waitFor(() => expect(api.get).toHaveBeenCalled())
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('帶他去建立策略，不是只告訴他有問題', async () => {
    answer({ strategies: [], positions: [] })

    renderBanner()

    await waitFor(() => {
      expect(screen.getByRole('link')).toHaveAttribute('href', '/strategies')
    })
  })
})
