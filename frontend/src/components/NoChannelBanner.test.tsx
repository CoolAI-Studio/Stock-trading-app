import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NoChannelBanner } from './NoChannelBanner'
import { api } from '../lib/api'
import type { NotificationChannel } from '../lib/types'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn() },
}))

function channel(overrides: Partial<NotificationChannel> = {}): NotificationChannel {
  return {
    id: 1,
    channel_type: 'telegram',
    label: '我的 Telegram',
    is_enabled: true,
    subscribed_events: null,
    quiet_start_hour: null,
    quiet_end_hour: null,
    last_sent_at: null,
    last_error: null,
    ...overrides,
  } as NotificationChannel
}

function renderBanner() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <NoChannelBanner />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/**
 * 一個沒有出口的提醒系統，跟一個沒有在跑的提醒系統，後果一模一樣。
 *
 * 這個 app 的策略可以全部啟用、worker 可以完全健康、/healthz 可以全綠——而如果
 * 沒有任何一個啟用中的通知管道，條件成立的時候還是不會有人知道。後端早就分得出
 * 這種情況（NotificationLog 的 channel_id 是 NULL 就代表「這一則誰都沒送到」），
 * 但畫面上沒有任何地方在事前講這件事。
 *
 * 跟 WorkerHealthBanner 放在同一個位置（Layout），因為它們是同一種失效：
 * 每一頁都看得見，而不是只有你剛好打開的那一頁。
 */
describe('沒有通知管道的時候要一直講', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('一個管道都沒有 → 講清楚現在提醒不會送到任何地方', async () => {
    vi.mocked(api.get).mockResolvedValue([])

    renderBanner()

    expect(await screen.findByRole('alert')).toHaveTextContent(/不會有任何提醒送到你手上/)
  })

  it('有管道但全部是停用的 → 一樣要講，停用等於沒有', async () => {
    vi.mocked(api.get).mockResolvedValue([channel({ is_enabled: false })])

    renderBanner()

    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })

  it('有一個啟用中 → 閉嘴', async () => {
    vi.mocked(api.get).mockResolvedValue([channel()])

    renderBanner()

    await waitFor(() => expect(api.get).toHaveBeenCalled())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('還在載入的時候不要閃一下', async () => {
    vi.mocked(api.get).mockReturnValue(new Promise(() => {}))

    renderBanner()

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('讀不到就不要再喊一次 —— 後端掛掉是另一個 banner 的事', async () => {
    // Two banners saying different things about the same outage teaches people
    // to skim both.
    vi.mocked(api.get).mockRejectedValue(new Error('backend down'))

    renderBanner()

    await waitFor(() => expect(api.get).toHaveBeenCalled())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('給一條去設定的路，不要只說有問題', async () => {
    vi.mocked(api.get).mockResolvedValue([])

    renderBanner()

    const link = await screen.findByRole('link', { name: /設定通知/ })
    expect(link).toHaveAttribute('href', '/notifications')
  })
})
