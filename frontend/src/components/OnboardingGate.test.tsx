import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { OnboardingGate } from './OnboardingGate'
import { api } from '../lib/api'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn() },
}))

function serve({
  strategies = [],
  watchlist = [],
  channels = [],
}: {
  strategies?: unknown[]
  watchlist?: unknown[]
  channels?: unknown[]
}) {
  vi.mocked(api.get).mockImplementation((path: string) => {
    if (path.includes('watchlist')) return Promise.resolve(watchlist) as never
    if (path.includes('channels')) return Promise.resolve(channels) as never
    if (path.includes('strategies')) return Promise.resolve(strategies) as never
    return Promise.resolve([]) as never
  })
}

function renderGate() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route
            path="/"
            element={
              <OnboardingGate>
                <div>儀表板</div>
              </OnboardingGate>
            }
          />
          <Route path="/welcome" element={<div>引導流程</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/**
 * 剛建好帳號的人看到的第一個畫面，本來是一個空的儀表板——上面沒有任何一句話說
 * 下一步該做什麼，而「下一步」在那時候需要打開一個程式碼編輯器。
 *
 * 這個閘門是獨立的元件而不是寫進 DashboardPage 裡，理由很實際：那一頁有三十幾條
 * 測試都在沒有 Router 的情況下 render，把 <Navigate> 塞進去等於為了一個小功能
 * 讓一整片測試改寫。
 */
describe('空帳號自動進引導', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('什麼都還沒有的時候，帶他去引導', async () => {
    serve({})

    renderGate()

    expect(await screen.findByText('引導流程')).toBeInTheDocument()
  })

  it('已經有策略就不要打擾他', async () => {
    serve({ strategies: [{ id: 1 }] })

    renderGate()

    expect(await screen.findByText('儀表板')).toBeInTheDocument()
  })

  it('只有自選股也算開始用了', async () => {
    serve({ watchlist: [{ id: 1, symbol: '2330.TW' }] })

    renderGate()

    expect(await screen.findByText('儀表板')).toBeInTheDocument()
  })

  it('只有通知管道也算', async () => {
    serve({ channels: [{ id: 1, is_enabled: true }] })

    renderGate()

    expect(await screen.findByText('儀表板')).toBeInTheDocument()
  })

  it('他自己離開過引導之後，就不要再抓他回去', async () => {
    // 沒有這一條，「我知道自己在做什麼，直接進儀表板」會變成一個迴圈：帳號還是
    // 空的，所以又被導回引導。
    localStorage.setItem('onboarding-seen', '1')
    serve({})

    renderGate()

    expect(await screen.findByText('儀表板')).toBeInTheDocument()
  })

  it('還沒問到答案之前先給儀表板，不要卡在空白畫面', async () => {
    vi.mocked(api.get).mockReturnValue(new Promise(() => {}) as never)

    renderGate()

    expect(screen.getByText('儀表板')).toBeInTheDocument()
  })

  it('讀不到就不要亂導 —— 後端有問題是另一件事', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('backend down'))

    renderGate()

    await waitFor(() => expect(api.get).toHaveBeenCalled())
    expect(screen.getByText('儀表板')).toBeInTheDocument()
  })
})
