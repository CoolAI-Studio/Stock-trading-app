import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NotificationsPage } from './NotificationsPage'
import { api } from '../lib/api'
import type { NotificationChannel } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
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
    vi.mocked(api.get).mockResolvedValue([CHANNEL] as never)
  })

  it('lists channels with their masked preview, never a raw secret', async () => {
    renderPage()
    expect(await screen.findByText('phone')).toBeInTheDocument()
    expect(screen.getByText(/bot_token=\*+abcd/)).toBeInTheDocument()
  })

  it('sends a test notification', async () => {
    vi.mocked(api.post).mockResolvedValue({ ok: true, error: null } as never)
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('phone')
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
      }),
    )
  })
})
