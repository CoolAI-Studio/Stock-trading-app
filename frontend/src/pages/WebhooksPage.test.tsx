import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WebhooksPage } from './WebhooksPage'
import { api } from '../lib/api'
import type { WebhookLog, WebhookSetup } from '../lib/types'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn() },
}))

const SETUP: WebhookSetup = {
  url: 'https://example.onrender.com/api/webhooks/tradingview',
  example_message: '{"secret": "<你的 TV_WEBHOOK_SECRET>", "id": "{{timenow}}"}',
  notes: ['id 一定要填。'],
}

const LOG: WebhookLog = {
  id: 1,
  received_at: '2026-08-19T01:30:00Z',
  remote_ip: '52.89.214.238',
  signature_valid: true,
  parsed_ok: true,
  raw_body: '{"symbol": "2330.TW", "action": "buy"}',
  order_id: 42,
  error: null,
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <WebhooksPage />
    </QueryClientProvider>,
  )
}

function serve(logs: WebhookLog[]) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path.includes('/setup')) return SETUP as never
    return logs as never
  })
}

describe('WebhooksPage', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows the URL to paste into TradingView', async () => {
    // Nothing told the owner this; it had to be worked out from the source.
    serve([])
    renderPage()
    expect(await screen.findByText(SETUP.url)).toBeInTheDocument()
  })

  it('shows the example message, with the secret as a placeholder', async () => {
    // Printing the real shared secret would put it in every screenshot.
    serve([])
    renderPage()
    expect(await screen.findByText(/TV_WEBHOOK_SECRET/)).toBeInTheDocument()
  })

  it('says an alert became an order', async () => {
    serve([LOG])
    renderPage()
    const row = (await screen.findByText(/2330.TW/)).closest('tr') as HTMLElement
    expect(within(row).getByText(/已建立訂單/)).toBeInTheDocument()
  })

  it('distinguishes a wrong secret from bad JSON', async () => {
    // Different problems with different fixes; both used to be invisible.
    serve([
      { ...LOG, id: 2, signature_valid: false, parsed_ok: false, error: 'secret mismatch' },
    ])
    renderPage()
    expect(await screen.findByText('密鑰不符')).toBeInTheDocument()

    serve([{ ...LOG, id: 3, signature_valid: true, parsed_ok: false, error: 'not JSON' }])
    renderPage()
    expect(await screen.findAllByText('格式看不懂')).not.toHaveLength(0)
  })

  it('flags an alert that arrived fine but was refused downstream', async () => {
    // The subtle one: nothing wrong with TradingView, a risk gate said no.
    serve([{ ...LOG, order_id: null, error: '買進後會超過本金上限' }])
    renderPage()

    expect(await screen.findByText('沒有變成訂單')).toBeInTheDocument()
    expect(screen.getByText(/本金上限/)).toBeInTheDocument()
  })

  it('says plainly when nothing has arrived yet', async () => {
    serve([])
    renderPage()
    expect(await screen.findByText(/還沒收到任何 TradingView 訊號/)).toBeInTheDocument()
  })
})
