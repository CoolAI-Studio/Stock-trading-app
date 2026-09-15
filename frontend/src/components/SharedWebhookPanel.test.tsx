import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SharedWebhookPanel } from './SharedWebhookPanel'
import { api } from '../lib/api'
import type { SharedWebhookState } from '../lib/types'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn(), put: vi.fn() },
}))

const DAY = 24 * 60 * 60 * 1000

function renderPanel(state: SharedWebhookState | Error) {
  if (state instanceof Error) {
    vi.mocked(api.get).mockRejectedValue(state)
  } else {
    vi.mocked(api.get).mockResolvedValue(state as never)
  }
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <SharedWebhookPanel />
    </QueryClientProvider>,
  )
}

/**
 * 舊的共用網址（訊息裡帶 secret 的那一條）的開關。
 *
 * 他要看得出兩件事才決定得了：它現在開不開、還有沒有警報在用它。關掉之後外洩的舊密碼就
 * 沒用了——但還打在它上面的警報也會從此不響，所以關之前要說清楚，而最近還收過訊號的時候
 * 要說得更清楚。
 */
describe('舊的共用網址', () => {
  beforeEach(() => vi.clearAllMocks())

  it('開著、還沒收過訊號：說清楚，並給一顆關掉的按鈕', async () => {
    renderPanel({ enabled: true, last_used_at: null })

    expect(await screen.findByText(/開著/)).toBeInTheDocument()
    expect(screen.getByText(/還沒有收到過/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '關掉舊的共用網址' })).toBeInTheDocument()
  })

  it('收過訊號就說最後一次是什麼時候', async () => {
    renderPanel({ enabled: true, last_used_at: new Date(Date.now() - 40 * DAY).toISOString() })

    expect(await screen.findByText(/最後一次收到/)).toBeInTheDocument()
  })

  it('關掉之前先確認，確定了才送出去', async () => {
    vi.mocked(api.put).mockResolvedValue({ enabled: false, last_used_at: null } as never)
    const user = userEvent.setup()
    renderPanel({ enabled: true, last_used_at: null })

    await user.click(await screen.findByRole('button', { name: '關掉舊的共用網址' }))
    expect(api.put).not.toHaveBeenCalled()
    expect(screen.getByText(/訊息裡帶 secret 的警報不會再進來/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '確定關掉' }))

    expect(api.put).toHaveBeenCalledWith('/api/webhooks/tradingview/shared', { enabled: false })
  })

  it('最近幾天還收過訊號：關之前明說有警報還在用它', async () => {
    const user = userEvent.setup()
    renderPanel({ enabled: true, last_used_at: new Date(Date.now() - 2 * DAY).toISOString() })

    await user.click(await screen.findByRole('button', { name: '關掉舊的共用網址' }))

    expect(screen.getByText(/2 天前還收到過訊號/)).toBeInTheDocument()
  })

  it('已經關掉：說清楚，重新打開不用再確認', async () => {
    vi.mocked(api.put).mockResolvedValue({ enabled: true, last_used_at: null } as never)
    const user = userEvent.setup()
    renderPanel({ enabled: false, last_used_at: null })

    expect(await screen.findByText(/已經關掉/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '重新打開' }))

    expect(api.put).toHaveBeenCalledWith('/api/webhooks/tradingview/shared', { enabled: true })
  })

  it('後端比畫面舊（還沒有這支端點）：這一格不出現，而不是弄壞整頁', async () => {
    const { container } = renderPanel(new Error('Not Found'))

    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(container).toBeEmptyDOMElement()
  })
})
