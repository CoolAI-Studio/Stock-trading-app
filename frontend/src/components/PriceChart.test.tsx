import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PriceChart } from './PriceChart'
import { ApiError, api } from '../lib/api'
import type { BarsResponse } from '../lib/types'

// importOriginal so ApiError is the real class -- the component uses
// `instanceof` to tell a 404 from any other failure.
vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn(), post: vi.fn() },
}))

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

/** The options the chart was created with, so `fixLeftEdge` can be asserted.
 *
 * It is not cosmetic. Without it the canvas scrolls freely past the oldest
 * candle into blank space -- which is the state the user reported: 「往前拉以
 * 往數據不會再讀取資料，只看到空的畫面」. */
const chartOptions: Record<string, unknown>[] = []

/** Whoever the chart asked to be told when the visible window moves.
 *
 * The lazy loading hangs off this callback, and there is no DOM to drag: the
 * chart is a canvas. Calling the handler IS the user panning, as far as this
 * component can tell. */
type LogicalRange = { from: number; to: number }
const rangeHandlers: ((range: LogicalRange | null) => void)[] = []
const setVisibleRange = vi.fn()
const getVisibleRange = vi.fn(() => ({ from: 1, to: 2 }))
/** 初始視角是用「最後 N 根」設的，所以它是索引不是時間。 */
const setVisibleLogicalRange = vi.fn()
const getVisibleLogicalRange = vi.fn(() => ({ from: 0, to: 1 }))

/** Every line series handed to the renderer, with the pane it went on.
 *
 * Recorded rather than read back from the DOM because the pane is the whole
 * point and there is no DOM -- it is canvas. RSI runs 0-100 and OBV runs to
 * +-7.6e7; either on the price axis flattens the candles into a line at the
 * bottom of the chart. The pane index this component asks for IS the
 * observable behaviour.
 */
type DrawnLine = { pane: number | undefined; options: Record<string, unknown>; data: unknown[] }
const lines: DrawnLine[] = []
const removeSeries = vi.fn((series: { entry?: DrawnLine }) => {
  if (series?.entry) series.entry.data = []
})
const removePane = vi.fn()
// The share of the height each pane asks for. Without it the library splits by
// its own default and the candles shrink every time an oscillator is added.
const stretch: number[] = []
const panes = () =>
  Array.from({ length: 1 + new Set(lines.filter((l) => l.pane).map((l) => l.pane)).size }, () => ({
    setStretchFactor: (value: number) => stretch.push(value),
  }))

