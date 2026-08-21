import { useQuery } from '@tanstack/react-query'
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  createChart,
  type IChartApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../lib/api'
import { looksUnpriceable } from '../lib/symbol'
import { tradingViewSymbol } from '../lib/tradingView'
import type { BarsResponse, DataSource } from '../lib/types'

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
 * WHAT IS LOST, said plainly: drawing tools and the indicator UI. The link at
 * the bottom goes to the real thing for anyone who wants them.
 */

const TIMEFRAMES: [value: string, label: string][] = [
  ['1d', '日'],
  ['1wk', '週'],
  ['1mo', '月'],
]

const CHART_HEIGHT_PX = 460

// Reads as a price chart at a glance in this app's dark theme, and keeps the
// Taiwanese convention: red is up, green is down. Getting that backwards on a
// TW chart is a wrong chart that looks right.
const UP = '#ef4444'
const DOWN = '#22c55e'

export function PriceChart({ symbol, dataSource }: { symbol: string; dataSource?: DataSource }) {
  const [timeframe, setTimeframe] = useState('1d')
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<{ candles: ReturnType<IChartApi['addSeries']> ; volume: ReturnType<IChartApi['addSeries']> } | null>(null)

  // Refused here as well as on the server. Asking the backend about 「台積電」
  // can only ever come back 422, and a request whose answer is already known
  // is a request that should not leave the page.
  const unpriceable = looksUnpriceable(symbol)

  const [renderFailed, setRenderFailed] = useState(false)

  const query = useQuery({
    queryKey: ['bars', symbol, timeframe, dataSource],
    enabled: !unpriceable,
    // No retries. The failures this actually sees are a 404 from a backend
    // older than this page and a 422 from a symbol it will not price -- both
    // permanent, and retrying them means seven seconds of a blank chart before
    // anything is said.
    retry: false,
    queryFn: () =>
      api.get<BarsResponse>(
        `/api/market/bars?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}` +
          (dataSource ? `&data_source=${dataSource}` : ''),
      ),
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
        timeScale: { borderColor: '#334155', timeVisible: false },
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
    series.volume.setData(
      bars.map((bar) => ({
        time: at(bar.time),
        value: bar.volume,
        color: bar.close >= bar.open ? `${UP}55` : `${DOWN}55`,
      })),
    )
    chartRef.current?.timeScale().fitContent()
  }, [query.data])

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

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1">
        {TIMEFRAMES.map(([value, label]) => (
          <button
            key={value}
            type="button"
            aria-pressed={timeframe === value}
            onClick={() => setTimeframe(value)}
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

      <div className="relative">
        <div
          ref={containerRef}
          role="img"
          aria-label={
            query.data?.bars?.length ? `${symbol} 價格走勢圖` : `${symbol} 價格走勢圖（目前沒有資料）`
          }
          style={{ height: CHART_HEIGHT_PX }}
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
                  ? '後端還沒有這個功能 —— 部署的後端版本比這個畫面舊。去 Render 按一次 Manual Deploy，等它跑完再重新整理。'
                  : '讀不到歷史資料。可能是後端還沒醒（Render 免費方案冷啟動要一分鐘左右），或者行情來源暫時不通 —— 稍後重新整理看看。'}
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
        資料來自這個 app 自己的行情來源，跟報價與提醒用的是同一份。要畫線、疊指標的話{' '}
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
