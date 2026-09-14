import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TradingViewSetupPanel } from './TradingViewSetupPanel'
import { api } from '../lib/api'
import { maskPersonalUrl } from '../lib/tradingviewUrl'
import type { WebhookSetup } from '../lib/types'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn(), post: vi.fn() },
}))

const MAC = 'Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MGFiY2RlZmdoaWpr'
const SETUP: WebhookSetup = {
  url: `https://example.onrender.com/api/webhooks/tradingview/1.0.${MAC}`,
  example_message: '{\n  "symbol": "{{ticker}}",\n  "action": "buy",\n  "id": "{{timenow}}"\n}',
  notes: ['這條網址本身就是密碼。'],
}

function renderPanel(allowRegenerate = false) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <TradingViewSetupPanel setup={SETUP} allowRegenerate={allowRegenerate} />
    </QueryClientProvider>,
  )
}

/**
 * 貼進 TradingView 的那一條網址，本身就是密碼（#115）。
 *
 * 規格（ONBOARDING.md 方案 4）要的是「都可以直接複製，不用去別的地方拿」。而一個
 * 「直接複製就能用」的憑證，同時也是一個「截圖就被帶走」的憑證——所以畫面上遮住、
 * 複製的是完整的，兩件事要一起成立。
 */
describe('TradingView 的網址就是密碼', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('畫面上先遮住憑證那一段——截圖和螢幕分享都會帶走它', () => {
    renderPanel()

    expect(screen.getByText(maskPersonalUrl(SETUP.url))).toBeInTheDocument()
    expect(screen.queryByText(new RegExp(MAC))).toBeNull()
  })

  it('但「複製網址」複製的是完整的那一條——他要貼得過去', async () => {
    const user = userEvent.setup()
    renderPanel()

    await user.click(screen.getByRole('button', { name: '複製網址' }))

    expect(await navigator.clipboard.readText()).toBe(SETUP.url)
  })

  it('要看整條也可以，按一下就好', async () => {
    const user = userEvent.setup()
    renderPanel()

    await user.click(screen.getByRole('button', { name: '顯示完整網址' }))

    expect(screen.getByText(SETUP.url)).toBeInTheDocument()
  })

  it('訊息範本裡沒有要他去別的地方拿的值', () => {
    renderPanel()

    const message = screen.getByText(/"id": "\{\{timenow\}\}"/)
    expect(message.textContent).not.toMatch(/secret|TV_WEBHOOK_SECRET/)
  })

  it('引導裡不放「重新產生」——第一次設定的人不需要一顆會讓東西失效的按鈕', () => {
    renderPanel(false)

    expect(screen.queryByRole('button', { name: '重新產生網址' })).toBeNull()
  })

  it('重新產生要先確認，而且要說清楚舊的網址會立刻失效', async () => {
    vi.mocked(api.post).mockResolvedValue({ ...SETUP, url: SETUP.url.replace('1.0.', '1.1.') })
    const user = userEvent.setup()
    renderPanel(true)

    await user.click(screen.getByRole('button', { name: '重新產生網址' }))

    // 按一下就生效的話，TradingView 裡每一則警報會在他不知道的情況下一起不響。
    expect(api.post).not.toHaveBeenCalled()
    expect(screen.getByText(/舊網址會立刻失效/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '確定重新產生' }))

    expect(api.post).toHaveBeenCalledWith('/api/webhooks/tradingview/setup/rotate')
  })

  // 放最後：它換掉了 navigator.clipboard，而 userEvent.setup() 會在前面幾條裝回它自己的。
  it('複製不了的時候（不是 https、或瀏覽器擋掉）說得出另一條路', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
      configurable: true,
    })
    renderPanel()

    fireEvent.click(screen.getByRole('button', { name: '複製網址' }))

    expect(await screen.findByText(/複製不了/)).toBeInTheDocument()
  })
})

describe('maskPersonalUrl', () => {
  it('只遮 MAC，帳號和版本留著——log 和畫面上都還看得出是哪一條', () => {
    expect(maskPersonalUrl(SETUP.url)).toBe(
      'https://example.onrender.com/api/webhooks/tradingview/1.0.••••••••',
    )
  })

  it('不是專屬網址的就原樣留著', () => {
    const shared = 'https://example.onrender.com/api/webhooks/tradingview'
    expect(maskPersonalUrl(shared)).toBe(shared)
  })
})
