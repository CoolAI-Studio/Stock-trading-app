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
  channels = [{ id: 3, label: '我的 Telegram', is_enabled: true }],
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

  it('通知分頁：一個管道都沒有的時候，先給不用去別的地方拿值的那一種', async () => {
    serve({ channels: [] })
    const user = userEvent.setup()
    renderGuide()

    await user.click(await screen.findByRole('tab', { name: /通知/ }))

    expect(await screen.findByRole('button', { name: /開啟這台裝置的推播/ })).toBeInTheDocument()
  })
})