vi.mock('lightweight-charts', () => ({
  createChart: vi.fn((_container: unknown, options: Record<string, unknown>) => {
    chartOptions.push(options)
    return {
    addSeries: vi.fn((definition: unknown, options: Record<string, unknown>, pane?: number) => {
      if (definition === 'LINE') {
        const entry: DrawnLine = { pane, options, data: [] }
        lines.push(entry)
        // The handle carries its entry so removeSeries can empty it. Without
        // that, a removed line still counts as drawn and every 「the stale line
        // is gone」 test is green for free.
        return {
          entry,
          setData: (data: unknown[]) => {
            entry.data = data
          },
          priceScale: () => ({ applyOptions: vi.fn() }),
          applyOptions: vi.fn(),
        }
      }
      return {
        setData: definition === 'HISTOGRAM' ? setVolumeData : setData,
        priceScale: () => ({ applyOptions: vi.fn() }),
        applyOptions: vi.fn(),
      }
    }),
    removeSeries,
    panes,
    removePane,
    timeScale: () => ({
      fitContent,
      subscribeVisibleLogicalRangeChange: (handler: (range: LogicalRange | null) => void) => {
        rangeHandlers.push(handler)
      },
      unsubscribeVisibleLogicalRangeChange: (handler: (range: LogicalRange | null) => void) => {
        const at = rangeHandlers.indexOf(handler)
        if (at >= 0) rangeHandlers.splice(at, 1)
      },
      getVisibleRange,
      setVisibleRange,
      getVisibleLogicalRange,
      setVisibleLogicalRange,
      applyOptions: vi.fn(),
    }),
    applyOptions: vi.fn(),
    remove,
    }
  }),
  CandlestickSeries: 'CANDLESTICK',
  HistogramSeries: 'HISTOGRAM',
  LineSeries: 'LINE',
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

const TIMEFRAMES = {
  sources: [
    {
      data_source: 'yfinance',
      timeframes: [
        { value: '1m', label: '1 分線', max_bars: 2800 },
        { value: '5m', label: '5 分線', max_bars: 4600 },
        { value: '15m', label: '15 分線', max_bars: 1550 },
        { value: '30m', label: '30 分線', max_bars: 780 },
        { value: '1h', label: '1 小時線', max_bars: 5000 },
        { value: '4h', label: '4 小時線', max_bars: 120 },
        { value: '1d', label: '日線', max_bars: 10000 },
        { value: '1wk', label: '週線', max_bars: 5000 },
        { value: '1mo', label: '月線', max_bars: 1200 },
      ],
    },
    {
      data_source: 'binance',
      timeframes: [
        { value: '1h', label: '1 小時線', max_bars: 1000 },
        { value: '4h', label: '4 小時線', max_bars: 1000 },
        { value: '12h', label: '12 小時線', max_bars: 1000 },
        { value: '1d', label: '日線', max_bars: 1000 },
      ],
    },
  ],
}

const CATALOGUE = {
  indicators: [
    {
      name: 'sma',
      title: '簡單移動平均',
      category: 'trend',
      category_label: '趨勢',
      outputs: [{ key: '', pane: 'price', scale: 'sma' }],
      params: [{ name: 'period', type: 'int', default: 20 }],
    },
    {
      name: 'rsi',
      title: '相對強弱指標',
      category: 'momentum',
      category_label: '動能',
      outputs: [{ key: '', pane: 'own', scale: 'rsi' }],
      params: [{ name: 'period', type: 'int', default: 14 }],
    },
    {
      name: 'macd',
      title: 'MACD',
      category: 'trend',
      category_label: '趨勢',
      outputs: [
        { key: 'macd', pane: 'own', scale: 'macd' },
        { key: 'signal', pane: 'own', scale: 'macd' },
        { key: 'histogram', pane: 'own', scale: 'macd' },
      ],
      params: [],
    },
  ],
}

function show(symbol = '0050.TW') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={client}>
      <PriceChart symbol={symbol} />
    </QueryClientProvider>,
  )
  // Switching symbol on the SAME mounted chart, which is what the dashboard
  // does. Re-rendering from scratch would create a new canvas and hide exactly
  // the stale-series bugs worth testing for.
  return {
    rerender: (next: string) =>
      view.rerender(
        <QueryClientProvider client={client}>
          <PriceChart symbol={next} />
        </QueryClientProvider>,
      ),
  }
}

/** Fail the BARS request only.
 *
 * `mockRejectedValue` fails every GET, including the indicator catalogue --
 * which puts a second role="alert" on the page and makes a test about the
 * chart's overlay assert against the picker's warning instead.
 */
function failBars(error: Error) {
  vi.mocked(api.get).mockImplementation((path: string) =>
    path.includes('/bars')
      ? Promise.reject(error)
      : path.includes('/timeframes')
        ? (Promise.resolve(TIMEFRAMES) as never)
        : (Promise.resolve(CATALOGUE) as never),
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  lines.length = 0
  stretch.length = 0
  chartOptions.length = 0
  rangeHandlers.length = 0
  window.localStorage.clear()
  // Path-aware, because this page now makes two different GETs: the candles
  // and the indicator catalogue. One blanket mockResolvedValue answers the
  // catalogue with a bars payload, the picker reads no indicators out of it,
  // and every parameter box silently fails to render.
  vi.mocked(api.get).mockImplementation((path: string) => {
    if (path.includes('/indicators/available')) return Promise.resolve(CATALOGUE) as never
    if (path.includes('/timeframes')) return Promise.resolve(TIMEFRAMES) as never
    return Promise.resolve(BARS) as never
  })
  vi.mocked(api.post).mockResolvedValue({ symbol: '0050.TW', timeframe: '1d', series: [] } as never)
})


/** The candles actually drawn.
 *
 * `setData` is now called with [] first on every render where there is no data
 * -- pending, empty, failed -- because leaving the previous symbol's candles on
 * the canvas was its own bug. So a test about the DRAWN series has to wait for a
 * non-empty call rather than for the first one.
 */
async function drawn(): Promise<Record<string, number>[]> {
  await vi.waitFor(() => {
    const last = setData.mock.calls.at(-1)?.[0]
    expect(last?.length).toBeGreaterThan(0)
  })
  return setData.mock.calls.at(-1)![0]
}

// --- the symbol the embedded widget refuses -----------------------------------

describe('台股也畫得出來', () => {
  it('0050.TW 有 K 棒就畫出來', async () => {
    show()

    expect(await drawn()).toHaveLength(2)
  })

  it('送給繪圖的是秒為單位的時間，不是毫秒', async () => {
    // The library reads a bare number as UNIX SECONDS. Handing it milliseconds
    // draws every candle somewhere around the year 57000, and the chart comes
    // back empty with no error anywhere.
    show()

    const first = (await drawn())[0]
    expect(first.time).toBe(Math.floor(Date.parse('2026-08-18T00:00:00Z') / 1000))
  })

  it('開高低收都傳過去，不是只有收盤價', async () => {
    show()

    expect((await drawn())[0]).toMatchObject({
      open: 100,
      high: 104,
      low: 99,
      close: 103,
    })
  })

  it('成交量也畫', async () => {
    show()

    await vi.waitFor(() => {
      expect(setVolumeData.mock.calls.at(-1)?.[0]?.length).toBeGreaterThan(0)
    })
  })

  it('中文公司名不要送出去問後端 —— 那一定是 422', async () => {
    show('台積電')

    await new Promise((r) => setTimeout(r, 50))
    // The bars endpoint specifically. The indicator catalogue is fetched too
    // and has nothing to do with the symbol, so 「api.get was never called」
    // would be asserting the wrong thing.
    expect(vi.mocked(api.get).mock.calls.filter(([path]) => path.includes('/bars'))).toHaveLength(0)
    expect(api.post).not.toHaveBeenCalled()
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

    await user.click(await screen.findByRole('button', { name: '週線' }))

    await vi.waitFor(() =>
      expect(vi.mocked(api.get).mock.calls.at(-1)?.[0]).toContain('timeframe=1wk'),
    )
  })

  it('現在選的是哪一個要看得出來', async () => {
    show()

    const day = await screen.findByRole('button', { name: '日線' })
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
    await drawn()

    view.unmount()

    expect(remove).toHaveBeenCalled()
  })
})

// --- the container is real, so the tests above are not testing a stub ------------

describe('元件本身', () => {
  it('畫布掛在畫面上，而且資料到了就不再蓋著「載入中」', async () => {
    show()

    // Waited on the DRAWN candles, not on the container: the container renders
    // immediately either way, and setData([]) now fires on the pending render,
    // so either alone would pass while the overlay still covered the chart.
    await drawn()

    const region = screen.getByRole('img', { name: /0050\.TW/ })
    expect(within(region.parentElement!).queryByText(/載入中/)).not.toBeInTheDocument()
  })
})

// --- the message that was there and could not be seen -----------------------
//
// The 404 from an out-of-date backend rendered its overlay and nobody saw it:
// lightweight-charts puts z-index up to 50 on its own elements, and an overlay
// with no z-index of its own paints UNDERNEATH the canvas. What the owner got
// was a black rectangle with no explanation -- the exact failure the overlay
// exists to prevent, produced by the overlay.

describe('錯誤訊息要真的看得到', () => {
  it('覆蓋層要疊在圖表上面，不是被圖表蓋住', async () => {
    // lightweight-charts uses z-index up to 50, so anything less loses.
    failBars(new Error('boom'))
    show()

    const alert = await screen.findByRole('alert')
    const overlay = alert.parentElement!
    expect(overlay.className).toMatch(/z-\[?\d/)
  })

  it('後端沒有這個端點時，說的是「線上的後端比較舊」而不是「稍後再試」', async () => {
    // A 404 is not a transient outage. It means the deployed backend is older
    // than the page asking it, and 「稍後重新整理」 would have somebody waiting
    // for something that will never happen on its own.
    //
    // 「去按 Manual Deploy」 was the old wording, and it is now wrong twice
    // over: deploys are automatic (CI calls the deploy hook once every job is
    // green), and the button it named belongs to one hosting company. What
    // somebody can actually act on is the CI run.
    failBars(new ApiError(404, 'Not Found'))
    show()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/線上的後端比這個畫面舊/)
    expect(alert).not.toHaveTextContent(/Manual Deploy/)
  })
})

// --- the canvas kept another company's candles -------------------------------
//
// Reported with a screenshot: the chart showed 0050.TW's candles under a
// message reading 「查不到「AAPL」的歷史資料」. The data effect returns early for
// pending, empty AND error (`if (!series || !bars?.length) return`), so the
// canvas holds the last successful non-empty answer regardless of which symbol
// is selected -- and the overlay is only 80% opaque, so the old candles show
// through it.
//
// That is worse than a blank chart. A blank one is obviously broken; this one
// is a plausible, well-formed, wrong chart with a warning somebody will read as
// spurious. It is the same failure the backend refuses symbols to avoid: 「would
// come back with a Japanese company's price history and draw it convincingly」.

describe('換代號的時候', () => {
  it('抓不到資料就把舊的 K 棒清掉，不要留著別檔股票的圖', async () => {
    vi.mocked(api.get).mockResolvedValue({ ...BARS, symbol: '0050.TW' } as never)
    const { rerender } = render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <PriceChart symbol="0050.TW" />
      </QueryClientProvider>,
    )
    await drawn()
    setData.mockClear()

    vi.mocked(api.get).mockResolvedValue({ symbol: 'AAPL', timeframe: '1d', bars: [] } as never)
    rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <PriceChart symbol="AAPL" />
      </QueryClientProvider>,
    )

    await vi.waitFor(() => expect(setData).toHaveBeenCalledWith([]))
  })

  it('抓失敗也一樣要清掉', async () => {
    vi.mocked(api.get).mockResolvedValue(BARS as never)
    show()
    await drawn()
    setData.mockClear()

    vi.mocked(api.get).mockRejectedValue(new Error('boom'))
    show('MSFT')

    await vi.waitFor(() => expect(setData).toHaveBeenCalledWith([]))
  })
})

