import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WebhooksPage } from './WebhooksPage'
import { api } from '../lib/api'
import type { WebhookLog, WebhookSetup } from '../lib/types'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn(), post: vi.fn() },
}))

const MAC = 'Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MGFiY2RlZmdoaWpr'

const SETUP: WebhookSetup = {
  url: `https://example.onrender.com/api/webhooks/tradingview/1.0.${MAC}`,
  example_message: '{"symbol": "{{ticker}}", "id": "{{timenow}}"}',
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
  missing_id: false,
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

  it('shows the URL to paste into TradingView, with its credential masked', async () => {
    // Nothing told the owner this; it had to be worked out from the source. The
    // URL is the account's own now and IS the password (#115), so the part that
    // grants access stays off the screen -- the copy button carries the whole thing.
    serve([])
    renderPage()
    expect(await screen.findByText(/\/api\/webhooks\/tradingview\/1\.0\./)).toBeInTheDocument()
    expect(screen.queryByText(new RegExp(MAC))).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '複製網址' })).toBeInTheDocument()
  })

  it('shows an example message that needs no password from anywhere else', async () => {
    // It used to print <你的 TV_WEBHOOK_SECRET>, which sent the owner digging
    // through the hosting platform's settings for a value (#115).
    serve([])
    renderPage()
    expect(await screen.findByText(/"id": "\{\{timenow\}\}"/)).toBeInTheDocument()
    expect(screen.queryByText(/TV_WEBHOOK_SECRET/)).not.toBeInTheDocument()
  })

  it('offers to regenerate the URL here, where a leak would be dealt with', async () => {
    serve([])
    renderPage()
    expect(await screen.findByRole('button', { name: '重新產生網址' })).toBeInTheDocument()
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

describe('alerts that cannot be fully protected', () => {
  it('flags an alert that arrived without an id, even though it worked', async () => {
    // Better learnt here than by being replayed: anyone who got hold of the
    // body can post it again, and only a short identical-body window stops
    // them.
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('/setup')) return SETUP as never
      return [{ ...LOG, missing_id: true }] as never
    })
    renderPage()

    expect(await screen.findByText(/無法完全防止重放/)).toBeInTheDocument()
  })

  it('says nothing extra when the alert carried an id', async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.includes('/setup')) return SETUP as never
      return [LOG] as never
    })
    renderPage()

    await screen.findByText(/已建立訂單/)
    expect(screen.queryByText(/無法完全防止重放/)).not.toBeInTheDocument()
  })
})
