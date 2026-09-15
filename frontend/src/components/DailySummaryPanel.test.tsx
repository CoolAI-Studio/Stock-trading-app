import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DailySummaryPanel } from './DailySummaryPanel'
import { api } from '../lib/api'
import type { DailySummaryState } from '../lib/types'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn(), put: vi.fn() },
}))

const OFF: DailySummaryState = {
  is_enabled: false,
  markets: [
    { market: 'tw', label: '台股', symbols: ['2330.TW', '2454.TW'], done_on: null },
    { market: 'us', label: '美股', symbols: [], done_on: null },
  ],
  unsupported: ['BTCUSDT'],
  last_sent_at: null,
  last_error: null,
  channels: { enabled: 1, receiving: 1 },
}

function renderPanel(state: DailySummaryState = OFF) {
  vi.mocked(api.get).mockResolvedValue(state as never)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DailySummaryPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/**
 * 收盤摘要的開關（ONBOARDING.md 方案 2，#117）。
 *
 * 清單就是自選股，所以這一格不收代號——但它要說得出「打開之後會發生什麼」，否則他打開了
 * 也無從判斷有沒有用：哪幾檔會進摘要、哪一則根本不會送、哪幾檔永遠不會出現、上一次送出
 * 是什麼時候、沒送成又是為什麼。
 */
describe('收盤摘要的開關', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('說得出每個市場會摘要哪幾檔', async () => {
    renderPanel()

    expect(await screen.findByText(/2330\.TW、2454\.TW/)).toBeInTheDocument()
  })

  it('那個市場沒有自選的時候，明說那一則不會送', async () => {
    renderPanel()

    expect(await screen.findByText(/自選股裡沒有美股/)).toBeInTheDocument()
  })

  it('加密貨幣不收盤，要說它不會出現在摘要裡——否則他會等一則永遠不會來的', async () => {
    renderPanel()

    const line = await screen.findByText(/BTCUSDT/)
    expect(line).toHaveTextContent(/不收盤/)
  })

  it('打開就記下來', async () => {
    vi.mocked(api.put).mockResolvedValue({ ...OFF, is_enabled: true } as never)
    const user = userEvent.setup()
    renderPanel()

    await user.click(await screen.findByRole('checkbox', { name: /收盤後傳一則/ }))

    expect(api.put).toHaveBeenCalledWith('/api/daily-summary', { is_enabled: true })
  })

  it('上一次沒送成的原因要看得到——那是唯一說得出「今天為什麼沒收到」的地方', async () => {
    renderPanel({
      ...OFF,
      is_enabled: true,
      last_error: '台股今天（9/15）還拿不到任何一檔的收盤資料——可能是休市日',
    })

    expect(await screen.findByText(/還拿不到任何一檔/)).toBeInTheDocument()
  })

  it('送出過就說是什麼時候', async () => {
    renderPanel({ ...OFF, is_enabled: true, last_sent_at: '2026-09-15T05:45:00Z' })

    expect(await screen.findByText(/上一次送出/)).toBeInTheDocument()
  })

  // --- 整理好了，送不送得到 --------------------------------------------------------
  //
  // （一次唯讀審查抓到的）通知頁在「沒有全勾」的時候存的是一份清單，而那份清單是在收盤摘要
  // 存在之前存的，裡面不會有它。派送器照清單跳過，這一格卻照樣寫「上一次送出」——他打開了、
  // 每天都「送出」、一則都沒收到，而畫面上沒有任何一個字不對勁。

  it('打開了、卻沒有任何通知管道：說清楚整理好了也沒有地方送', async () => {
    renderPanel({ ...OFF, is_enabled: true, channels: { enabled: 0, receiving: 0 } })

    expect(await screen.findByText(/還沒有任何通知管道/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /設定通知管道/ })).toHaveAttribute(
      'href',
      '/notifications',
    )
  })

  it('有管道、但每一個都沒勾收盤摘要：明說這一則不會到', async () => {
    renderPanel({ ...OFF, is_enabled: true, channels: { enabled: 2, receiving: 0 } })

    expect(await screen.findByText(/沒有勾「每日收盤摘要」/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /到通知頁勾起來/ })).toHaveAttribute(
      'href',
      '/notifications',
    )
  })

  it('有管道收得到的時候，不多說', async () => {
    renderPanel({ ...OFF, is_enabled: true, channels: { enabled: 2, receiving: 1 } })

    await screen.findByText(/2330\.TW、2454\.TW/)
    expect(screen.queryByText(/通知管道/)).not.toBeInTheDocument()
  })

  it('還沒打開的時候，不拿管道的事去煩他', async () => {
    renderPanel({ ...OFF, channels: { enabled: 0, receiving: 0 } })

    await screen.findByText(/2330\.TW、2454\.TW/)
    expect(screen.queryByText(/通知管道/)).not.toBeInTheDocument()
  })

  it('後端比畫面舊、還不會回管道的狀況：照常顯示，不亂猜', async () => {
    const { channels: _unused, ...older } = OFF
    renderPanel({ ...(older as DailySummaryState), is_enabled: true })

    expect(await screen.findByText(/2330\.TW、2454\.TW/)).toBeInTheDocument()
    expect(screen.queryByText(/通知管道/)).not.toBeInTheDocument()
  })
})