// --- 「could not ask」 and 「there is nothing」 are different sentences ----------

describe('分清楚是抓不到還是真的沒有', () => {
  it('抓取失敗要說是暫時的、可以重試', async () => {
    // One clears on its own and one never will. Showing the permanent message
    // for the transient case is how a stock with fifty years of candles reads
    // as delisted.
    vi.mocked(api.get).mockResolvedValue({
      symbol: 'AAPL',
      timeframe: '1d',
      bars: [],
      fetch_failed: true,
    } as never)
    show()

    expect(await screen.findByRole('alert')).toHaveTextContent(/暫時|稍後|重試/)
  })

  it('真的沒有歷史才說沒有歷史', async () => {
    vi.mocked(api.get).mockResolvedValue({
      symbol: '9999.TW',
      timeframe: '1d',
      bars: [],
      fetch_failed: false,
    } as never)
    show('9999.TW')

    expect(await screen.findByText(/查不到.*歷史/)).toBeInTheDocument()
  })
})


// --- indicators, computed by the code the strategies use ---------------------------

/**
 * The owner's words: the chart had no indicators to choose from, and 「重點就是
 * 要那些指標才有辦法下策略跟回測」.
 *
 * THE LINE IS NOT COMPUTED HERE. Every value comes from the server, from
 * `spec.fn` -- the very function object the strategy sandbox hands to user
 * code. A moving average in TypeScript would be a second implementation of the
 * same idea, and the day the two disagreed the chart would be a picture of
 * something that is not happening. That is worse than having no indicators.
 */

