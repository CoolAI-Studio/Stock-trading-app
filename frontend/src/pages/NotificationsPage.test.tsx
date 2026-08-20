import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NotificationsPage } from './NotificationsPage'
import { api } from '../lib/api'
import * as push from '../lib/push'
import * as platform from '../lib/platform'
import type { NotificationChannel, NotificationLog } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

vi.mock('../lib/push', () => ({
  isPushSupported: vi.fn(() => true),
  requestPushPermission: vi.fn(async () => 'granted' as NotificationPermission),
  currentSubscriptionEndpoint: vi.fn(async () => null as string | null),
  subscribeToPush: vi.fn(),
  unsubscribeFromPush: vi.fn(),
}))

// jsdom has no PushManager, so the real reader would answer "unsupported" for
// every test in this file. Stubbed to "ready" by default; the tests that are
// ABOUT the other answers override it.
vi.mock('../lib/platform', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/platform')>()),
  currentPushAvailability: vi.fn(() => ({ kind: 'ready' as const })),
}))

const CHANNEL: NotificationChannel = {
  id: 1,
  channel_type: 'telegram',
  label: 'phone',
  is_enabled: true,
  subscribed_events: null,
  quiet_start_hour: null,
  quiet_end_hour: null,
  last_sent_at: null,
  last_error: null,
  config_preview: 'telegram: bot_token=****abcd, chat_id=999',
  push_endpoint: null,
}

const WEB_PUSH_CHANNEL: NotificationChannel = {
  id: 2,
  channel_type: 'web_push',
  label: 'my-laptop',
  is_enabled: true,
  subscribed_events: null,
  quiet_start_hour: null,
  quiet_end_hour: null,
  last_sent_at: null,
  last_error: null,
  config_preview: 'web_push: endpoint=https://push.example.com/x',
  push_endpoint: 'https://push.example.com/x',
}

const LOG: NotificationLog = {
  id: 1,
  channel_id: 1,
  order_id: null,
  event: 'test',
  status: 'sent',
  error: null,
  created_at: '2026-08-16T00:00:00Z',
  delivered_at: null,
  delivery_state: 'sent',
  attempts: 1,
  max_attempts: 5,
  next_retry_at: null,
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <NotificationsPage />
    </QueryClientProvider>,
  )
}

