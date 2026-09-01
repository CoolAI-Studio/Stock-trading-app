import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WelcomePage } from './WelcomePage'
import { api } from '../lib/api'
import { requestPushPermission, subscribeToPush } from '../lib/push'
import type { StrategyTemplate } from '../lib/types'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn(), post: vi.fn() },
}))

vi.mock('../lib/push', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/push')>()),
  isPushSupported: vi.fn(() => true),
  requestPushPermission: vi.fn(),
  subscribeToPush: vi.fn(),
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
    if (path.includes('ai-settings')) return Promise.resolve({ configured: true }) as never
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
    // AI 那一顆要等「AI 有沒有設定好」的答案回來才會出現（沒設定就不出現），
    // 所以這裡不能用 getByRole。
    const ai = await screen.findByRole('button', { name: /讓 AI 幫我/ })

    expect(own.compareDocumentPosition(ai)).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
  })

  it('第一個問題就是「要不要先把設定弄完」，那才是新使用者真正卡住的地方', async () => {
    // 使用者的話：「引導只是引導如何讓 AI 的 API 上線，還有幾個建議資料庫如何讓
    // 它連上線或是引導用本機的設定。」設一則提醒是之後的事。
    serve({})
    renderWizard()

    expect(await screen.findByRole('link', { name: /把設定弄完/ })).toHaveAttribute(
      'href',
      '/guide',
    )
  })

  it('AI 還沒接上線的時候，就不要給那個選項', async () => {
    // 給一個按了會走進死路的選項，比不給還糟。
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path.includes('ai-settings')) return Promise.resolve({ configured: false }) as never
      if (path.includes('templates')) return Promise.resolve([TEMPLATE]) as never
      return Promise.resolve([]) as never
    })
    renderWizard()

    expect(await screen.findByRole('button', { name: /我自己選/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /讓 AI 幫我/ })).not.toBeInTheDocument()
  })

  it('選「我自己選」就看到現成的範本，全程沒有 AI 也沒有金鑰', async () => {
    serve({})
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole('button', { name: /我自己選/ }))

    expect(await screen.findByText('到價提醒')).toBeInTheDocument()
    // 沒有任何一次真的去問 AI。（「AI 有沒有設定好」還是要問，因為那決定要不要
    // 給那個選項——但那是問這個部署的狀態，不是花他的錢去問模型。）
    const posted = vi.mocked(api.post).mock.calls.map(([path]) => String(path))
    expect(posted.some((path) => path.includes('generate'))).toBe(false)
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

/**
 * 階段 2B：讓 AI 幫忙。
 *
 * 這一段的規格有一條是硬的：**AI 產生的東西一定要他按下確認才會寫進去**。
 * 他讀不懂那段程式碼，而「看起來完成、實際上做別的事」正是他抓不到的那一種錯。
 *
 * 另一條是：任何一步失敗都要退回「我自己選」，不是停在那裡。引導不能因為一個
 * 選填功能而卡死——那會讓 AI 事實上變成必需品。
 */
describe('引導流程：讓 AI 幫我', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  function serveAi({
    configured = true,
    generate,
  }: {
    configured?: boolean
    generate?: unknown
  } = {}) {
    vi.mocked(api.get).mockImplementation((path: string) => {
      if (path.includes('ai-settings')) return Promise.resolve({ configured }) as never
      if (path.includes('templates')) return Promise.resolve([TEMPLATE]) as never
      return Promise.resolve([]) as never
    })
    vi.mocked(api.post).mockImplementation((path: string) => {
      if (path.includes('generate')) return Promise.resolve(generate) as never
      return Promise.resolve({ id: 9, name: 'AI 的提醒', is_active: true }) as never
    })
  }

  it('設定好了就直接在這裡問他要什麼', async () => {
    serveAi({ generate: { ok: true, source_code: 'class Strategy: pass', detected_name: '台積電到價', detected_symbol: '2330.TW' } })
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole('button', { name: /讓 AI 幫我/ }))
    await user.type(await screen.findByLabelText(/用一句話說/), '台積電跌到 900 提醒我')
    await user.click(screen.getByRole('button', { name: '讓 AI 想一個' }))

    await waitFor(() =>
      expect(vi.mocked(api.post).mock.calls.some(([p]) => String(p).includes('generate'))).toBe(
        true,
      ),
    )
  })

  it('AI 想好了也不會直接寫進去 —— 要他按確認', async () => {
    serveAi({
      generate: {
        ok: true,
        source_code: 'class Strategy: pass',
        detected_name: '台積電到價',
        detected_symbol: '2330.TW',
      },
    })
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole('button', { name: /讓 AI 幫我/ }))
    await user.type(await screen.findByLabelText(/用一句話說/), '台積電跌到 900 提醒我')
    await user.click(screen.getByRole('button', { name: '讓 AI 想一個' }))

    expect(await screen.findByText(/台積電到價/)).toBeInTheDocument()
    // 還沒建立任何東西：只有 generate 被呼叫過。
    const created = vi
      .mocked(api.post)
      .mock.calls.filter(([p]) => String(p) === '/api/strategies')
    expect(created).toHaveLength(0)
  })

  it('按下確認之後才建立，而且只會通知、不會下單', async () => {
    serveAi({
      generate: {
        ok: true,
        source_code: 'class Strategy: pass',
        detected_name: '台積電到價',
        detected_symbol: '2330.TW',
      },
    })
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole('button', { name: /讓 AI 幫我/ }))
    await user.type(await screen.findByLabelText(/用一句話說/), '台積電跌到 900 提醒我')
    await user.click(screen.getByRole('button', { name: '讓 AI 想一個' }))
    await user.click(await screen.findByRole('button', { name: /建立這則提醒/ }))

    await waitFor(() => {
      const call = vi.mocked(api.post).mock.calls.find(([p]) => String(p) === '/api/strategies')
      expect(call).toBeTruthy()
      expect(call![1]).toMatchObject({ alert_only: true, symbol: '2330.TW' })
    })
  })

  it('AI 反過來問問題的時候，讓他回答，而不是當成失敗', async () => {
    serveAi({
      generate: { ok: false, question: '你要看的是收盤價還是盤中價？' },
    })
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole('button', { name: /讓 AI 幫我/ }))
    await user.type(await screen.findByLabelText(/用一句話說/), '台積電跌就提醒我')
    await user.click(screen.getByRole('button', { name: '讓 AI 想一個' }))

    expect(await screen.findByText(/收盤價還是盤中價/)).toBeInTheDocument()
    expect(await screen.findByLabelText(/你的回答/)).toBeInTheDocument()
  })

  it('AI 失敗的時候給得出一條走得完的路', async () => {
    // 規格：任何一步失敗一律退回「我自己選」，不是停在那裡——否則一個選填的
    // 功能就變成了必需品。
    serveAi({ generate: { ok: false, error: '這個模型現在沒有回應。' } })
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole('button', { name: /讓 AI 幫我/ }))
    await user.type(await screen.findByLabelText(/用一句話說/), '隨便')
    await user.click(screen.getByRole('button', { name: '讓 AI 想一個' }))

    expect(await screen.findByText(/這個模型現在沒有回應/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /改用現成的範本/ }))
    expect(await screen.findByText('到價提醒')).toBeInTheDocument()
  })

  it('程式碼沒問題但代號永遠不會有報價的時候，要說出來', async () => {
    // 這個陷阱後端的 schema 自己寫著：編輯器印「偵測到：均線（2330）」是綠的，
    // 而拒絕在存檔時從另一個欄位才出現，中間沒有任何東西把兩件事連起來。
    serveAi({
      generate: {
        ok: true,
        source_code: 'class Strategy: pass',
        detected_name: '均線',
        detected_symbol: '2330',
        symbol_problem: '2330 少了 .TW，這樣抓不到報價。',
      },
    })
    const user = userEvent.setup()
    renderWizard()

    await user.click(await screen.findByRole('button', { name: /讓 AI 幫我/ }))
    await user.type(await screen.findByLabelText(/用一句話說/), '均線')
    await user.click(screen.getByRole('button', { name: '讓 AI 想一個' }))

    expect(await screen.findByText(/少了 .TW/)).toBeInTheDocument()
  })
})