const SMA_SERIES = {
  symbol: '0050.TW',
  timeframe: '1d',
  series: [
    {
      name: 'sma',
      key: '',
      pane: 'price',
      scale: 'sma',
      points: [{ time: '2026-08-19T00:00:00Z', value: 104 }],
    },
  ],
}

const RSI_SERIES = {
  symbol: '0050.TW',
  timeframe: '1d',
  series: [
    {
      name: 'rsi',
      key: '',
      pane: 'own',
      scale: 'rsi',
      points: [{ time: '2026-08-19T00:00:00Z', value: 62 }],
    },
  ],
}

function withIndicators(selected: { name: string; params: Record<string, number> }[]) {
  window.localStorage.setItem('chart-indicators', JSON.stringify(selected))
}

async function linesDrawn(count: number): Promise<DrawnLine[]> {
  await vi.waitFor(() => expect(lines.filter((line) => line.data.length > 0)).toHaveLength(count))
  return lines.filter((line) => line.data.length > 0)
}

describe('指標畫得出來，而且跟策略算的是同一份', () => {
  it('改參數的時候不要每打一個字就打一次後端', async () => {
    // This endpoint runs in the same process as the market loop, on a free
    // dyno. Typing 「120」 into a period box is three keystrokes; unthrottled
    // that is three full indicator computations over 300 candles, and the
    // first two are answers nobody will ever see.
    withIndicators([{ name: 'sma', params: { period: 20 } }])
    vi.mocked(api.post).mockResolvedValue(SMA_SERIES as never)
    show()

    await vi.waitFor(() => expect(api.post).toHaveBeenCalledTimes(1))
    const field = await screen.findByLabelText('sma period')
    await userEvent.clear(field)
    await userEvent.type(field, '120')

    // Settled, then exactly one more request -- for the number that was
    // actually landed on.
    await vi.waitFor(
      () => {
        expect(api.post).toHaveBeenCalledTimes(2)
        expect(vi.mocked(api.post).mock.calls[1][1]).toMatchObject({
          indicators: [{ name: 'sma', params: { period: 120 } }],
        })
      },
      { timeout: 3000 },
    )
  })

  it('沒有選指標就不要多打一次後端', async () => {
    // A free-tier dyno and a rate-limited quote provider. A request whose
    // answer is already known to be empty should not leave the page.
    show()

    await drawn()
    expect(api.post).not.toHaveBeenCalled()
  })

  it('選了就跟 K 棒同一個代號、同一個週期去要', async () => {
    // A moving average computed over daily candles and drawn on a weekly chart
    // is a wrong chart that looks right.
    withIndicators([{ name: 'sma', params: { period: 20 } }])
    vi.mocked(api.post).mockResolvedValue(SMA_SERIES as never)
    show()

    await vi.waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(vi.mocked(api.post).mock.calls[0][0]).toBe('/api/market/indicators')
    expect(vi.mocked(api.post).mock.calls[0][1]).toMatchObject({
      symbol: '0050.TW',
      timeframe: '1d',
      indicators: [{ name: 'sma', params: { period: 20 } }],
    })
  })

  it('均線跟 K 棒共用價格軸', async () => {
    withIndicators([{ name: 'sma', params: { period: 20 } }])
    vi.mocked(api.post).mockResolvedValue(SMA_SERIES as never)
    show()

    const [line] = await linesDrawn(1)
    expect(line.pane ?? 0).toBe(0)
  })

  it('RSI 這種另外開一格，不能壓在價格軸上', async () => {
    // 0-100 against candles in the hundreds is survivable; OBV at +-7.6e7 is
    // not -- the candles become a flat line at the bottom. Same mistake, and
    // the server is the one that decides which axis.
    withIndicators([{ name: 'rsi', params: { period: 14 } }])
    vi.mocked(api.post).mockResolvedValue(RSI_SERIES as never)
    show()

    const [line] = await linesDrawn(1)
    expect(line.pane).toBeGreaterThan(0)
  })

  it('後端說在哪一格就在哪一格，前端不自己判斷', async () => {
    // The same indicator name, moved by the server. If this page had a rule of
    // its own, this would still land on the price axis.
    withIndicators([{ name: 'sma', params: {} }])
    vi.mocked(api.post).mockResolvedValue({
      ...SMA_SERIES,
      series: [{ ...SMA_SERIES.series[0], pane: 'own' }],
    } as never)
    show()

    const [line] = await linesDrawn(1)
    expect(line.pane).toBeGreaterThan(0)
  })

  it('一次三條線的指標，三條都畫，而且擠在同一格', async () => {
    // macd, signal and histogram are one reading. Split across three panes
    // they cannot be compared, which is the only thing anybody reads them for.
    withIndicators([{ name: 'macd', params: {} }])
    vi.mocked(api.post).mockResolvedValue({
      symbol: '0050.TW',
      timeframe: '1d',
      series: ['macd', 'signal', 'histogram'].map((key) => ({
        name: 'macd',
        key,
        pane: 'own',
        scale: 'macd',
        points: [{ time: '2026-08-19T00:00:00Z', value: 1 }],
      })),
    } as never)
    show()

    const drawnLines = await linesDrawn(3)
    expect(new Set(drawnLines.map((line) => line.pane)).size).toBe(1)
    // ...and that one pane is NOT the price axis. Without this the assertion
    // above is satisfied by all three landing on pane 0, which is the exact
    // bug it is meant to catch.
    expect(drawnLines[0].pane).toBeGreaterThan(0)
  })

  it('畫上去的值就是後端算出來的值，前端不改它', async () => {
    // The whole design rests on this: the number on the chart is the number a
    // strategy would trade on. A test that only checks 「a line was drawn」
    // would stay green if this page rounded, scaled or re-based it.
    withIndicators([{ name: 'sma', params: {} }])
    vi.mocked(api.post).mockResolvedValue(SMA_SERIES as never)
    show()

    const [line] = await linesDrawn(1)
    expect((line.data[0] as { value: number }).value).toBe(104)
  })

  it('同一格裡尺度差太多的兩條線，各用各的軸', async () => {
    // bollinger_bands' bandwidth runs 4.5-25 and percent_b runs -0.2-1.2.
    // Sharing one axis makes percent_b a flat line on the floor -- the same
    // failure the pane map exists to prevent, one magnitude smaller. The
    // SERVER says which outputs may be measured against each other; this page
    // does not decide it.
    withIndicators([{ name: 'bollinger_bands', params: {} }])
    vi.mocked(api.post).mockResolvedValue({
      symbol: '0050.TW',
      timeframe: '1d',
      series: [
        {
          name: 'bollinger_bands',
          key: 'bandwidth',
          pane: 'own',
          scale: 'bollinger_bands',
          points: [{ time: '2026-08-19T00:00:00Z', value: 18 }],
        },
        {
          name: 'bollinger_bands',
          key: 'percent_b',
          pane: 'own',
          scale: 'bollinger_bands:percent_b',
          points: [{ time: '2026-08-19T00:00:00Z', value: 0.4 }],
        },
      ],
    } as never)
    show()

    const drawnLines = await linesDrawn(2)
    // One strip, two axes.
    expect(new Set(drawnLines.map((line) => line.pane)).size).toBe(1)
    expect(drawnLines[0].options.priceScaleId).not.toBe(drawnLines[1].options.priceScaleId)
  })

  it('本來就該互相比較的線，共用一個軸', async () => {
    // macd against its own signal line is the entire point of macd. Splitting
    // them onto separate axes would destroy the comparison.
    withIndicators([{ name: 'macd', params: {} }])
    vi.mocked(api.post).mockResolvedValue({
      symbol: '0050.TW',
      timeframe: '1d',
      series: ['macd', 'signal', 'histogram'].map((key) => ({
        name: 'macd',
        key,
        pane: 'own',
        scale: 'macd',
        points: [{ time: '2026-08-19T00:00:00Z', value: 1 }],
      })),
    } as never)
    show()

    const drawnLines = await linesDrawn(3)
    expect(new Set(drawnLines.map((line) => line.options.priceScaleId)).size).toBe(1)
  })

  it('時間用秒，跟 K 棒同一套', async () => {
    withIndicators([{ name: 'sma', params: {} }])
    vi.mocked(api.post).mockResolvedValue(SMA_SERIES as never)
    show()

    const [line] = await linesDrawn(1)
    expect((line.data[0] as { time: number }).time).toBe(
      Math.floor(Date.parse('2026-08-19T00:00:00Z') / 1000),
    )
  })

  it('後端沒回指標就不要留著上一次的線', async () => {
    // The same bug that put another company's candles under a 「查不到 AAPL」
    // message: a stale line left on the canvas is a plausible, well-formed,
    // wrong chart.
    //
    // IT HAS TO DRAW ONE FIRST. Mocking an empty answer from the very first
    // call makes this pass before the component has mounted -- green, and
    // proving nothing about removal.
    withIndicators([{ name: 'sma', params: {} }])
    vi.mocked(api.post).mockResolvedValue(SMA_SERIES as never)
    const { rerender } = show()
    await linesDrawn(1)

    // Now the same page asks again -- a different symbol, a refetch -- and the
    // answer has no series in it.
    vi.mocked(api.post).mockResolvedValue({ symbol: 'AAPL', timeframe: '1d', series: [] } as never)
    rerender('AAPL')

    await vi.waitFor(() => expect(lines.filter((line) => line.data.length > 0)).toHaveLength(0))
  })

  it('加了指標之後，K 棒那一格不會被壓扁', async () => {
    // Panes divide the chart's height by stretch factor, and the default gives
    // the price pane twice an indicator's. Three oscillators and the candles
    // are down to a third of what they were -- on a laptop that is the chart
    // becoming unreadable as a reward for using the feature.
    withIndicators([{ name: 'rsi', params: {} }])
    vi.mocked(api.post).mockResolvedValue(RSI_SERIES as never)
    show()

    await linesDrawn(1)
    await vi.waitFor(() => expect(stretch.length).toBe(2))
    expect(stretch[0]).toBeGreaterThan(stretch[1] * 3)
  })

  it('算不出來就說一聲，不要讓 K 棒跟著消失', async () => {
    // A bad period is a 422 about the indicator, not about the price history.
    // Blanking the candles over it would lose the part that still works.
    withIndicators([{ name: 'sma', params: { period: 9999 } }])
    vi.mocked(api.post).mockRejectedValue(new ApiError(422, 'sma 算不出來'))
    show()

    expect(await drawn()).toHaveLength(2)
    // Specifically an alert, and specifically the server's own sentence. A
    // loose /指標/ match is satisfied by the 「要畫線、疊指標的話」 footer that
    // was already on this page, which is a green test for a broken feature.
    expect(await screen.findByRole('alert')).toHaveTextContent('sma 算不出來')
  })
})