describe('NotificationsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/notifications/channels') return [CHANNEL] as never
      if (path === '/api/notifications/logs') return [LOG] as never
      return [] as never
    })
  })

  it('lists channels with their masked preview, never a raw secret', async () => {
    renderPage()
    const previewCell = await screen.findByText(/bot_token=\*+abcd/)
    const row = previewCell.closest('tr')!
    expect(within(row).getByText('phone')).toBeInTheDocument()
  })

  it('sends a test notification', async () => {
    vi.mocked(api.post).mockResolvedValue({ ok: true, error: null } as never)
    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/bot_token=\*+abcd/)
    await user.click(screen.getByRole('button', { name: '測試' }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/notifications/channels/1/test'))
    expect(await screen.findByText('已送出。')).toBeInTheDocument()
  })

  it('creates a new telegram channel', async () => {
    vi.mocked(api.post).mockResolvedValue(CHANNEL as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增管道' }))
    await user.type(screen.getByLabelText('名稱'), 'my-phone')
    await user.type(screen.getByLabelText(/機器人權杖/), 't123')
    await user.type(screen.getByLabelText(/聊天室代號/), '555')
    await user.click(screen.getByRole('button', { name: '建立' }))

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/notifications/channels', {
        channel_type: 'telegram',
        label: 'my-phone',
        config: { bot_token: 't123', chat_id: '555' },
        subscribed_events: null,
        quiet_start_hour: null,
        quiet_end_hour: null,
      }),
    )
  })

  it('edits a channel label without resending unchanged secret fields', async () => {
    vi.mocked(api.patch).mockResolvedValue(CHANNEL as never)
    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/bot_token=\*+abcd/)
    await user.click(screen.getByRole('button', { name: '編輯' }))

    const labelInput = screen.getByLabelText('名稱')
    await user.clear(labelInput)
    await user.type(labelInput, 'renamed')
    await user.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith('/api/notifications/channels/1', {
        label: 'renamed',
        is_enabled: true,
        subscribed_events: null,
        quiet_start_hour: null,
        quiet_end_hour: null,
      }),
    )
  })

  it('edits a channel and replaces its secret config when provided', async () => {
    vi.mocked(api.patch).mockResolvedValue(CHANNEL as never)
    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/bot_token=\*+abcd/)
    await user.click(screen.getByRole('button', { name: '編輯' }))
    await user.type(screen.getByLabelText(/機器人權杖/), 'newtoken')
    await user.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() =>
      expect(api.patch).toHaveBeenCalledWith('/api/notifications/channels/1', {
        label: 'phone',
        is_enabled: true,
        subscribed_events: null,
        quiet_start_hour: null,
        quiet_end_hour: null,
        config: { bot_token: 'newtoken', chat_id: '' },
      }),
    )
  })

  it('shows notification send logs', async () => {
    renderPage()

    expect(await screen.findByText('已送出')).toBeInTheDocument()
    expect(screen.getByText('test')).toBeInTheDocument()
  })

  it('creates a web push channel via the browser subscribe flow', async () => {
    vi.mocked(push.subscribeToPush).mockResolvedValue({
      endpoint: 'https://push.example.com/x',
      p256dh: 'p',
      auth: 'a',
    })
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/notifications/channels') return [CHANNEL] as never
      if (path === '/api/notifications/logs') return [LOG] as never
      if (path === '/api/notifications/push/vapid-public-key') return { public_key: 'vapid-key' } as never
      return [] as never
    })
    vi.mocked(api.post).mockResolvedValue(WEB_PUSH_CHANNEL as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '新增管道' }))
    await user.click(screen.getByRole('radio', { name: '瀏覽器推播' }))
    await user.type(screen.getByLabelText('名稱'), 'my-laptop')
    await user.click(screen.getByRole('button', { name: '建立' }))

    await waitFor(() => expect(push.subscribeToPush).toHaveBeenCalledWith('vapid-key'))
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/notifications/channels', {
        channel_type: 'web_push',
        label: 'my-laptop',
        config: { endpoint: 'https://push.example.com/x', p256dh: 'p', auth: 'a' },
        subscribed_events: null,
        quiet_start_hour: null,
        quiet_end_hour: null,
      }),
    )
  })

  it('unsubscribes the browser push subscription when the deleted row IS this device', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/notifications/channels') return [WEB_PUSH_CHANNEL] as never
      if (path === '/api/notifications/logs') return [] as never
      return [] as never
    })
    // The row has to BE this device now; unsubscribing on any web_push row was
    // the bug, because it disconnects whichever browser is doing the deleting.
    vi.mocked(push.currentSubscriptionEndpoint).mockResolvedValue(WEB_PUSH_CHANNEL.push_endpoint)
    vi.mocked(api.delete).mockResolvedValue(undefined as never)
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('my-laptop')
    await user.click(screen.getByRole('button', { name: '刪除' }))

    await waitFor(() => expect(push.unsubscribeFromPush).toHaveBeenCalled())
    expect(api.delete).toHaveBeenCalledWith('/api/notifications/channels/2')
  })
})