describe('引導那顆推播按鈕（全新裝置）', () => {
  /**
   * **這一條是實地找出來的，而它讓引導唯一「不用去別的地方拿值」的通知管道必定失敗。**
   *
   * subscribeToPush() 在 lib/push.ts:78 開頭就寫著
   *
   *     if (Notification.permission !== 'granted') throw new Error('未取得通知權限')
   *
   * ——它**不會**去要權限，那是 requestPushPermission() 的事，而註解裡明說了原因
   * （要權限必須由使用者手勢直接觸發）。NotificationsPage 有照做（:746 先要再訂），
   * 兩個引導頁沒有。
   *
   * 全新裝置上 Notification.permission 是 'default'，所以引導那顆
   * 「開啟這台裝置的推播（最快，按一下就好）」在**每一台第一次打開的手機上都必定丟錯**
   * ——而它正是引導裡唯一不需要他去別的服務註冊的那條路。
   */
  beforeEach(() => {
    vi.mocked(requestPushPermission).mockReset()
    vi.mocked(subscribeToPush).mockReset()
  })

  it('先向瀏覽器要權限，再訂閱', async () => {
    serve({})
    vi.mocked(requestPushPermission).mockResolvedValue('granted')
    vi.mocked(subscribeToPush).mockResolvedValue(undefined as never)
    const user = userEvent.setup()
    renderWizard()
    // 推播那一步在精靈的第二頁，先跳過建立提醒。
    await user.click(await screen.findByRole('button', { name: /先跳過/ }))

    await user.click(await screen.findByRole('button', { name: /開啟這台裝置的推播/ }))

    await waitFor(() => expect(requestPushPermission).toHaveBeenCalled())
    expect(subscribeToPush).toHaveBeenCalled()
  })

  it('他按了「封鎖」就不要去訂閱，並且說得出唯一能改回來的地方', async () => {
    serve({})
    vi.mocked(requestPushPermission).mockResolvedValue('denied')
    const user = userEvent.setup()
    renderWizard()
    await user.click(await screen.findByRole('button', { name: /先跳過/ }))

    await user.click(await screen.findByRole('button', { name: /開啟這台裝置的推播/ }))

    await waitFor(() => expect(requestPushPermission).toHaveBeenCalled())
    // 訂閱一定會失敗（權限不是 granted），送出去只換到一句他看不懂的「未取得通知權限」。
    expect(subscribeToPush).not.toHaveBeenCalled()
    expect(await screen.findByText(/瀏覽器不會再問一次/)).toBeInTheDocument()
  })
})
