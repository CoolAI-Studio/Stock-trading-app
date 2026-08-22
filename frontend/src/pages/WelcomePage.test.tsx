import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WelcomePage } from './WelcomePage'
import { api } from '../lib/api'
import type { StrategyTemplate } from '../lib/types'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn(), post: vi.fn() },
}))

const TEMPLATE: StrategyTemplate = {
  key: 'price_alert',
  title: '到價提醒',
  summary: '跌破或漲過你設的價位，就通知你。',
  good_for: '只想要「跌到我想買的價位叫我」的時候。',
  fields: [
    {
      key: 'buy_below',
      label: '跌破多少通知我',
      help: '例如 900。留 0 表示這一邊不用管。',
      kind: 'number',
      default: 0,
      minimum: 0,
    },
  ],
}

function serve({ channels = [], strategies = [] }: { channels?: unknown[]; strategies?: unknown[] }) {
  vi.mocked(api.get).mockImplementation((path: string) => {
    if (path.includes('templates')) return Promise.resolve([TEMPLATE]) as never
    if (path.includes('channels')) return Promise.resolve(channels) as never
    if (path.includes('strategies')) return Promise.resolve(strategies) as never
    return Promise.resolve([]) as never
  })
}

function renderWizard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/welcome']}>
        <Routes>
          <Route path="/welcome" element={<WelcomePage />} />
          <Route path="/" element={<div>儀表板</div>} />
          <Route path="/notifications" element={<div>通知設定頁</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/**
 * 引導流程，照 ONBOARDING.md。
 *
 * 這份規格的判準只有一條：他建完帳號之後，多久手機會響第一次，中間有沒有任何
 * 一步需要他寫程式、開終端機、或去別的地方查一個值。
 */
describe('引導流程', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.post).mockResolvedValue({ id: 1, name: '到價提醒 2330.TW', is_active: true })
  })

  it('第一個畫面只問一句話，而且不需要金鑰的那條路排在最上面', async () => {
    // 規格的一部分，不是排版偏好：引導的預設路徑不可以是需要 AI 金鑰的那一條。
    serve({})
    renderWizard()

    const own = await screen.findByRole('button', { name: /我自己選/ })
    const ai = screen.getByRole('button', { name: /讓 AI 幫我/ })

    expect(own.compareDocumentPosition(ai)).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
  })

  it('選「我自己選」就看到現成的範本，全程沒有 AI 也沒有金鑰', async () => {
    serve({})
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole('button', { name: /我自己選/ }))

    expect(await screen.findByText('到價提醒')).toBeInTheDocument()
    // 沒有任何一個請求打到 AI 那邊。
    const asked = vi.mocked(api.get).mock.calls.map(([path]) => String(path))
    expect(asked.some((path) => path.includes('ai'))).toBe(false)
  })

  it('建立完提醒之後，下一步問的是「這些提醒要送到哪裡」', async () => {
    serve({})
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole('button', { name: /我自己選/ }))
    await user.click(await screen.findByRole('button', { name: /到價提醒/ }))
    await user.type(screen.getByLabelText('股票代號'), '2330.TW')
    await user.click(screen.getByRole('button', { name: '建立提醒' }))

    expect(await screen.findByText(/送到哪裡/)).toBeInTheDocument()
  })

  it('已經有啟用中的管道時，這一步直接說它會送到哪裡', async () => {
    serve({ channels: [{ id: 1, channel_type: 'telegram', label: '我的 Telegram', is_enabled: true }] })
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole('button', { name: /先跳過/ }))

    expect(await screen.findByText(/我的 Telegram/)).toBeInTheDocument()
  })

  it('沒有管道時，第一個給的是不用去別的地方拿值的那一種', async () => {
    // Telegram 要去 BotFather 拿 token，Email 要一組 SMTP——兩個都是「去別的地方
    // 拿一個值」。瀏覽器推播是這個 app 自己就生得出來的那一個。
    serve({})
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole('button', { name: /先跳過/ }))

    expect(await screen.findByRole('button', { name: /開啟這台裝置的推播/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Telegram/ })).toHaveAttribute(
      'href',
      '/notifications',
    )
  })

  it('要跳過通知管道可以，但要明說現在不會有任何提醒送出', async () => {
    serve({})
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole('button', { name: /先跳過/ }))
    await user.click(await screen.findByRole('button', { name: /這一步先跳過/ }))

    expect(await screen.findByText(/現在不會有任何提醒送出/)).toBeInTheDocument()
  })

  it('完成畫面說得出他現在有幾則提醒、會送到哪裡', async () => {
    serve({
      channels: [{ id: 1, channel_type: 'telegram', label: '我的 Telegram', is_enabled: true }],
      strategies: [{ id: 1, name: '到價提醒 2330.TW', is_active: true }],
    })
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole('button', { name: /先跳過/ }))
    await user.click(await screen.findByRole('button', { name: /完成/ }))

    // 數字被 <strong> 包起來，所以要看整段的文字內容，不能用預設的文字比對
    // ——那個比對不跨越元素邊界。
    expect(await screen.findByText(/你現在有/)).toHaveTextContent('1 則提醒')
  })

  it('整個引導可以直接離開，不會把人關在裡面', async () => {
    serve({})
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole('button', { name: /我知道自己在做什麼/ }))

    await waitFor(() => expect(screen.getByText('儀表板')).toBeInTheDocument())
  })
})
