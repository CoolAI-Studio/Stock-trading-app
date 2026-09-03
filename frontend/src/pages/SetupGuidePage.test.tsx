import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SetupGuidePage } from './SetupGuidePage'
import { api } from '../lib/api'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

const LOCAL_FILE = {
  database: {
    kind: 'sqlite',
    ephemeral: false,
    status: 'ok',
    detail: '資料存在本機的一個檔案裡。',
  },
  platform: { name: '你的部署平台', env_where: '你的部署平台上「環境變數」那一頁' },
}

const EPHEMERAL_FILE = {
  database: {
    kind: 'sqlite',
    ephemeral: true,
    status: 'warn',
    detail: '資料存在容器裡的一個檔案，而這個平台每次重新部署都會換一個新的容器。',
  },
  platform: { name: 'Render', env_where: 'Render 後台 → 你的服務 → Environment' },
}

const POSTGRES = {
  database: { kind: 'postgres', ephemeral: false, status: 'ok', detail: '資料存在 Postgres 裡。' },
  platform: { name: 'Render', env_where: 'Render 後台 → 你的服務 → Environment' },
}

function serve({
  system = POSTGRES,
  ai = { configured: true },
  // 預設是一個**真的送過而且沒有失敗**的管道。光是存在不算完成——那正是底下
  // 「填了不算完成」那一組在守的事。
  channels = [
    {
      id: 3,
      label: '我的 Telegram',
      is_enabled: true,
      last_sent_at: '2026-09-01T00:00:00Z',
      last_error: null,
    },
  ],
}: { system?: unknown; ai?: unknown; channels?: unknown[] } = {}) {
  vi.mocked(api.get).mockImplementation((path: string) => {
    if (path.includes('system/status')) return Promise.resolve(system) as never
    if (path.includes('ai-settings')) return Promise.resolve(ai) as never
    if (path.includes('channels')) return Promise.resolve(channels) as never
    return Promise.resolve([]) as never
  })
}