describe('SMTP credentials', () => {
  it('sends the account and password, so an authenticating mail server works', async () => {
    // Gmail, Outlook and every hosted SMTP need these. The backend read all
    // six fields from day one; the form sent three, so the only reachable
    // mail server was an unauthenticated one on a local network.
    vi.mocked(api.post).mockResolvedValue({} as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: '新增管道' }))
    await user.click(screen.getByRole('radio', { name: 'email' }))
    await user.type(screen.getByLabelText('名稱'), 'gmail')
    await user.type(screen.getByLabelText('SMTP 主機'), 'smtp.gmail.com')
    await user.type(screen.getByLabelText('帳號'), 'me@gmail.com')
    await user.type(screen.getByLabelText('密碼'), 'app-password')
    await user.type(screen.getByLabelText('寄件人信箱'), 'me@gmail.com')
    await user.type(screen.getByLabelText('收件人信箱'), 'me@gmail.com')
    await user.click(screen.getByRole('button', { name: '建立' }))

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        '/api/notifications/channels',
        expect.objectContaining({
          config: expect.objectContaining({
            host: 'smtp.gmail.com',
            port: 587,
            username: 'me@gmail.com',
            password: 'app-password',
            use_tls: true,
          }),
        }),
      ),
    )
  })

  it('masks the password field', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: '新增管道' }))
    await user.click(screen.getByRole('radio', { name: 'email' }))

    expect(screen.getByLabelText('密碼')).toHaveAttribute('type', 'password')
  })
})

describe('whether a channel is actually working', () => {
  it('marks a disabled channel as switched off', async () => {
    // It used to look identical to an enabled one, so a channel the owner (or
    // the dead-endpoint sweep) turned off read as still delivering.
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('channels')) return [{ ...CHANNEL, is_enabled: false }] as never
      return [] as never
    })
    renderPage()

    const row = (await screen.findByText(CHANNEL.label)).closest('tr') as HTMLElement
    expect(within(row).getByTestId('channel-health')).toHaveTextContent('已停用')
  })

  it('shows the reason a channel stopped working, not just that it did', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('channels'))
        return [
          {
            ...CHANNEL,
            is_enabled: false,
            last_error: '瀏覽器的推播訂閱已失效（HTTP 410），這個管道已自動停用。請重新建立。',
          },
        ] as never
      return [] as never
    })
    renderPage()

    const row = (await screen.findByText(CHANNEL.label)).closest('tr') as HTMLElement
    expect(row).toHaveTextContent('請重新建立')
  })

  it('flags a channel that is still enabled but failing', async () => {
    // The dangerous middle state: on, and quietly not delivering.
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('channels'))
        return [{ ...CHANNEL, is_enabled: true, last_error: 'Read timed out' }] as never
      return [] as never
    })
    renderPage()

    const row = (await screen.findByText(CHANNEL.label)).closest('tr') as HTMLElement
    expect(within(row).getByTestId('channel-health')).toHaveTextContent('上次失敗')
  })

  it('says a working channel is working', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('channels'))
        return [
          { ...CHANNEL, is_enabled: true, last_error: null, last_sent_at: '2026-08-19T01:30:00Z' },
        ] as never
      return [] as never
    })
    renderPage()

    const row = (await screen.findByText(CHANNEL.label)).closest('tr') as HTMLElement
    expect(within(row).getByTestId('channel-health')).toHaveTextContent('正常')
  })
})

