import { useQuery } from '@tanstack/react-query'
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LogicalRange,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../lib/api'
import { INITIAL_VISIBLE_BARS, LOAD_MORE_WITHIN_BARS } from '../lib/chartViewport'
import { looksUnpriceable } from '../lib/symbol'
import { useTimeframes } from '../lib/timeframes'
import { tradingViewSymbol } from '../lib/tradingView'
import type { BarsResponse, DataSource, IndicatorsResponse } from '../lib/types'
import { ChartIndicators, type SelectedIndicator } from './ChartIndicators'

/**
 * The chart, drawn from data this app already has.
 *
 * TradingView's free embedded widget answers 「此商品僅在 TradingView 上可用」 for
 * Taiwanese symbols -- its own words for 「the symbol is real, but this widget
 * is not licensed to show its data」. The symbol was never wrong: that dialog's
 * own header reads TWSE:0050, which is exactly what lib/tradingView.ts
 * produces. It is a data licensing restriction and no amount of symbol
 * correctness reaches it.
 *
 * Meanwhile this backend already fetches OHLC for those symbols -- it is how
 * every price, every alert and every backtest works. The data was never
 * missing either. What was missing was a renderer, and lightweight-charts is
 * TradingView's own open-source one: a canvas drawing library with no data,
 * no account and no symbol restrictions attached.
 *
 * THERE IS NO 「fall back when the widget fails」. The widget renders inside an
 * iframe and this page cannot see it fail, so that fallback is not
 * implementable -- it has to be one or the other. This is the one that works
 * for every symbol the app can price.
 *
 * INDICATORS ARE COMPUTED ON THE SERVER, by `spec.fn` -- the very function
 * object the strategy sandbox hands to user code. Not 「the same formula」: the
 * same object. A moving average implemented in TypeScript here would be a
 * second implementation of the same idea, and the first day the two disagreed
 * this chart would be a picture of something that is not happening, which is
 * worse than drawing no indicators at all.
 *
 * WHAT IS LOST, said plainly: drawing tools. The link at the bottom goes to
 * the real thing for anyone who wants them.
 */

const CHART_HEIGHT_PX = 460

// Reads as a price chart at a glance in this app's dark theme, and keeps the
// Taiwanese convention: red is up, green is down. Getting that backwards on a
// TW chart is a wrong chart that looks right.
const UP = '#ef4444'
const DOWN = '#22c55e'

// How long a parameter box has to sit still before the chart asks the server
// again. This endpoint runs in the same process as the market loop on a free
// dyno, and typing 「120」 into a period box is three keystrokes -- three full
// computations over 300 candles, two of which nobody will ever see.
//
// Only PARAMETERS wait. Adding or removing an indicator is a deliberate click
// and goes immediately; making that feel laggy to save a request nobody was
// going to send anyway would be paying in the wrong currency.
const PARAM_SETTLE_MS = 400

// HOW DEEP THE FIRST ASK GOES, and how the chart digs when somebody scrolls
// back past it.
//
// 300 is the backend's DEFAULT_BAR_LIMIT, and matching it is not decoration:
// the market loop asks for exactly that depth, so the chart's first request
// shares the loop's cache entry and costs the rate-limited provider nothing.
//
// It used to be the ONLY depth this chart ever asked for. Scrolling further
// back than 300 candles reached blank canvas and stayed blank, because nothing
// went and got more -- 「往前拉以往數據不會在讀取資料，只看到空的畫面」.
const DEFAULT_DEPTH = 300

// Doubling, not a fixed step. The depth somebody wants is unknown and the cost
// of asking is one request either way (the provider clamps intraday to the
// source's wall regardless of depth), so a fixed +300 would mean twenty
// requests to reach five years while doubling reaches it in four.
const DEEPEN_BY = 2

// 這兩個數字住在 lib/chartViewport.ts：開真的瀏覽器的那一關在 node 專案下跑，
// 讀不到這個檔案（JSX ＋ DOM 型別），而抄一份過去就會漂。


