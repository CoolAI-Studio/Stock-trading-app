/**
 * 版本清單、差異、還原按鈕。
 *
 * 後端做完了（#35），但那三個端點對這個使用者等於不存在——他不會去打 API。這個元件
 * 是「改壞了有路可以回去」唯一到得了他手上的地方。
 *
 * ＊ 這一頁最重要的一句話：還原之後會發生什麼事。
 *
 * 他不會寫 Python，而他即將把正在盯盤的策略換成三個月前的版本。按鈕上必須說清楚那
 * 件事是可以再還原回來的——否則他會不敢按，而一個不敢按的還原鍵等於沒有還原功能。
 *
 * ＊ 還原被拒絕的時候，要說清楚不是他的錯。
 *
 * 舊版本可能因為我們收緊了沙箱而編不過（#50 就是在處理那件事的後果）。那時候顯示
 * 一句「編譯失敗」會讓他去改一段其實沒有問題的程式碼——而他改不動，因為問題不在
 * 那裡。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { StrategyVersions } from './StrategyVersions'
import { ApiError, api } from '../lib/api'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

const NOW = 'class Strategy:\n    def on_tick(self, p):\n        return "BUY"\n'
const BEFORE = 'class Strategy:\n    def on_tick(self, p):\n        return "HOLD"\n'

const VERSIONS = [
  {
    id: 3,
    source_code: NOW,
    params: {},
    code_hash: 'ccc',
    author: 'ai',
    created_at: '2026-08-20T10:00:00Z',
  },
  {
    id: 1,
    source_code: BEFORE,
    params: {},
    code_hash: 'aaa',
    author: 'manual',
    created_at: '2026-08-01T09:00:00Z',
  },
]

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <StrategyVersions strategyId={7} currentSource={NOW} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.get).mockResolvedValue(VERSIONS as never)
  vi.mocked(api.post).mockResolvedValue({} as never)
})

describe('版本清單', () => {
  it('看得出哪一版是現在在跑的', async () => {
    // 沒有這個標記的話，他會不知道自己在看的是歷史還是現況——而「還原到我已經在跑
    // 的那一版」是一個沒有意義、但看起來完全合理的操作。
    renderPanel()

    const rows = await screen.findAllByRole('listitem')
    expect(rows[0]).toHaveTextContent(/現在/)
    expect(rows[1]).not.toHaveTextContent(/現在/)
  })

  it('說得出每一版是誰改的', async () => {
    // 「AI 改的」和「我自己改的」在他心裡是兩件完全不同的事。而 restore 也要標出
    // 來——一個看起來跟三個月前一模一樣的版本，如果沒說它是還原來的，他會以為自己
    // 的編輯不見了。
    renderPanel()

    const rows = await screen.findAllByRole('listitem')
    expect(rows[0]).toHaveTextContent(/AI/)
    expect(rows[1]).toHaveTextContent(/自己|手動/)
  })

  it('點一版看得到它跟現在差在哪裡', async () => {
    // 給他兩段看起來幾乎一樣的程式碼、叫他自己找出差在哪，等於沒給。
    const user = userEvent.setup()
    renderPanel()

    const rows = await screen.findAllByRole('listitem')
    await user.click(within(rows[1]).getByRole('button', { name: /看|比較/ }))

    expect(await screen.findByText(/return "HOLD"/)).toBeInTheDocument()
  })

  it('還原按鈕說得出它會怎樣，而且說得出可以再還原回來', async () => {
    // 他即將把正在盯盤的策略換成三個月前的版本。不說清楚那是可逆的，他就不敢按，
    // 而一個不敢按的還原鍵等於沒有還原功能。
    renderPanel()

    await screen.findAllByRole('listitem')
    // 用 <strong> 裡那一句，因為它跟外層的 <p> 都會命中同一個 regex。
    expect(screen.getByText('還原不會刪掉任何東西')).toBeInTheDocument()
  })

  it('按下還原真的送出去', async () => {
    const user = userEvent.setup()
    renderPanel()

    const rows = await screen.findAllByRole('listitem')
    await user.click(within(rows[1]).getByRole('button', { name: /還原/ }))

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/strategies/7/versions/1/restore'),
    )
  })

  it('還原被拒絕的時候，說清楚不是他的錯', async () => {
    // 舊版本可能因為我們收緊了沙箱而編不過。顯示一句「編譯失敗」會讓他去改一段其
    // 實沒有問題的程式碼——而他改不動，因為問題不在那裡。
    vi.mocked(api.post).mockRejectedValue(
      new ApiError(422, '這一版現在編不過了，所以不能還原：importing os is not allowed'),
    )
    const user = userEvent.setup()
    renderPanel()

    const rows = await screen.findAllByRole('listitem')
    await user.click(within(rows[1]).getByRole('button', { name: /還原/ }))

    expect(await screen.findByRole('status')).toHaveTextContent(/編不過/)
  })

  it('只有一版的時候不要擺一個空的清單在那裡', async () => {
    // 剛建立的策略只有一版，而那一版就是現在在跑的。一個只有一列、按鈕還不能按的
    // 清單，只是在佔畫面。
    vi.mocked(api.get).mockResolvedValue([VERSIONS[0]] as never)
    renderPanel()

    expect(await screen.findByText(/還沒有.*版本|只有這一版/)).toBeInTheDocument()
  })
})