// --- the candle sizes the owner asked for -------------------------------------------

/**
 * 「K線的單位不夠細，通常還要有小時 (譬如4hr / 12hr)，分鐘 (1/5/15/30分)」.
 *
 * The buttons come from the SERVER, per data source, because the set differs:
 * Yahoo refuses 12h outright and Binance serves it. A list hard-coded here
 * would offer a stock user a candle that answers 「暫時抓不到…可能是被限流
 * 了」 -- a transient sentence for a permanent condition.
 */
describe('K 棒週期', () => {
  it('分鐘、小時、日週月都選得到，不再只有三個', async () => {
    show()

    expect(await screen.findByRole('button', { name: '15 分線' })).toBeInTheDocument()
    for (const label of ['1 分線', '30 分線', '4 小時線', '日線', '月線']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
    }
  })

  it('按鈕是後端給的，前端不自己列一份', async () => {
    show()

    await screen.findByRole('button', { name: '4 小時線' })
    expect(vi.mocked(api.get).mock.calls.some(([p]) => p.includes('/timeframes'))).toBe(true)
  })

  it('看美股時不出現 12 小時線 —— Yahoo 根本不提供', async () => {
    show()

    await screen.findByRole('button', { name: '4 小時線' })
    expect(screen.queryByRole('button', { name: '12 小時線' })).not.toBeInTheDocument()
  })

  it('看加密貨幣時才出現 12 小時線', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <PriceChart symbol="BTCUSDT" dataSource="binance" />
      </QueryClientProvider>,
    )

    expect(await screen.findByRole('button', { name: '12 小時線' })).toBeInTheDocument()
  })

  it('選了週期就用那個週期去要資料', async () => {
    show()

    await userEvent.click(await screen.findByRole('button', { name: '4 小時線' }))

    await vi.waitFor(() =>
      expect(
        vi.mocked(api.get).mock.calls.some(([p]) => p.includes('timeframe=4h')),
      ).toBe(true),
    )
  })

  it('從加密貨幣的 12 小時線換到美股，不會默默改畫別的週期', async () => {
    // Silently falling back to 日線 while the pressed button still reads 12
    // 小時線 is a chart that disagrees with its own label -- the plausible,
    // well-formed, WRONG chart this codebase treats as worse than a blank one.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const view = render(
      <QueryClientProvider client={client}>
        <PriceChart symbol="BTCUSDT" dataSource="binance" />
      </QueryClientProvider>,
    )
    await userEvent.click(await screen.findByRole('button', { name: '12 小時線' }))

    view.rerender(
      <QueryClientProvider client={client}>
        <PriceChart symbol="AAPL" dataSource="yfinance" />
      </QueryClientProvider>,
    )

    // It moved to a candle this source has...
    await vi.waitFor(() =>
      expect(screen.getByRole('button', { name: '日線' })).toHaveAttribute('aria-pressed', 'true'),
    )
    // ...and said so, rather than leaving the reader to notice.
    expect(screen.getByRole('status')).toHaveTextContent(/12 小時線/)
  })
})