describe('choosing which events a channel receives', () => {
  // Asserting on mock.calls[0] needs a clean slate; earlier tests in this file
  // leave their own calls behind.
  beforeEach(() => vi.clearAllMocks())

  it('sends the chosen subset', async () => {
    // The column and the dispatcher's filter have existed all along; the form
    // never sent the field, so every enabled channel received all four kinds
    // and the owner got washed out by order.updated.
    vi.mocked(api.post).mockResolvedValue({} as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: '新增管道' }))
    await user.type(screen.getByLabelText('名稱'), 'quiet-phone')
    await user.type(screen.getByLabelText(/機器人權杖/), 't')
    await user.type(screen.getByLabelText(/聊天室代號/), '1')
    // Start from "all selected"; drop the noisy one.
    await user.click(screen.getByLabelText('訂單狀態變更'))
    await user.click(screen.getByRole('button', { name: '建立' }))

    await waitFor(() => expect(api.post).toHaveBeenCalled())
    const payload = vi.mocked(api.post).mock.calls[0][1] as { subscribed_events: string[] }
    expect(payload.subscribed_events).not.toContain('order.updated')
    expect(payload.subscribed_events).toContain('order.created')
  })

  it('sends null when nothing is picked, which the backend reads as all', async () => {
    // An empty list would be a channel that is enabled and never fires.
    vi.mocked(api.post).mockResolvedValue({} as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: '新增管道' }))
    await user.type(screen.getByLabelText('名稱'), 'all-events')
    await user.type(screen.getByLabelText(/機器人權杖/), 't')
    await user.type(screen.getByLabelText(/聊天室代號/), '1')
    for (const label of [
      '新的待確認訂單',
      '訂單狀態變更',
      '策略提醒（只提醒模式）',
      '策略發生錯誤',
    ]) {
      await user.click(screen.getByLabelText(label))
    }
    await user.click(screen.getByRole('button', { name: '建立' }))

    await waitFor(() => expect(api.post).toHaveBeenCalled())
    const payload = vi.mocked(api.post).mock.calls[0][1] as { subscribed_events: unknown }
    expect(payload.subscribed_events).toBeNull()
  })

  it('shows everything ticked for a channel that never chose', async () => {
    // null means all, and the boxes have to say so rather than looking like
    // a channel subscribed to nothing.
    renderPage()
    await user_open_edit()

    expect(screen.getByLabelText('新的待確認訂單')).toBeChecked()
    expect(screen.getByLabelText('策略發生錯誤')).toBeChecked()
  })
})

async function user_open_edit() {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: '編輯' }))
}

describe('not being woken at three in the morning', () => {
  beforeEach(() => vi.clearAllMocks())

  it('sends the window when one is set', async () => {
    // The alternative the owner had was switching the channel off, which
    // takes the stop-loss alerts with it.
    vi.mocked(api.post).mockResolvedValue({} as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: '新增管道' }))
    await user.type(screen.getByLabelText('名稱'), 'phone')
    await user.type(screen.getByLabelText(/機器人權杖/), 't')
    await user.type(screen.getByLabelText(/聊天室代號/), '1')
    await user.click(screen.getByLabelText('設定靜音時段'))
    await user.click(screen.getByRole('button', { name: '建立' }))

    await waitFor(() => expect(api.post).toHaveBeenCalled())
    const payload = vi.mocked(api.post).mock.calls[0][1] as Record<string, unknown>
    expect(payload.quiet_start_hour).toBe(23)
    expect(payload.quiet_end_hour).toBe(7)
  })

  it('promises the notification is only delayed, not lost', async () => {
    // "Quiet hours" reads as "you will not be told"; here it means "you will
    // be told at seven", and that difference is the whole point.
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: '新增管道' }))
    await user.click(screen.getByLabelText('設定靜音時段'))

    expect(screen.getByText(/時段一結束就補送/)).toBeInTheDocument()
  })

  it('warns that US market hours are the middle of the night here', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: '新增管道' }))
    await user.click(screen.getByLabelText('設定靜音時段'))

    expect(screen.getByText(/美股盤中是台灣的半夜/)).toBeInTheDocument()
  })

  it('sends nulls when the window is switched back off', async () => {
    vi.mocked(api.post).mockResolvedValue({} as never)
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: '新增管道' }))
    await user.type(screen.getByLabelText('名稱'), 'always-on')
    await user.type(screen.getByLabelText(/機器人權杖/), 't')
    await user.type(screen.getByLabelText(/聊天室代號/), '1')
    await user.click(screen.getByLabelText('設定靜音時段'))
    await user.click(screen.getByLabelText('設定靜音時段'))
    await user.click(screen.getByRole('button', { name: '建立' }))

    await waitFor(() => expect(api.post).toHaveBeenCalled())
    const payload = vi.mocked(api.post).mock.calls[0][1] as Record<string, unknown>
    expect(payload.quiet_start_hour).toBeNull()
  })
})


