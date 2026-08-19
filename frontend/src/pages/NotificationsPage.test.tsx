import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NotificationsPage } from './NotificationsPage'
import { api } from '../lib/api'
import * as push from '../lib/push'
import type { NotificationChannel, NotificationLog } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

vi.mock('../lib/push', () => ({
  isPushSupported: vi.fn(() => true),
  subscribeToPush: vi.fn(),
  unsubscribeFromPush: vi.fn(),
}))

const CHANNEL: NotificationChannel = {
  id: 1,
  channel_type: 'telegram',
  label: 'phone',
  is_enabled: true,
  subscribed_events: null,
  last_sent_at: null,
  last_error: null,
  config_preview: 'telegram: bot_token=****abcd, chat_id=999',
}

const WEB_PUSH_CHANNEL: NotificationChannel = {
  id: 2,
  channel_type: 'web_push',
  label: 'my-laptop',
  is_enabled: true,
  subscribed_events: null,
  last_sent_at: null,
  last_error: null,
  config_preview: 'web_push: endpoint=https://push.example.com/x',
}

const LOG: NotificationLog = {
  id: 1,
  channel_id: 1,
  order_id: null,
  event: 'test',
  status: 'sent',
  error: null,
  created_at: '2026-08-16T00:00:00Z',
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
      }),
    )
  })

  it('unsubscribes the browser push subscription when deleting a web push channel', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/notifications/channels') return [WEB_PUSH_CHANNEL] as never
      if (path === '/api/notifications/logs') return [] as never
      return [] as never
    })
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