// --- 往前拉 ---------------------------------------------------------------------

/**
 * 「圖表只讀取當下可顯示的頁面，往前拉以往數據不會在讀取資料，只看到空的畫面。」
 *
 * 那是真的，而且是兩件事疊在一起：
 *
 * 1. 這張圖只問過一次後端，深度固定 300 根，從此不再問。
 * 2. lightweight-charts 預設**允許捲出資料範圍之外**，所以拉過最舊那一根之後
 *    畫布還在動，只是上面什麼都沒有。
 *
 * 第二點讓第一點看起來像壞掉而不像「沒有更多了」。兩件都修：往左拉到最舊那一根
 * 就再問一次更深的，問到來源給不出來為止；而任何時候都不能捲進空白。
 *
 * 這裡沒有真的拖曳可以做——圖是 canvas，沒有 DOM。對這個元件來說，「使用者往前
 * 拉」的定義就是 lightweight-charts 回呼它說可見範圍到哪裡了，所以測試呼叫的
 * 就是那個回呼。
 */

const DEPTH = 300

function deepBars(count: number, from = DEPTH) {
  return {
    symbol: '0050.TW',
    timeframe: '1d',
    bars: Array.from({ length: count }, (_, i) => ({
      // 往回數，所以最舊的那一根會隨著問得更深而更早。
      time: new Date(Date.UTC(2026, 0, 1) - (from - i) * 86_400_000).toISOString(),
      open: 100,
      high: 104,
      low: 99,
      close: 103,
      volume: 1200,
    })),
  }
}

