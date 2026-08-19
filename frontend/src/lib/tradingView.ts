import type { DataSource } from './types'

/**
 * This app's symbols, spelled the way TradingView spells them.
 *
 * The chart "could not display Taiwanese stocks". TradingView has had them all
 * along: TWSE:2330 is Taiwan Semiconductor, TPEX:6488 is GlobalWafers -- both
 * confirmed against TradingView's own symbol resolver, not assumed. What the
 * app sent was `2330.TW`, which is yfinance's spelling and resolves to nothing
 * on TradingView. The stocks were never missing; the request was.
 *
 * WHY THIS IS CAUTIOUS RATHER THAN THOROUGH. A missing chart is visible and
 * annoying. A WRONG chart is neither: a symbol that resolves to some other
 * instrument draws a real, convincing, wrong price history, and nobody
 * double-checks a chart that looks fine. So this translates only the markets
 * the app actually models -- the same set market_calendar.py classifies -- and
 * passes everything else through untouched. Passing through fails visibly.
 * Guessing fails invisibly.
 */

// Taiwan's two boards are different exchanges on TradingView, and a listed
// company and an OTC one can share a number. Sending an OTC symbol to TWSE
// would chart a different company at real prices.
const TAIWAN_EXCHANGES: [suffix: string, exchange: string][] = [
  // .TWO first: '2330.TWO'.endsWith('.TW') is false, but the order is pinned
  // anyway so a later switch to a prefix/contains test cannot silently start
  // routing every OTC symbol to the listed board.
  ['.TWO', 'TPEX'],
  ['.TW', 'TWSE'],
]

export function tradingViewSymbol(symbol: string, dataSource?: DataSource): string {
  const trimmed = symbol.trim()
  if (!trimmed) return ''

  // Already exchange-qualified: whoever typed this has named the exact symbol
  // they want, and second-guessing it can only make it wrong.
  if (trimmed.includes(':')) return trimmed

  const upper = trimmed.toUpperCase()

  for (const [suffix, exchange] of TAIWAN_EXCHANGES) {
    if (!upper.endsWith(suffix)) continue
    const code = upper.slice(0, -suffix.length)
    // A bare suffix is not a symbol. `TWSE:` would be a nonsense request.
    if (!code) break
    return `${exchange}:${code}`
  }

  if (dataSource === 'binance') return `BINANCE:${upper}`

  // Bare US tickers already work, and anything else (.HK, .T, .L) is a market
  // this app does not model anywhere -- see market_calendar.py, which reads
  // those as "cannot tell" too.
  return trimmed
}

/** Says so when the symbol carries a market suffix this app cannot turn into a
 * TradingView exchange, so a blank chart comes with a reason.
 *
 * The alternative is what used to happen: the widget quietly fails to resolve
 * the symbol and shows nothing, leaving the owner unable to tell a typo from a
 * market the app does not cover. Everything else here exists to avoid drawing
 * a wrong chart; this exists to avoid drawing no chart and saying nothing.
 */
export function unsupportedMarketNote(symbol: string, dataSource?: DataSource): string | null {
  const trimmed = symbol.trim()
  if (!trimmed || trimmed.includes(':')) return null
  if (dataSource === 'binance') return null

  const translated = tradingViewSymbol(trimmed, dataSource)
  if (translated.includes(':')) return null

  const dot = trimmed.lastIndexOf('.')
  if (dot < 0) return null // a bare ticker; TradingView resolves those itself

  const suffix = trimmed.slice(dot).toUpperCase()
  return (
    `這個代號的市場後綴 ${suffix} 對應不到 TradingView 的交易所，圖表可能是空白的。` +
    "價格、策略與提醒不受影響 —— 那些走的是後端自己的行情來源，跟這張圖無關。"
  )
}