// --- what an iPhone is told --------------------------------------------------

describe('iPhone 上的推播', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/notifications/channels') return [] as never
      return [] as never
    })
  })

  async function openPushForm() {
    renderPage()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: '新增管道' }))
    await user.click(screen.getByRole('radio', { name: '瀏覽器推播' }))
    return user
  }

  it('在 Safari 裡打開時給的是步驟，不是「不支援」', async () => {
    vi.mocked(platform.currentPushAvailability).mockReturnValue({
      kind: 'needs-install',
      message: '在 Safari 下方按「分享」→「加入主畫面」，然後從主畫面打開這個 app。',
    })

    await openPushForm()

    expect(screen.getByText(/分享/)).toBeInTheDocument()
    expect(screen.queryByText(/瀏覽器不支援/)).not.toBeInTheDocument()
  })

  it('真的不支援時照實說', async () => {
    vi.mocked(platform.currentPushAvailability).mockReturnValue({
      kind: 'unsupported',
      message: '這個瀏覽器沒有 Web 推播功能，設定了也收不到。',
    })

    await openPushForm()

    expect(screen.getByText(/沒有 Web 推播功能/)).toBeInTheDocument()
  })
})


// --- permission has to be asked for by the click itself ----------------------
//
// Notification.requestPermission() needs transient user activation, and an
// intervening await spends it. The mutation used to fetch the VAPID key over
// the network and ask afterwards, so on Safari -- every iPhone -- the sheet
// never appeared: press 建立, nothing happens, conclude push does not work on
// this phone. These pin the ordering that fixes it.

describe('推播權限的取得時機', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // clearAllMocks resets call history, NOT implementations -- so the
    // 'unsupported' verdict the previous describe installs survives into this
    // one and leaves the 建立 button disabled. Put it back explicitly rather
    // than depending on file order.
    vi.mocked(platform.currentPushAvailability).mockReturnValue({ kind: 'ready' })
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/notifications/channels') return [] as never
      if (path === '/api/notifications/logs') return [] as never
      if (path === '/api/notifications/push/vapid-public-key')
        return { public_key: 'vapid-key' } as never
      return [] as never
    })
    vi.mocked(api.post).mockResolvedValue(WEB_PUSH_CHANNEL as never)
    vi.mocked(push.subscribeToPush).mockResolvedValue({
      endpoint: 'https://push.example.com/x',
      p256dh: 'p',
      auth: 'a',
    })
    vi.mocked(push.requestPushPermission).mockResolvedValue('granted')
  })

  async function pressCreate() {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '新增管道' }))
    await user.click(screen.getByRole('radio', { name: '瀏覽器推播' }))
    await user.type(screen.getByLabelText('名稱'), 'my-iphone')
    await user.click(screen.getByRole('button', { name: '建立' }))
  }

  it('先要權限，才去拿 VAPID 金鑰', async () => {
    const order: string[] = []
    vi.mocked(push.requestPushPermission).mockImplementation(async () => {
      order.push('permission')
      return 'granted'
    })
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/notifications/push/vapid-public-key') {
        order.push('vapid')
        return { public_key: 'vapid-key' } as never
      }
      if (path === '/api/notifications/channels') return [] as never
      return [] as never
    })

    await pressCreate()

    await waitFor(() => expect(order).toContain('vapid'))
    expect(order[0]).toBe('permission')
  })

  it('被拒絕就不要建立一個永遠收不到東西的管道', async () => {
    vi.mocked(push.requestPushPermission).mockResolvedValue('denied')

    await pressCreate()

    await waitFor(() => expect(screen.getByText(/通知權限/)).toBeInTheDocument())
    expect(api.post).not.toHaveBeenCalled()
  })

  it('被拒絕時要說去哪裡改，不是只說失敗', async () => {
    vi.mocked(push.requestPushPermission).mockResolvedValue('denied')

    await pressCreate()

    // Specific enough not to match the unrelated 「設定靜音時段」 heading: the
    // point is that the message names the place the owner has to go.
    await waitFor(() => expect(screen.getByText(/裝置的「設定」/)).toBeInTheDocument())
  })

  it('拿到權限就照原本的流程建立', async () => {
    await pressCreate()

    await waitFor(() => expect(push.subscribeToPush).toHaveBeenCalledWith('vapid-key'))
    expect(api.post).toHaveBeenCalled()
  })
})