// Each extra pane costs height. Enough to read a shape in, not so much that
// four of them push the candles off a laptop screen.
const INDICATOR_PANE_HEIGHT_PX = 110

// Assigned in order, and stable for as long as the selection is: an indicator
// that changes colour when a different one is removed is unreadable. Picked to
// stay apart from the red/green candles.
const LINE_COLOURS = [
  '#38bdf8',
  '#f59e0b',
  '#a78bfa',
  '#f472b6',
  '#4ade80',
  '#facc15',
  '#22d3ee',
  '#fb923c',
]

/** Whether a cached answer belongs to the chart currently on screen.
 *
 * Depth is part of the query key, so asking for more history is a NEW query
 * and react-query has no data for it -- the canvas would blank and the
 * 「載入中…」 overlay would flash on every scroll leftwards, which is the very
 * thing being fixed. Keeping the previous answer while the deeper one arrives
 * fixes that, but ONLY for the same chart: kept across a change of symbol it
 * would leave another company's candles under this company's name, which is a
 * bug this component has already had once and is why setData([]) is called at
 * all.
 */
function sameChart(
  key: readonly unknown[] | undefined,
  symbol: string,
  timeframe: string,
  dataSource?: DataSource,
): boolean {
  return key?.[1] === symbol && key?.[2] === timeframe && key?.[3] === dataSource
}

