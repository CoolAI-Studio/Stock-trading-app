import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PriceChart } from './PriceChart'
import { api } from '../lib/api'
import type { BarsResponse } from '../lib/types'

vi.mock('../lib/api', () => ({ api: { get: vi.fn() } }))

/**
 * The chart, drawn from data this app already has.
 *
 * TradingView's free embedded widget answers 「此商品僅在 TradingView 上可用」 for
 * Taiwanese symbols -- its own words for 「the symbol is real, but this widget
 * is not licensed to show its data」. The symbol was never wrong; the dialog
 * itself reads TWSE:0050, which is exactly what lib/tradingView.ts produces.
 * It is a licensing restriction and no amount of symbol correctness reaches it.
 *
 * There is no 「fall back when the widget fails」, because the widget renders
 * inside an iframe and this page cannot see it fail. It has to be one or the
 * other, so it is the one that works for every symbol this app can price.
 *
 * WHAT IS TESTED HERE is the data path and the states around it -- that the
 * right series reaches the renderer, and that loading, empty and failed all
 * say something. Whether TradingView's own library draws correct pixels is
 * their test, not ours.
 */

const setData = vi.fn()
const setVolumeData = vi.fn()
const remove = vi.fn()
const fitContent = vi.fn()

vi.mock('lightweight-charts', () => ({
  createChart: vi.fn(() => ({
    addSeries: vi.fn((definition: unknown) => ({
      setData: definition === 'HISTOGRAM' ? setVolumeData : setData,
      priceScale: () => ({ applyOptions: vi.fn() }),
      applyOptions: vi.fn(),
    })),
    timeScale: () => ({ fitContent }),
    applyOptions: vi.fn(),
    remove,
  })),
  CandlestickSeries: 'CANDLESTICK',
  HistogramSeries: 'HISTOGRAM',
  ColorType: { Solid: 'solid' },
}))

const BARS: BarsResponse = {
  symbol: '0050.TW',
  timeframe: '1d',
  bars: [
    { time: '2026-08-18T00:00:00Z', open: 100, high: 104, low: 99, close: 103, volume: 1200 },
    { time: '2026-08-19T00:00:00Z', open: 103, high: 106, low: 102, close: 105, volume: 1400 },
  ],
}

function show(symbol = '0050.TW') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <PriceChart symbol={symbol} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.get).mockResolvedValue(BARS as never)
})

// --- the symbol the embedded widget refuses -----------------------------------

describe('台股也畫得出來', () => {
  it('0050.TW 有 K 棒就畫出來', async () => {
    show()

    await vi.waitFor(() => expect(setData).toHaveBeenCalled())
    expect(setData.mock.calls[0][0]).toHaveLength(2)
  })

  it('送給繪圖的是秒為單位的時間，不是毫秒', async () => {
    // The library reads a bare number as UNIX SECONDS. Handing it milliseconds
    // draws every candle somewhere around the year 57000, and the chart comes
    // back empty with no error anywhere.
    show()

    await vi.waitFor(() => expect(setData).toHaveBeenCalled())
    const first = setData.mock.calls[0][0][0]
    expect(first.time).toBe(Math.floor(Date.parse('2026-08-18T00:00:00Z') / 1000))
  })

  it('開高低收都傳過去，不是只有收盤價', async () => {
    show()

    await vi.waitFor(() => expect(setData).toHaveBeenCalled())
    expect(setData.mock.calls[0][0][0]).toMatchObject({
      open: 100,
      high: 104,
      low: 99,
      close: 103,
    })
  })

  it('成交量也畫', async () => {
    show()

    await vi.waitFor(() => expect(setVolumeData).toHaveBeenCalled())
  })

  it('中文公司名不要送出去問後端 —— 那一定是 422', async () => {
    show('台積電')

    await new Promise((r) => setTimeout(r, 50))
    expect(api.get).not.toHaveBeenCalled()
  })
})

// --- every state says something ------------------------------------------------

describe('沒有圖的時候要說為什麼', () => {
  it('載入中會講', async () => {
    vi.mocked(api.get).mockReturnValue(new Promise(() => {}) as never)
    show()

    expect(await screen.findByText(/載入中/)).toBeInTheDocument()
  })

  it('真的沒有歷史資料時說沒有，不是留一片黑', async () => {
    vi.mocked(api.get).mockResolvedValue({ ...BARS, bars: [] } as never)
    show()

    expect(await screen.findByText(/沒有.*歷史|查不到/)).toBeInTheDocument()
  })

  it('抓失敗要說失敗，不要跟「沒有資料」混在一起', async () => {
    // A blank chart with no explanation is indistinguishable from a typo, an
    // outage, and a broken app.
    vi.mocked(api.get).mockRejectedValue(new Error('boom'))
    show()

    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
})

// --- choosing the candle size ---------------------------------------------------

describe('週期切換', () => {
  it('可以換成週線', async () => {
    const user = userEvent.setup()
    show()
    await vi.waitFor(() => expect(api.get).toHaveBeenCalled())

    await user.click(screen.getByRole('button', { name: '週' }))

    await vi.waitFor(() =>
      expect(vi.mocked(api.get).mock.calls.at(-1)?.[0]).toContain('timeframe=1wk'),
    )
  })

  it('現在選的是哪一個要看得出來', async () => {
    show()

    const day = await screen.findByRole('button', { name: '日' })
    expect(day).toHaveAttribute('aria-pressed', 'true')
  })
})

// --- and the full tool is still one click away -----------------------------------

describe('還是留一條路去 TradingView', () => {
  it('給一個連結，用 TradingView 自己的代號寫法', async () => {
    // The embedded widget cannot show this symbol, but tradingview.com can --
    // the restriction is on redistribution, not on the site.
    show()

    const link = await screen.findByRole('link', { name: /TradingView/ })
    expect(link).toHaveAttribute('href', expect.stringContaining('TWSE%3A0050'))
  })

  it('在新分頁開，不要把使用者從自己的儀表板帶走', async () => {
    show()

    expect(await screen.findByRole('link', { name: /TradingView/ })).toHaveAttribute(
      'target',
      '_blank',
    )
  })
})

// --- cleaning up -----------------------------------------------------------------

describe('離開頁面', () => {
  it('把圖表拆掉，不要留著吃記憶體', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const view = render(
      <QueryClientProvider client={client}>
        <PriceChart symbol="0050.TW" />
      </QueryClientProvider>,
    )
    await vi.waitFor(() => expect(setData).toHaveBeenCalled())

    view.unmount()

    expect(remove).toHaveBeenCalled()
  })
})

// --- the container is real, so the tests above are not testing a stub ------------

describe('元件本身', () => {
  it('畫布掛在畫面上，而且資料到了就不再蓋著「載入中」', async () => {
    show()

    // Waited on the DATA, not on the container: the container renders
    // immediately either way, so asserting on it alone would pass while the
    // overlay was still covering the chart.
    await vi.waitFor(() => expect(setData).toHaveBeenCalled())

    const region = screen.getByRole('img', { name: /0050\.TW/ })
    expect(within(region.parentElement!).queryByText(/載入中/)).not.toBeInTheDocument()
  })
})