// --- deleting a push channel must not disconnect a different device ----------
//
// unsubscribeFromPush() acts on whatever subscription THIS browser holds. The
// delete used to call it for any web_push row, so tidying a stale "iPhone" row
// away from a laptop unsubscribed the laptop -- whose own row stayed in the
// list looking perfectly healthy and never delivered again. It also ran before
// the DELETE, so a failed request left the device disconnected with the row
// still there.

describe('刪除推播管道', () => {
  const THIS_DEVICE = 'https://push.example.com/this-device'
  const OTHER_DEVICE = 'https://push.example.com/other-device'

  function channel(id: number, endpoint: string) {
    return { ...WEB_PUSH_CHANNEL, id, label: `device-${id}`, push_endpoint: endpoint }
  }

  beforeEach(() => {
    vi.clearAllMocks()
    // DeleteButton guards with window.confirm, which jsdom does not implement.
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(platform.currentPushAvailability).mockReturnValue({ kind: 'ready' })
    vi.mocked(push.currentSubscriptionEndpoint).mockResolvedValue(THIS_DEVICE)
    vi.mocked(api.delete).mockResolvedValue(undefined as never)
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/notifications/channels')
        return [channel(1, THIS_DEVICE), channel(2, OTHER_DEVICE)] as never
      return [] as never
    })
  })

  async function remove(label: string) {
    const user = userEvent.setup()
    renderPage()
    const row = (await screen.findByText(label)).closest('tr') as HTMLElement
    await user.click(within(row).getByRole('button', { name: '刪除' }))
  }

  it('刪掉別台裝置的管道時，不要動到自己這台的訂閱', async () => {
    await remove('device-2')

    await waitFor(() => expect(api.delete).toHaveBeenCalledWith('/api/notifications/channels/2'))
    expect(push.unsubscribeFromPush).not.toHaveBeenCalled()
  })

  it('刪掉自己這台的管道時才解除訂閱', async () => {
    await remove('device-1')

    await waitFor(() => expect(push.unsubscribeFromPush).toHaveBeenCalled())
  })

  it('先刪伺服器上的資料，成功了才解除訂閱', async () => {
    // Reversed, a failed DELETE leaves a row that looks healthy and can never
    // deliver -- the worst of both outcomes.
    const order: string[] = []
    vi.mocked(api.delete).mockImplementation(async () => {
      order.push('delete')
      return undefined as never
    })
    vi.mocked(push.unsubscribeFromPush).mockImplementation(async () => {
      order.push('unsubscribe')
    })

    await remove('device-1')

    await waitFor(() => expect(order).toEqual(['delete', 'unsubscribe']))
  })

  it('伺服器刪除失敗就完全不要解除訂閱', async () => {
    vi.mocked(api.delete).mockRejectedValue(new Error('nope'))

    await remove('device-1')

    await waitFor(() => expect(api.delete).toHaveBeenCalled())
    expect(push.unsubscribeFromPush).not.toHaveBeenCalled()
  })
})

// --- the test button has to tell the truth ----------------------------------
//
// It reported 已送出 whenever the push service returned 2xx. RFC 8030 §5 says
// exactly what that means: "A 201 (Created) response indicates that the push
// message was accepted... This does not indicate that the message was
// delivered to the user agent." Apple returns 201 the moment it accepts a
// message for later delivery, so the test passed with the phone switched off,
// with notifications disabled for the web app, and with a subscription iOS had
// already discarded. The one button whose whole job is to answer 「提醒到底會不
// 會送到？」 answered yes when the answer was no.