function renderGuide() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/guide']}>
        <Routes>
          <Route path="/guide" element={<SetupGuidePage />} />
          <Route path="/ai-settings" element={<div>AI 設定頁</div>} />
          <Route path="/notifications" element={<div>通知設定頁</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/**
 * 設定引導：把資料庫接上線、把 AI 的 API 接上線、把通知接上線。
 *
 * 三件事的共同點是：**光是「填了」不算完成，要真的通得過。** 每一個分頁都有一個
 * 動作，而那個動作成功了才算數——這也是為什麼每一格旁邊的狀態是問後端問來的，
 * 不是記在畫面上的一個布林值。
 */
describe('設定引導（分頁）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.post).mockResolvedValue({ ok: true } as never)
    vi.mocked(api.put).mockResolvedValue({ configured: true } as never)
  })

  it('三件事各一個分頁', async () => {
    serve()
    renderGuide()

    expect(await screen.findByRole('tab', { name: /資料庫/ })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /AI/ })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /通知/ })).toBeInTheDocument()
  })

  it('一打開就停在第一個還沒完成的那一個', async () => {
    // 全部都好的時候停在第一個；資料庫有問題的時候當然也是資料庫。
    serve({ system: EPHEMERAL_FILE })
    renderGuide()

    expect(await screen.findByRole('tab', { name: /資料庫/, selected: true })).toBeInTheDocument()
  })

  it('資料庫已經是 Postgres 的時候就別再囉唆，直接停在下一件沒完成的事', async () => {
    serve({ system: POSTGRES, ai: { configured: false } })
    renderGuide()

    expect(await screen.findByRole('tab', { name: /AI/, selected: true })).toBeInTheDocument()
  })

  it('資料庫分頁講的是現況，不是一般性的說明', async () => {
    serve({ system: EPHEMERAL_FILE })
    renderGuide()

    expect(await screen.findByText(/每次重新部署都會換一個新的容器/)).toBeInTheDocument()
  })

  it('而且要用「你這個平台」的說法講那一格在哪裡填', async () => {
    serve({ system: EPHEMERAL_FILE })
    renderGuide()

    expect(await screen.findByText(/Render 後台 → 你的服務 → Environment/)).toBeInTheDocument()
  })

  it('本機的檔案資料庫不是錯誤，是一個選項', async () => {
    serve({ system: LOCAL_FILE })
    renderGuide()

    expect(await screen.findByText(/資料存在本機的一個檔案裡/)).toBeInTheDocument()
    // 不能出現「還沒設定完」那種說法。
    expect(screen.queryByText(/還沒設定/)).not.toBeInTheDocument()
  })

  it('AI 有設定的時候，資料庫這一格多一條問 AI 的路', async () => {
    // 使用者：「若 AI 有設，資料庫的設定就可以改跑 AI 引導；若沒有，那就需要事
    // 前比較多的引導頁去引導如何設定資料庫。」
    //
    // 做得到的地方是這裡（登入後、擁有者本人）。登入前的 /setup 做不到：那時候
    // 還沒有帳號，在那裡放 AI 對話框等於開一個不需要登入的 AI 端點，任何人都能
    // 燒掉部署者的額度——稽查員的規則就是「沒有帳號閘門的端點一律紅燈」。
    serve({ system: LOCAL_FILE, ai: { configured: true } })
    const user = userEvent.setup()
    renderGuide()

    await user.click(await screen.findByRole('tab', { name: /資料庫/ }))

    await user.type(await screen.findByLabelText(/問 AI/), '我想換成雲端資料庫，該怎麼做')
    await user.click(screen.getByRole('button', { name: /^問$|問問看/ }))

    await waitFor(() =>
      expect(
        vi.mocked(api.post).mock.calls.some(([path]) => String(path).includes('system/assist')),
      ).toBe(true),
    )
  })

  it('AI 沒設定的時候不給那條路 —— 靜態的引導自己要站得住', async () => {
    // 那份靜態引導不是備案，它是永遠都在的那一條。CLAUDE.md：設定流程不可以依賴
    // AI，因為 AI 需要一把金鑰，那本身就是一格空白。
    serve({ system: LOCAL_FILE, ai: { configured: false } })
    const user = userEvent.setup()
    renderGuide()

    await user.click(await screen.findByRole('tab', { name: /資料庫/ }))

    expect(screen.queryByLabelText(/問 AI/)).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /改用雲端/ })).toBeInTheDocument()
  })

  it('本機跑的時候，本機和雲端是兩個可以選的，不是系統替他決定', async () => {
    serve({ system: LOCAL_FILE })
    renderGuide()

    expect(await screen.findByRole('button', { name: /就用本機/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /改用雲端/ })).toBeInTheDocument()
  })

  it('選「改用雲端」就拿得到完整步驟 —— 那串步驟本來只有容器裡的人看得到', async () => {
    // 原本寫死在 `{database?.ephemeral && ...}` 裡面，所以在本機跑的人想搬上雲
    // 端，只會拿到一句「把連線字串放進 DATABASE_URL」。對不寫程式的人，那句話
    // 就是流程到此結束。
    serve({ system: LOCAL_FILE })
    const user = userEvent.setup()
    renderGuide()

    await user.click(await screen.findByRole('button', { name: /改用雲端/ }))

    expect(await screen.findByText(/去開一個 Postgres/)).toBeInTheDocument()
    expect(screen.getByText(/你的部署平台上「環境變數」那一頁/)).toBeInTheDocument()
  })

  it('選「就用本機」就不要再囉唆，只提醒備份', async () => {
    serve({ system: LOCAL_FILE })
    const user = userEvent.setup()
    renderGuide()

    await user.click(await screen.findByRole('button', { name: /就用本機/ }))

    expect(await screen.findByText(/不用再設定/)).toBeInTheDocument()
    expect(screen.getByText(/備份/)).toBeInTheDocument()
    expect(screen.queryByText(/去開一個 Postgres/)).not.toBeInTheDocument()
  })

  it('會被清空的那一種不給「用本機」這個選項，而且說得出為什麼', async () => {
    // 這裡不是偏好問題。容器裡的檔案下一次重新部署就沒了，把它列成一個選項，
    // 等於把「資料會不見」包裝成一個可以勾的方案。
    serve({ system: EPHEMERAL_FILE })
    renderGuide()

    expect(await screen.findByText(/去開一個 Postgres/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /就用本機/ })).not.toBeInTheDocument()
    expect(screen.getByText(/每次重新部署都會換一個新的容器/)).toBeInTheDocument()
  })

  it('改完之後可以在這裡重新檢查，不用自己猜生效了沒', async () => {
    serve({ system: EPHEMERAL_FILE })
    const user = userEvent.setup()
    renderGuide()

    const before = vi.mocked(api.get).mock.calls.length
    await user.click(await screen.findByRole('button', { name: /重新檢查/ }))

    await waitFor(() => expect(vi.mocked(api.get).mock.calls.length).toBeGreaterThan(before))
  })

  it('AI 還沒接上線的時候，明說需要 AI 的功能是關著的、其他不受影響', async () => {
    serve({ ai: { configured: false } })
    const user = userEvent.setup()
    renderGuide()

    await user.click(await screen.findByRole('tab', { name: /AI/ }))

    expect(await screen.findByText(/需要 AI 的功能現在是關著的/)).toBeInTheDocument()
    expect(screen.getByText(/其他功能完全不受影響/)).toBeInTheDocument()
  })

  it('AI 接上線之後，這裡按一下就知道它是不是真的通', async () => {
    serve({ ai: { configured: true } })
    const user = userEvent.setup()
    renderGuide()

    await user.click(await screen.findByRole('tab', { name: /AI/ }))
    await user.click(await screen.findByRole('button', { name: /測試/ }))

    await waitFor(() =>
      expect(vi.mocked(api.post).mock.calls.some(([p]) => String(p).includes('ai-settings/test'))).toBe(
        true,
      ),
    )
    expect(await screen.findByText(/通了/)).toBeInTheDocument()
  })

  it('沒有金鑰的時候就在這一頁貼上去 —— 不要把人踢到別的頁面', async () => {
    // 這一格本來只有一串指示：「到 AI 輔助那一頁貼上金鑰，再回來按測試」。
    // 引導最容易斷掉的就是這種一步——離開了還回不回得來，不在我們手上。而
    // CLAUDE.md 的第一條規則就是「永遠不要叫他去別的地方拿一個值」：金鑰確實
    // 要去供應商那裡拿（那是誠實的），但「貼上」發生在哪一頁是我們決定的。
    serve({
      ai: {
        configured: false,
        provider: 'openai_compatible',
        base_url: 'https://openrouter.ai/api/v1',
        model: '',
        source: 'none',
      },
    })
    const user = userEvent.setup()
    renderGuide()

    await user.click(await screen.findByRole('tab', { name: /AI/ }))

    await user.type(await screen.findByLabelText('API 金鑰'), 'sk-test-123')
    await user.type(screen.getByLabelText('模型'), 'anthropic/claude-opus-5')
    await user.click(screen.getByRole('button', { name: '儲存' }))

    await waitFor(() => expect(api.put).toHaveBeenCalled())
    const [path, body] = vi.mocked(api.put).mock.calls.at(-1)!
    expect(String(path)).toContain('/api/ai-settings')
    expect(body).toMatchObject({ api_key: 'sk-test-123', model: 'anthropic/claude-opus-5' })

    // 而且人還在引導頁上。
    expect(screen.getByRole('tab', { name: /AI/ })).toBeInTheDocument()
    expect(screen.queryByText('AI 設定頁')).not.toBeInTheDocument()
  })

  it('金鑰要去哪裡拿還是要老實說 —— 那一個 app 真的生不出來', async () => {
    // 同一條規則的另一半：app 生得出來的就給按鈕，生不出來的就老實說，不要假裝。
    serve({ ai: { configured: false } })
    const user = userEvent.setup()
    renderGuide()

    await user.click(await screen.findByRole('tab', { name: /AI/ }))

    expect(await screen.findByRole('link', { name: /OpenRouter/ })).toHaveAttribute(
      'href',
      expect.stringContaining('openrouter.ai'),
    )
  })

  it('測試失敗的時候把原因講出來，不要只說失敗', async () => {
    serve({ ai: { configured: true } })
    vi.mocked(api.post).mockResolvedValue({ ok: false, error: '這個模型不存在。' } as never)
    const user = userEvent.setup()
    renderGuide()

    await user.click(await screen.findByRole('tab', { name: /AI/ }))
    await user.click(await screen.findByRole('button', { name: /測試/ }))

    expect(await screen.findByText(/這個模型不存在/)).toBeInTheDocument()
  })

  it('通知分頁：已經有管道就給「傳一則測試」，而且要真的送得出去才算', async () => {
    serve()
    const user = userEvent.setup()
    renderGuide()

    await user.click(await screen.findByRole('tab', { name: /通知/ }))
    await user.click(await screen.findByRole('button', { name: /傳一則測試/ }))

    await waitFor(() =>
      expect(
        vi.mocked(api.post).mock.calls.some(([p]) => String(p).includes('/channels/3/test')),
      ).toBe(true),
    )
  })

  /** 等到後端的答案真的回來了。
   *
   * **沒有這一步，⭕ 那兩條會在載入畫面上就通過。** 第一次 render 的時候三個查詢都還
   * 沒回來，所以 done 全是 false、三格都是 ⭕——`findByRole('tab', {name: /⭕.*通知/})`
   * 立刻命中，判準改壞了也不會紅。實測過：把判準換回舊的 `channels.length > 0`，那兩
   * 條照樣綠。
   *
   * 資料庫那一格是 POSTGRES，所以它變成 ✅ 就代表 /api/system/status 回來了；AI 那一
   * 格同理。兩個都到齊，通知那一格顯示的才是真的判準算出來的。
   */
  async function loaded() {
    await screen.findByRole('tab', { name: '✅ 資料庫' })
    await screen.findByRole('tab', { name: '✅ AI 的 API' })
  }

  // --- 「填了不算完成」對通知那一格原本是假的 -------------------------------
  //
  // 這一頁的檔頭自己寫著「三件事的共同點是：光是填了不算完成，要真的通得過」，而
  // 通知那一格原本只看 `channels.length > 0`——建了一列就打勾。
  //
  // 那是這個產品最不能有的謊：他看到 ✅ 就相信提醒會到，然後第一則真的警告安靜地
  // 掉在地上。而這一頁上早就寫著「沒有真的收到就不算完成」，只是那句話跟旁邊的勾
  // 勾說的不是同一件事。
  //
  // `last_sent_at` 在 dispatcher 裡是**成功和失敗都會寫**的（它其實是「最後一次試
  // 過」），所以判準要兩個一起看：試過，而且那一次沒有錯。

  it('通知分頁：管道建好了但從來沒送成功過，不算完成', async () => {
    serve({
      channels: [{ id: 3, label: '我的 Telegram', is_enabled: true, last_sent_at: null }],
    })
    renderGuide()

    await loaded()

    expect(screen.getByRole('tab', { name: '⭕ 通知' })).toBeInTheDocument()
  })

  it('通知分頁：最後一次送出去是失敗的，也不算完成', async () => {
    // last_sent_at 有值不代表送到了——失敗也會寫。只看它的話，一個憑證過期的管道
    // 會永遠掛著一個 ✅。
    serve({
      channels: [
        {
          id: 3,
          label: '我的 Telegram',
          is_enabled: true,
          last_sent_at: '2026-09-01T00:00:00Z',
          last_error: 'HTTP 401',
        },
      ],
    })
    renderGuide()

    await loaded()

    expect(screen.getByRole('tab', { name: '⭕ 通知' })).toBeInTheDocument()
  })

  it('通知分頁：真的送成功過才打勾', async () => {
    serve()
    renderGuide()

    expect(await screen.findByRole('tab', { name: '✅ 通知' })).toBeInTheDocument()
  })

  it('通知分頁：沒送成功過的時候，引導就停在通知這一格', async () => {
    // 停在哪一格是這一頁唯一會主動說話的地方。判準錯了，它就會把他送去別的地方，
    // 而那一格其實已經好了。
    serve({
      channels: [{ id: 3, label: '我的 Telegram', is_enabled: true, last_sent_at: null }],
    })
    renderGuide()

    expect(await screen.findByRole('tab', { name: /通知/, selected: true })).toBeInTheDocument()
  })

  it('通知分頁：一個管道都沒有的時候，先給不用去別的地方拿值的那一種', async () => {
    serve({ channels: [] })
    const user = userEvent.setup()
    renderGuide()

    await user.click(await screen.findByRole('tab', { name: /通知/ }))

    expect(await screen.findByRole('button', { name: /開啟這台裝置的推播/ })).toBeInTheDocument()
  })
})
