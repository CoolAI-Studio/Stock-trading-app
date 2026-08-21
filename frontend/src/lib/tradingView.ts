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
