import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkerHealthBanner, type Health } from './WorkerHealthBanner'
import { api } from '../lib/api'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn() },
}))

function renderBanner() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkerHealthBanner />
    </QueryClientProvider>,
  )
}

const HEALTHY: Health = {
  status: 'ok',
  checks: {
    database: { status: 'ok' },
    worker: { status: 'ok', last_loop_age_sec: 2.1 },
    market_data: { status: 'ok', last_poll_age_sec: 4.7 },
  },
}

describe('WorkerHealthBanner', () => {
  beforeEach(() => vi.clearAllMocks())

  it('says nothing while everything is running', async () => {
    vi.mocked(api.get).mockResolvedValue(HEALTHY as never)
    const { container } = renderBanner()
    await vi.waitFor(() => expect(api.get).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('says the engine has stopped, and what that costs', async () => {
    vi.mocked(api.get).mockResolvedValue({
      ...HEALTHY,
      status: 'fail',
      checks: { ...HEALTHY.checks, worker: { status: 'fail', last_loop_age_sec: 900 } },
    } as never)
    renderBanner()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('背景引擎已停止')
    expect(alert).toHaveTextContent('15 分鐘')
    // The consequence, not just the state -- "worker down" means nothing to
    // someone who did not build this.
    expect(alert).toHaveTextContent('提醒不會送出')
  })

  it('distinguishes a dead price feed from a dead engine', async () => {
    vi.mocked(api.get).mockResolvedValue({
      ...HEALTHY,
      status: 'fail',
      checks: { ...HEALTHY.checks, market_data: { status: 'fail', last_poll_age_sec: 600 } },
    } as never)
    renderBanner()

    expect(await screen.findByRole('alert')).toHaveTextContent('行情已經抓不到')
  })

  it('treats a backend that will not answer as the outage it is', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Failed to fetch'))
    renderBanner()

    expect(await screen.findByRole('alert')).toHaveTextContent('連不上後端服務')
  })

  it('says warming up rather than broken during startup', async () => {
    vi.mocked(api.get).mockResolvedValue({
      ...HEALTHY,
      checks: { ...HEALTHY.checks, worker: { status: 'starting' } },
    } as never)
    renderBanner()

    expect(await screen.findByRole('alert')).toHaveTextContent('正在暖機')
  })
})