describe('測試按鈕的送達回報', () => {
  const DEVICE_CHANNEL = { ...WEB_PUSH_CHANNEL, id: 2 }

  function logRow(delivered: string | null) {
    return {
      id: 55,
      channel_id: 2,
      order_id: null,
      event: 'test',
      status: 'sent' as const,
      error: null,
      created_at: '2026-08-20T00:00:00Z',
      delivered_at: delivered,
      delivery_state: 'sent' as const,
      attempts: 1,
      max_attempts: 5,
      next_retry_at: null,
    }
  }

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(platform.currentPushAvailability).mockReturnValue({ kind: 'ready' })
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/notifications/channels') return [DEVICE_CHANNEL] as never
      if (path === '/api/notifications/logs') return [] as never
      if (path === '/api/notifications/logs/55') return logRow(null) as never
      return [] as never
    })
    vi.mocked(api.post).mockResolvedValue({ ok: true, error: null, log_id: 55 } as never)
  })

  async function pressTest() {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('my-laptop')
    await user.click(screen.getByRole('button', { name: '測試' }))
  }

  it('裝置回報收到了才說「已送達」', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/notifications/channels') return [DEVICE_CHANNEL] as never
      if (path === '/api/notifications/logs') return [] as never
      if (path === '/api/notifications/logs/55')
        return logRow('2026-08-20T00:00:05Z') as never
      return [] as never
    })

    await pressTest()

    expect(await screen.findByText(/已送達/)).toBeInTheDocument()
  })

  it('推播服務收下了但裝置沒回報時，不能講成成功', async () => {
    // The exact false pass this whole feature exists to remove.
    await pressTest()

    expect(await screen.findByText(/沒有回報/, {}, { timeout: 20000 })).toBeInTheDocument()
  }, 30000)

  it('沒回報時要列出可能的原因，不要只說失敗', async () => {
    await pressTest()

    const message = await screen.findByText(/沒有回報/, {}, { timeout: 20000 })
    expect(message.textContent).toMatch(/通知|離線|失效/)
  }, 30000)

  it('送不出去時照舊講失敗，不要進入等待', async () => {
    vi.mocked(api.post).mockResolvedValue({ ok: false, error: 'HTTP 410', log_id: 56 } as never)

    await pressTest()

    expect(await screen.findByText(/失敗/)).toBeInTheDocument()
    expect(api.get).not.toHaveBeenCalledWith('/api/notifications/logs/56')
  })
})

// --- an alert that reached nobody ------------------------------------------
//
// The ledger's most important row. Before it existed, an alert raised for
// somebody with no enabled channel returned silently, so 發送紀錄 looked
// identical to an afternoon on which nothing had happened -- and the owner
// could not find the failure even by going and looking for it.

describe('沒有送到任何管道的紀錄', () => {
  const NOBODY_LOG: NotificationLog = {
    id: 9,
    channel_id: null,
    order_id: 1,
    event: 'order.created',
    status: 'failed',
    error: '沒有任何啟用中的通知管道，所以這則提醒沒有送到任何地方。',
    created_at: '2026-08-20T01:00:00Z',
    delivered_at: null,
    // Nowhere to send it and nothing scheduled: this is 「stopped」, and
    // 「still retrying」 would be a lie in the worst possible direction.
    delivery_state: 'given_up',
    attempts: 0,
    max_attempts: 5,
    next_retry_at: null,
  }

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(platform.currentPushAvailability).mockReturnValue({ kind: 'ready' })
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/notifications/channels') return [] as never
      if (path === '/api/notifications/logs') return [NOBODY_LOG] as never
      return [] as never
    })
  })

  it('管道欄位要講清楚是「沒有送到任何管道」，不能是空白或 #null', async () => {
    renderPage()

    expect(await screen.findByText('沒有送到任何管道')).toBeInTheDocument()
  })

  it('那一列要看得出來不對勁', async () => {
    renderPage()

    const cell = await screen.findByText('沒有送到任何管道')
    expect(cell.className).toContain('red')
  })

  it('要把原因原樣顯示出來，那是使用者唯一的下一步', async () => {
    renderPage()

    expect(await screen.findByText(/沒有任何啟用中的通知管道/)).toBeInTheDocument()
  })
})