export function PriceChart({ symbol, dataSource }: { symbol: string; dataSource?: DataSource }) {
  const [timeframe, setTimeframe] = useState('1d')
  // Which candle sizes THIS symbol's source actually serves. Asked of the
  // server rather than listed here: Yahoo refuses 12h outright while Binance
  // serves it, so a list in this file would offer a stock user a button whose
  // only possible answer is 「暫時抓不到…可能是被限流了」 -- a transient
  // sentence for a permanent condition.
  const timeframes = useTimeframes(dataSource)
  // Named when a switch of symbol forced a switch of candle, so the chart never
  // silently draws one interval under a button that reads another.
  const [movedFrom, setMovedFrom] = useState<string | null>(null)

  // The NAME of the candle currently selected, remembered while it is still
  // resolvable. By the time a switch of source makes it unsupported, the
  // option list is already the new source's and the label is gone -- so the
  // notice would have to say 「12h」 to somebody who reads 十二小時線.
  const currentLabel = useRef('日線')
  useEffect(() => {
    const found = timeframes.options.find((option) => option.value === timeframe)
    if (found) currentLabel.current = found.label
  }, [timeframe, timeframes.options])

  useEffect(() => {
    if (timeframes.isPending || timeframes.supports(timeframe)) return
    setMovedFrom(currentLabel.current)
    // Daily is the one interval every source serves.
    setTimeframe(timeframes.options.some((o) => o.value === '1d') ? '1d' : timeframes.options[0].value)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataSource, timeframes.isPending])
  // HOW MANY CANDLES ARE CURRENTLY BEING ASKED FOR. Grows when the view
  // reaches the oldest one; back to the default whenever the chart becomes a
  // different chart.
  const [depth, setDepth] = useState(DEFAULT_DEPTH)

  // The ceiling, asked of the server rather than written here. base.py measured
  // what each source actually parts with per interval -- 1m stops at seven
  // days, hourly at two years -- and past it Yahoo returns an EMPTY frame
  // rather than a shorter one. A number in this file would drift from that one
  // and the drift would show up as a chart that goes blank at a depth the app
  // said was available.
  const depthCap = Math.max(
    timeframes.options.find((option) => option.value === timeframe)?.max_bars ?? 0,
    DEFAULT_DEPTH,
  )

  // The depth of the last answer AND how many candles came back in it. Both,
  // because either alone cannot say whether there is more: asking for 600 and
  // receiving 320 means the source has 320, and asking again is a wasted round
  // trip against a rate limiter that this product cannot afford to lose.
  const answered = useRef({ depth: 0, count: 0 })
  // How many candles are on the canvas right now, read by the scroll handler.
  const loadedRef = useRef(0)
  // Which chart the canvas is currently showing, so 「more history arrived」 can
  // be told apart from 「this is a different symbol now」.
  const viewRef = useRef('')

  useEffect(() => {
    setDepth(DEFAULT_DEPTH)
    answered.current = { depth: 0, count: 0 }
  }, [symbol, timeframe, dataSource])

  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<{ candles: ReturnType<IChartApi['addSeries']> ; volume: ReturnType<IChartApi['addSeries']> } | null>(null)

  // Refused here as well as on the server. Asking the backend about 「台積電」
  // can only ever come back 422, and a request whose answer is already known
  // is a request that should not leave the page.
  const unpriceable = looksUnpriceable(symbol)

  const [renderFailed, setRenderFailed] = useState(false)
  // Restored from the last visit, so somebody's moving average is still there
  // after a reload. Read once, in the initialiser: reading it on every render
  // would fight the state it is seeding.
  const [indicators, setIndicators] = useState<SelectedIndicator[]>(() =>
    ChartIndicators.restore(),
  )
  const indicatorSeriesRef = useRef<ISeriesApi<'Line'>[]>([])

  // What the chart has actually asked the server for, as opposed to what the
  // boxes currently say. See PARAM_SETTLE_MS.
  const [settled, setSettled] = useState<SelectedIndicator[]>(indicators)
  const settledNamesRef = useRef(indicators.map((entry) => entry.name).join(','))

  useEffect(() => {
    const names = indicators.map((entry) => entry.name).join(',')
    if (names !== settledNamesRef.current) {
      settledNamesRef.current = names
      setSettled(indicators)
      return
    }
    const timer = window.setTimeout(() => setSettled(indicators), PARAM_SETTLE_MS)
    return () => window.clearTimeout(timer)
  }, [indicators])

  const query = useQuery({
    queryKey: ['bars', symbol, timeframe, dataSource, depth],
    enabled: !unpriceable,
    // See sameChart(): keeps the shallower candles on screen while the deeper
    // answer is on its way, and only for this same chart.
    placeholderData: (previous, previousQuery) =>
      sameChart(previousQuery?.queryKey, symbol, timeframe, dataSource) ? previous : undefined,
    // No retries. The failures this actually sees are a 404 from a backend
    // older than this page and a 422 from a symbol it will not price -- both
    // permanent, and retrying them means seven seconds of a blank chart before
    // anything is said.
    retry: false,
    queryFn: () =>
      api.get<BarsResponse>(
        `/api/market/bars?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}` +
          `&limit=${depth}` +
          (dataSource ? `&data_source=${dataSource}` : ''),
      ),
  })

  const indicatorQuery = useQuery({
    // Keyed on the params too: changing a period from 20 to 60 has to refetch,
    // and keying only on the names would serve the 20 forever.
    queryKey: ['indicators', symbol, timeframe, dataSource, depth, JSON.stringify(settled)],
    // Same as the candles: the lines must not vanish while a deeper window is
    // being fetched.
    placeholderData: (previous, previousQuery) =>
      sameChart(previousQuery?.queryKey, symbol, timeframe, dataSource) ? previous : undefined,
    // A free-tier dyno and a rate-limited quote provider. With nothing picked
    // there is nothing to ask for, and the request should not leave the page.
    enabled: !unpriceable && settled.length > 0,
    // Same reasoning as the bars query: the failures this sees are a 422 about
    // a parameter and a 404 from an older backend, both permanent.
    retry: false,
    queryFn: () =>
      api.post<IndicatorsResponse>('/api/market/indicators', {
        symbol,
        timeframe,
        // THE SAME DEPTH AS THE CANDLES, always. Candles reaching back three
        // years under a line that starts one year in draws 「這個指標從這裡才
        // 開始存在」, which is not true of the indicator and which nothing on
        // the screen contradicts.
        limit: depth,
        ...(dataSource ? { data_source: dataSource } : {}),
        indicators: settled,
      }),
  })

  // Created once and reused. Tearing the canvas down on every data change
  // would restart the zoom and scroll position under somebody's cursor.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    // Wrapped because this is the dashboard. A canvas that cannot be created
    // -- an old browser, a hardened environment, a library that throws on
    // something unexpected -- would otherwise unmount the entire page and take
    // the positions, the orders and the watchlist down with a chart.
    let chart: IChartApi
    try {
      chart = createChart(container, {
        height: CHART_HEIGHT_PX,
        layout: {
          background: { type: ColorType.Solid, color: '#020617' },
          textColor: '#94a3b8',
        },
        grid: {
          vertLines: { color: '#1e293b' },
          horzLines: { color: '#1e293b' },
        },
        rightPriceScale: { borderColor: '#334155' },
        timeScale: {
          borderColor: '#334155',
          timeVisible: false,
          // THE CANVAS MUST NOT SCROLL PAST THE OLDEST CANDLE. Left to its
          // default it scrolls into empty space forever, and empty space is
          // indistinguishable from 「the data failed to load」 -- which is
          // exactly how this was reported. With the edge fixed, reaching the
          // oldest candle either loads more (see the handler below) or simply
          // stops, and stopping reads as 「這就是全部了」.
          fixLeftEdge: true,
        },
      })
    } catch {
      setRenderFailed(true)
      return
    }

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      borderUpColor: UP,
      borderDownColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
    })
    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    })
    // Volume in the bottom fifth, so it reads as context rather than
    // competing with the price it is context for.
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })

    chartRef.current = chart
    seriesRef.current = { candles, volume }

    return () => {
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [])

  // Deciding whether to dig, kept in a ref so the subscription below can be
  // made once and still see the current depth. Re-subscribing on every render
  // would churn a listener the library holds onto.
  const deepenRef = useRef(() => {})
  useEffect(() => {
    deepenRef.current = () => {
      if (query.isFetching) return
      if (depth >= depthCap) return
      // Fewer came back than were asked for, so the source is out: a stock
      // listed six months ago has six months of candles and no amount of
      // asking produces a seventh. Asking anyway spends a request against the
      // rate limiter that every alert in this app shares.
      if (answered.current.depth > 0 && answered.current.count < answered.current.depth) return
      setDepth((current) => Math.min(depthCap, current * DEEPEN_BY))
    }
  })

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const scale = chart.timeScale()

    const onRange = (range: LogicalRange | null) => {
      if (!range) return
      // JUST THE LEFT EDGE. An earlier version also refused when the view
      // already held every loaded candle, to stop fitContent triggering a dig
      // on load -- but that condition is exactly true of the gesture this is
      // for, so it blocked the real thing to guard against a self-inflicted
      // one. The self-inflicted one is gone now: the opening view deliberately
      // leaves history off-screen (INITIAL_VISIBLE_BARS), so `from` starts well
      // clear of zero, and 「the source has no more」 is answered by what came
      // back rather than by how much is on screen.
      if (range.from > LOAD_MORE_WITHIN_BARS) return
      deepenRef.current()
    }

    scale.subscribeVisibleLogicalRangeChange(onRange)
    return () => scale.unsubscribeVisibleLogicalRangeChange(onRange)
  }, [])

  // What the last real answer cost and what it contained. Recorded off the
  // placeholder flag: while a deeper window is in flight `query.data` is still
  // the shallower one, and pairing THAT count with the NEW depth would read as
  // 「the source ran out」 and stop the digging one step in.
  useEffect(() => {
    if (query.isSuccess && !query.isPlaceholderData) {
      answered.current = { depth, count: query.data?.bars?.length ?? 0 }
    }
  }, [query.isSuccess, query.isPlaceholderData, query.data, depth])

  useEffect(() => {
    const series = seriesRef.current
    if (!series) return

    const bars = query.data?.bars
    if (!bars?.length) {
      // CLEARED, not left alone. Returning early here is what put another
      // company's candles under a 「查不到 AAPL 的歷史資料」 message: the canvas
      // held the last successful non-empty answer regardless of which symbol
      // was selected, and the overlay is only 80% opaque so the old candles
      // showed through it.
      //
      // A blank chart is obviously broken. A plausible, well-formed, WRONG
      // chart with a warning somebody reads as spurious is worse -- it is the
      // same failure the backend refuses ambiguous symbols to avoid.
      series.candles.setData([])
      series.volume.setData([])
      loadedRef.current = 0
      return
    }

    // SECONDS, not milliseconds. The library reads a bare number as a UNIX
    // second count; handed milliseconds it plots every candle somewhere around
    // the year 57000 and the chart comes back empty with no error anywhere.
    const at = (iso: string) => (Date.parse(iso) / 1000) as UTCTimestamp

    series.candles.setData(
      bars.map((bar) => ({
        time: at(bar.time),
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      })),
    )
    // WHERE THE READER IS LOOKING, saved before the data underneath them
    // changes. Prepending three years of candles renumbers every bar, so the
    // position has to be remembered as a span of TIME, not as bar indices.
    //
    // Only for the same chart, and only when there was already something to
    // hold a position in: a different symbol is a different picture and should
    // open showing all of itself.
    const view = `${symbol}|${timeframe}|${dataSource ?? ''}`
    const scale = chartRef.current?.timeScale()
    const holding = viewRef.current === view && loadedRef.current > 0 ? scale?.getVisibleRange() : null
    viewRef.current = view

    series.volume.setData(
      bars.map((bar) => ({
        time: at(bar.time),
        value: bar.volume,
        color: bar.close >= bar.open ? `${UP}55` : `${DOWN}55`,
      })),
    )
    loadedRef.current = bars.length

    if (holding) {
      try {
        scale?.setVisibleRange(holding)
      } catch {
        // The saved span does not land inside the new data -- nothing worth
        // breaking the chart over; the reader keeps whatever the library chose.
      }
    } else {
      // The most recent slice, with the rest of the history off-screen to the
      // left where it can be dragged into view. See INITIAL_VISIBLE_BARS.
      try {
        scale?.setVisibleLogicalRange({
          from: Math.max(0, bars.length - INITIAL_VISIBLE_BARS),
          to: bars.length - 1,
        })
      } catch {
        // A range the library will not take (one candle, an empty series).
        // Whatever it chose by itself is still a chart.
      }
    }
  }, [query.data, symbol, timeframe, dataSource])

  // The indicator lines. Torn down and rebuilt whenever the answer changes,
  // rather than updated in place: the SET of series changes with the selection
  // (macd is three lines, sma is one), and reconciling that by hand is how a
  // line for an indicator nobody has selected any more stays on the canvas.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    for (const series of indicatorSeriesRef.current) {
      // Wrapped: a series belonging to a chart that has already been removed
      // throws, and the cleanup is not worth unmounting the dashboard for.
      try {
        chart.removeSeries(series)
      } catch {
        /* already gone with the chart */
      }
    }
    indicatorSeriesRef.current = []
    // Panes are indexed, so they have to go from the back. Removing pane 1
    // first renumbers pane 2 to 1 and the second removal takes the wrong one.
    for (let index = chart.panes().length - 1; index >= 1; index -= 1) {
      try {
        chart.removePane(index)
      } catch {
        /* already gone */
      }
    }

    const series = indicatorQuery.data?.series
    if (!series?.length) {
      // Back to the plain height. Skipping this leaves the canvas as tall as
      // it was with three oscillators on it while the container div shrinks
      // back to CHART_HEIGHT_PX, and the candles are drawn outside the border
      // and clipped.
      chart.applyOptions({ height: CHART_HEIGHT_PX })
      return
    }

    // One pane per INDICATOR, not per output: macd, signal and histogram are a
    // single reading, and split across three strips they cannot be compared,
    // which is the only thing anybody reads them for.
    const panes = new Map<string, number>()
    // Which scale group already owns each pane's own labelled axis. The FIRST
    // group in a pane keeps the default scale so its numbers stay on screen --
    // an RSI with no 0/50/100 to read against is half an RSI. Any further
    // group in the same pane gets an axis of its own, unlabelled but
    // independent, which is what stops bollinger's percent_b (-0.2 to 1.2)
    // being flattened by its bandwidth (4.5 to 25).
    const primaryScale = new Map<number, string>()
    let colour = 0

    for (const entry of series) {
      let pane = 0
      if (entry.pane !== 'price') {
        const existing = panes.get(entry.name)
        if (existing !== undefined) {
          pane = existing
        } else {
          // The pane index the server's answer implies. Created by asking for
          // it: lightweight-charts adds the pane when a series names one that
          // does not exist yet.
          pane = panes.size + 1
          panes.set(entry.name, pane)
        }
      }

      // Price-pane series always share the candles' axis -- that is what
      // makes a moving average readable against them.
      let priceScaleId: string | undefined
      if (entry.pane !== 'price') {
        const owner = primaryScale.get(pane)
        if (owner === undefined) primaryScale.set(pane, entry.scale)
        else if (owner !== entry.scale) priceScaleId = entry.scale
      }

      const line = chart.addSeries(
        LineSeries,
        {
          color: LINE_COLOURS[colour % LINE_COLOURS.length],
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: entry.pane !== 'price',
          title: entry.key ? `${entry.name}.${entry.key}` : entry.name,
          ...(priceScaleId ? { priceScaleId } : {}),
        },
        pane,
      )
      colour += 1

      line.setData(
        // SECONDS, like the candles. A millisecond timestamp here plots the
        // line around the year 57000 -- off the visible range, so the line
        // just does not appear and nothing says why.
        entry.points.map((point) => ({
          time: (Date.parse(point.time) / 1000) as UTCTimestamp,
          value: point.value,
        })),
      )
      indicatorSeriesRef.current.push(line)
    }

    // Give the new strips somewhere to live. Without this they are carved out
    // of the candles' height and the price chart shrinks every time an
    // oscillator is added.
    chart.applyOptions({ height: CHART_HEIGHT_PX + panes.size * INDICATOR_PANE_HEIGHT_PX })

    // And divide that height the way it was asked for. Panes split by stretch
    // FACTOR, not by pixels, and the default gives the price pane only twice
    // an indicator's -- so three oscillators would leave the candles on a
    // third of the chart even after it grew. Expressed in the same pixel
    // numbers as the height above so the two cannot drift apart.
    const layout = chart.panes()
    layout[0]?.setStretchFactor(CHART_HEIGHT_PX)
    for (let index = 1; index < layout.length; index += 1) {
      layout[index]?.setStretchFactor(INDICATOR_PANE_HEIGHT_PX)
    }
  }, [indicatorQuery.data])

  const tvSymbol = tradingViewSymbol(symbol, dataSource)
  // Optional chaining rather than `query.data.bars.length`: a response whose
  // shape is not what this version expects -- an older backend, a proxy
  // returning something else -- would otherwise throw during render and
  // unmount the whole dashboard over a chart.
  const empty = query.isSuccess && !query.data?.bars?.length
  // 「Could not ask」 and 「there is nothing here」 need different words: one
  // clears on its own and one never will, and showing the permanent message
  // for the transient case is how a stock with fifty years of candles reads
  // as delisted.
  const fetchFailed = empty && query.data?.fetch_failed === true
  // A 404 is not a transient outage: it means the deployed backend is older
  // than the page asking it, and 「稍後重新整理」 would leave somebody waiting
  // for something that never happens on its own.
  const notFound = query.error instanceof ApiError && query.error.status === 404
  // The container is a plain div and lightweight-charts draws inside it, so it
  // has to be told to grow too -- otherwise the extra panes are drawn outside
  // the border and clipped.
  const extraPanes = new Set(
    (indicatorQuery.data?.series ?? [])
      .filter((entry) => entry.pane !== 'price')
      .map((entry) => entry.name),
  ).size

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1">
        {timeframes.options.map(({ value, label }) => (
          <button
            key={value}
            type="button"
            aria-pressed={timeframe === value}
            onClick={() => {
              setMovedFrom(null)
              setTimeframe(value)
            }}
            className={`rounded px-3 py-1 text-sm ${
              timeframe === value
                ? 'bg-sky-700 font-medium text-white'
                : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {movedFrom && (
        <p role="status" className="text-xs text-amber-400">
          「{symbol}」的資料來源沒有{movedFrom}，已改用日線。
        </p>
      )}

      <ChartIndicators selected={indicators} onChange={setIndicators} />

      {/* An indicator that could not be computed is a problem with the
          indicator, not with the price history. Said next to the chart rather
          than over it, so the candles -- which are fine -- stay readable. */}
      {indicatorQuery.isError && (
        <p role="alert" className="text-xs text-amber-400">
          {indicatorQuery.error instanceof ApiError && indicatorQuery.error.status === 422
            ? indicatorQuery.error.message
            : indicatorQuery.error instanceof ApiError && indicatorQuery.error.status === 404
              ? '後端還沒有指標功能 —— 線上的後端比這個畫面舊。部署是自動的（CI 全綠就會部署），等一兩分鐘重新整理通常就好；一直這樣就去 GitHub 的 Actions 看最後一次有沒有失敗。'
              : '指標算不出來 —— 稍後重新整理看看。K 棒和報價不受影響。'}
        </p>
      )}

      {/* Said beside the chart, never over it. The candles already on screen
          are correct and stay readable; covering them with 「載入中…」 while
          more history arrives would reproduce the blank the reader reported,
          only this time drawn on purpose. */}
      {query.isPlaceholderData && (
        <p role="status" className="text-xs text-slate-400">
          載入更早的資料…
        </p>
      )}

      <div className="relative">
        <div
          ref={containerRef}
          role="img"
          aria-label={
            query.data?.bars?.length ? `${symbol} 價格走勢圖` : `${symbol} 價格走勢圖（目前沒有資料）`
          }
          style={{ height: CHART_HEIGHT_PX + extraPanes * INDICATOR_PANE_HEIGHT_PX }}
          className="rounded border border-slate-800"
        />

        {/* Every non-chart state says which one it is. A blank chart with no
            explanation is indistinguishable from a typo, an outage and a
            broken app, which is exactly what the embedded widget gave. */}
        {(renderFailed || unpriceable || query.isPending || empty || query.isError) && (
          <div className="absolute inset-0 z-[60] flex items-center justify-center rounded bg-slate-950/80 p-4 text-center text-sm">
            {renderFailed ? (
              <p role="alert" className="text-red-400">
                這個瀏覽器畫不出圖表。下面的「在 TradingView 開啟」還是可以看，
                報價和提醒也都不受影響。
              </p>
            ) : unpriceable ? (
              <p className="text-amber-300">{unpriceable}</p>
            ) : query.isError ? (
              <p role="alert" className="text-red-400">
                {notFound
                  ? '後端還沒有這個功能 —— 線上的後端比這個畫面舊。部署是自動的，等一兩分鐘重新整理；一直這樣就去 GitHub 的 Actions 看最後一次有沒有失敗。'
                  : '讀不到歷史資料。可能是後端還沒醒（免費方案的冷啟動常常要一分鐘左右），或者行情來源暫時不通 —— 稍後重新整理看看。'}
              </p>
            ) : fetchFailed ? (
              <p role="alert" className="text-amber-300">
                暫時抓不到「{symbol}」的歷史資料 —— 行情來源沒有回應，可能是被限流了。
                這是暫時的，稍後重新整理就會回來。
              </p>
            ) : empty ? (
              <p className="text-slate-400">
                查不到「{symbol}」的歷史資料。代號可能剛上市，或者這個市場這個 app 抓不到。
              </p>
            ) : (
              <p className="text-slate-500">載入中…</p>
            )}
          </div>
        )}
      </div>

      <p className="text-xs text-slate-500">
        指標是後端算的，跟策略、回測用的是同一份程式，所以圖上看到的就是策略會用到的數字。
        要畫趨勢線的話{' '}
        <a
          href={`https://www.tradingview.com/chart/?symbol=${encodeURIComponent(tvSymbol)}`}
          target="_blank"
          rel="noreferrer"
          className="text-sky-400 underline"
        >
          在 TradingView 開啟
        </a>
        。
      </p>
    </div>
  )
}
