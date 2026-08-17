import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BrokerSettingsPage } from './BrokerSettingsPage'
import { api } from '../lib/api'
import type { BrokerCredential } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

const CREDENTIAL: BrokerCredential = {
  id: 1,
  label: 'my-yuanta',
  broker_name: 'Yuanta SPARK API',
  created_at: '2026-08-17T00:00:00Z',
  config_preview: 'api_key=****cdef, account_id=12345',
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <BrokerSettingsPage />
    </QueryClientProvider>,
  )
}

describe('BrokerSettingsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.get).mockResolvedValue([CREDENTIAL] as never)
  })

  it('lists credentials with their masked preview, never a raw secret', async () => {
    renderPage()
    expect(await screen.findByText('my-yuanta')).toBeInTheDocument()
    expect(screen.getByText('Yuanta SPARK API')).toBeInTheDocument()
    expect(screen.getByText(/api_key=\*+cdef/)).toBeInTheDocument()
  })

  it('creates a credential from dynamic key/value fields', async () => {
    vi.mocked(api.post).mockResolvedValue(CREDENTIAL as never)
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('my-yuanta')
    await user.click(screen.getByRole('button', { name: '新增券商憑證' }))
    await user.type(screen.getByLabelText('名稱'), 'my-broker')
    await user.type(screen.getByLabelText('券商 / 交易所名稱'), 'Some Broker')
    await user.type(screen.getByLabelText('欄位名稱 1'), 'api_key')
    await user.type(screen.getByLabelText('欄位值 1'), 'abc123')
    await user.click(screen.getByRole('button', { name: '+ 新增欄位' }))
    await user.type(screen.getByLabelText('欄位名稱 2'), 'api_secret')
    await user.type(screen.getByLabelText('欄位值 2'), 'xyz789')
    await user.click(screen.getByRole('button', { name: '建立' }))

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/broker-credentials', {
        label: 'my-broker',
        broker_name: 'Some Broker',
        config: { api_key: 'abc123', api_secret: 'xyz789' },
      }),
    )
  })

  it('deletes a credential after confirming', async () => {
    vi.mocked(api.delete).mockResolvedValue(undefined as never)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('my-yuanta')
    await user.click(screen.getByRole('button', { name: '刪除' }))

    await waitFor(() => expect(api.delete).toHaveBeenCalledWith('/api/broker-credentials/1'))
  })

  it('sends a message to the AI setup assistant and shows the reply', async () => {
    vi.mocked(api.post).mockResolvedValue({
      ok: true,
      reply: 'Go to your broker dashboard...',
      error: null,
    } as never)
    const user = userEvent.setup()
    renderPage()

    await user.type(screen.getByLabelText('問題'), 'How do I get an API key?')
    await user.click(screen.getByRole('button', { name: '送出' }))

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/broker-credentials/ai-assist', {
        message: 'How do I get an API key?',
      }),
    )
    expect(await screen.findByText(/Go to your broker dashboard/)).toBeInTheDocument()
  })

  it('shows an error from the AI assistant without crashing', async () => {
    vi.mocked(api.post).mockResolvedValue({
      ok: false,
      reply: null,
      error: 'AI_API_KEY is not configured',
    } as never)
    const user = userEvent.setup()
    renderPage()

    await user.type(screen.getByLabelText('問題'), 'help')
    await user.click(screen.getByRole('button', { name: '送出' }))

    expect(await screen.findByText(/AI_API_KEY is not configured/)).toBeInTheDocument()
  })
})