// --- 「失敗」 was four different situations wearing one word -------------------
//
// A failed row can be waiting out quiet hours, between attempts, out of
// attempts, or attached to a channel that no longer exists. The page printed
// 失敗 for all four and the raw provider error underneath. The owner of an
// alerting app has exactly one question about a failed alert -- 「所以它還會來
// 嗎？」 -- and that was the one thing the row did not say.

describe('這則通知到底還會不會來', () => {
  const base: NotificationLog = {
    id: 9,
    channel_id: 1,
    order_id: null,
    event: 'order.created',
    status: 'failed',
    error: 'Telegram timed out',
    created_at: '2026-08-16T00:00:00Z',
    delivered_at: null,
    delivery_state: 'retrying',
    attempts: 3,
    max_attempts: 5,
    next_retry_at: '2026-08-16T00:08:00Z',
  }

  function showLog(log: NotificationLog) {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/notifications/channels') return [CHANNEL] as never
      if (path === '/api/notifications/logs') return [log] as never
      return [] as never
    })
    renderPage()
  }

  it('還在重試的說得出第幾次、還有幾次', async () => {
    showLog(base)

    const cell = await screen.findByText(/重試中/)
    expect(cell).toHaveTextContent('3')
    expect(cell).toHaveTextContent('5')
  })

  it('還在重試的說得出下次什麼時候 —— 使用者正在決定要不要現在去開券商 App', async () => {
    showLog(base)

    const cell = await screen.findByText(/重試中/)
    // Rendered in the reader's own timezone, so pin the hour the browser
    // would show rather than the UTC string.
    const expected = new Date(base.next_retry_at!).toLocaleTimeString()
    expect(cell.textContent).toContain(expected)
  })

  it('已經放棄的要說清楚不會再送了，不能跟還在重試的長一樣', async () => {
    showLog({ ...base, delivery_state: 'given_up', attempts: 5, next_retry_at: null })

    expect(await screen.findByText(/不會再/)).toBeInTheDocument()
    expect(screen.queryByText(/重試中/)).not.toBeInTheDocument()
  })

  it('靜音時段是在等，不是失敗 —— 標成失敗會讓人以為靜音在吃掉提醒', async () => {
    showLog({
      ...base,
      delivery_state: 'deferred',
      attempts: 0,
      error: '靜音時段，將在 07:00 UTC 之後送出',
      next_retry_at: '2026-08-16T07:00:00Z',
    })

    expect(await screen.findByText(/等待靜音時段/)).toBeInTheDocument()
    expect(screen.queryByText(/重試中/)).not.toBeInTheDocument()
  })

  it('靜音的那一列不要在錯誤欄再用紅字說一次同樣的事', async () => {
    // The status cell already says it is waiting. Repeating it in the error
    // column, in red, is precisely what made a deliberate hold read as a
    // failure.
    showLog({
      ...base,
      delivery_state: 'deferred',
      attempts: 0,
      error: '靜音時段，將在 07:00 UTC 之後送出',
      next_retry_at: '2026-08-16T07:00:00Z',
    })

    await screen.findByText(/等待靜音時段/)
    expect(screen.queryByText(/將在 07:00 UTC 之後送出/)).not.toBeInTheDocument()
  })

  it('送出去的還是送出去', async () => {
    showLog({ ...base, status: 'sent', delivery_state: 'sent', error: null, next_retry_at: null })

    expect(await screen.findByText('已送出')).toBeInTheDocument()
  })
})