/** 後端：問幾根就給幾根，最多到 `available` 根。 */
function sourceWith(available: number) {
  vi.mocked(api.get).mockImplementation((path: string) => {
    if (path.includes('/indicators/available')) return Promise.resolve(CATALOGUE) as never
    if (path.includes('/timeframes')) return Promise.resolve(TIMEFRAMES) as never
    const asked = Number(new URL(path, 'http://x').searchParams.get('limit') ?? DEPTH)
    return Promise.resolve(deepBars(Math.min(asked, available), asked)) as never
  })
}

/** 每一次問 K 棒時帶的深度，依序。 */
function depthsAsked(): number[] {
  return vi
    .mocked(api.get)
    .mock.calls.map(([path]) => path)
    .filter((path) => path.includes('/bars'))
    .map((path) => Number(new URL(path, 'http://x').searchParams.get('limit') ?? 0))
}

/** 使用者把畫面拉到最舊那一根。`to` 小於已載入的根數＝畫面裝不下全部。 */
async function panToOldest(to = 60) {
  await act(async () => {
    for (const handler of [...rangeHandlers]) handler({ from: 0, to })
  })
}

describe('往前拉要看得到更早的資料，不是一片空白', () => {
  it('捲不出資料範圍以外 —— 空白畫面本身就是那個 bug 的樣子', async () => {
    show()

    await drawn()
    expect(chartOptions.at(-1)?.timeScale).toMatchObject({ fixLeftEdge: true })
  })

  it('第一次只問預設的深度', async () => {
    sourceWith(10_000)
    show()

    await drawn()
    expect(depthsAsked()).toEqual([DEPTH])
  })

  it('拉到最舊那一根就再問一次，而且問得更深', async () => {
    sourceWith(10_000)
    show()
    await drawn()

    await panToOldest()

    await vi.waitFor(() => {
      expect(depthsAsked().at(-1)).toBeGreaterThan(DEPTH)
    })
    await vi.waitFor(async () => {
      expect((await drawn()).length).toBeGreaterThan(DEPTH)
    })
  })

  it('一載入就要拉得動 —— 初始視角把歷史留在畫面左外側', async () => {
    // 這是上一版真正壞掉的地方，而十條測試全綠、CI 全綠、部署成功都沒抓到。
    //
    // fitContent() 讓可見範圍等於「全部載入的 K 棒」。再配上 fixLeftEdge（那是
    // 用來擋住捲進空白的），使用者往左拉的時候函式庫會說「左邊沒有東西了」，
    // 於是**畫布紋風不動**——除非他先用滾輪放大。沒有人會猜到要先放大。
    //
    // 所以初始視角改成只顯示最近的那幾根，歷史留在畫面左外側：一開始就拉得動，
    // 拉到最舊那一根就去要更早的。
    sourceWith(10_000)
    show()
    await drawn()

    await vi.waitFor(() => {
      expect(setVisibleLogicalRange).toHaveBeenCalled()
    })
    const range = setVisibleLogicalRange.mock.calls.at(-1)![0] as { from: number; to: number }
    expect(range.to).toBe(DEPTH - 1)
    expect(range.from).toBeGreaterThan(0)
    expect(range.to - range.from).toBeLessThan(DEPTH - 1)
    // 而且不是 fitContent —— 那正是把畫布釘死的那一個。
    expect(fitContent).not.toHaveBeenCalled()
  })

  it('往左拉不需要先放大 —— from 靠近 0 就去要，不管畫面裝了幾根', async () => {
    // 上一版多了一道「畫面已裝下全部就不要問」的閘門，本來是防止 fitContent 自
    // 己觸發無限加深。初始視角改掉之後那道閘門不再需要，而它會擋掉真正的手勢。
    sourceWith(10_000)
    show()
    await drawn()

    await act(async () => {
      for (const handler of [...rangeHandlers]) handler({ from: 0, to: DEPTH - 1 })
    })

    await vi.waitFor(() => {
      expect(depthsAsked().at(-1)).toBeGreaterThan(DEPTH)
    })
  })

  it('上游回的比問的少，就是沒有更早的了，不要一直問', async () => {
    // 一支剛上市的股票就是這樣：問 300 根拿回 120 根。再問 600 根還是 120 根，
    // 而每一次都是一趟被限流的上游請求。
    sourceWith(120)
    show()
    await drawn()

    await panToOldest()
    await panToOldest()

    await new Promise((r) => setTimeout(r, 50))
    expect(depthsAsked()).toEqual([DEPTH])
  })

  it('問到來源宣告的上限就停', async () => {
    // 日線在 TIMEFRAMES 裡宣告 10000 根。問超過那個數字，後端會回 422，而 422
    // 在畫面上的樣子就是往前拉之後那一片空白。
    sourceWith(1_000_000)
    show()
    await drawn()

    for (let i = 0; i < 12; i += 1) await panToOldest()
    await new Promise((r) => setTimeout(r, 50))

    expect(Math.max(...depthsAsked())).toBeLessThanOrEqual(10_000)
  })

  it('載入更深的時候，畫面不會先被清空', async () => {
    // 深度是 query key 的一部分，所以問得更深就是一個新的 query。沒有保留舊資
    // 料的話，每拉一次畫面就閃一下「載入中…」——那正是使用者說的空白畫面，只是
    // 這次是我們自己畫的。
    sourceWith(10_000)
    show()
    await drawn()
    const before = setData.mock.calls.length

    await panToOldest()

    await vi.waitFor(() => {
      expect(depthsAsked().length).toBeGreaterThan(1)
    })
    const since = setData.mock.calls.slice(before).map((call) => call[0])
    expect(since.some((data) => (data as unknown[]).length === 0)).toBe(false)
    expect(screen.queryByText(/載入中…/)).not.toBeInTheDocument()
  })

  it('載入更深之後不會跳回全圖 —— 使用者原本在看的位置要留著', async () => {
    sourceWith(10_000)
    show()
    await drawn()
    const fitted = fitContent.mock.calls.length

    await panToOldest()
    // 等到更深的那一批真的畫上去為止。只等請求送出去的話，這一條會在畫圖之前
    // 就通過，而「有沒有跳回全圖」正是畫圖那一刻才決定的事。
    await vi.waitFor(async () => {
      expect((await drawn()).length).toBeGreaterThan(DEPTH)
    })

    expect(fitContent.mock.calls.length).toBe(fitted)
    expect(setVisibleRange).toHaveBeenCalled()
  })

  it('換代號會回到預設深度', async () => {
    sourceWith(10_000)
    const { rerender } = show()
    await drawn()
    await panToOldest()
    await vi.waitFor(() => {
      expect(depthsAsked().length).toBeGreaterThan(1)
    })

    rerender('2330.TW')

    await vi.waitFor(() => {
      const last = vi
        .mocked(api.get)
        .mock.calls.map(([path]) => path)
        .filter((path) => path.includes('2330.TW'))
        .at(-1)
      expect(last).toBeTruthy()
      expect(Number(new URL(last!, 'http://x').searchParams.get('limit'))).toBe(DEPTH)
    })
  })

  it('指標跟著同一個深度，不然線會在半路斷掉', async () => {
    // K 棒往回長到 600 根而指標只算了 300 根，畫出來會像「這個指標從這裡才開始
    // 存在」。那不是真的，而且沒有任何東西會說它不是真的。
    sourceWith(10_000)
    withIndicators([{ name: 'sma', params: { period: 20 } }])
    show()
    await drawn()

    await panToOldest()

    await vi.waitFor(() => {
      const bars = depthsAsked().at(-1)!
      expect(bars).toBeGreaterThan(DEPTH)
      const posted = vi.mocked(api.post).mock.calls.at(-1)?.[1] as { limit?: number }
      expect(posted?.limit).toBe(bars)
    })
  })
})
