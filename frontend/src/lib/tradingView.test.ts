import { describe, expect, it } from 'vitest'
import { tradingViewSymbol } from './tradingView'

/**
 * Turning this app's symbols into TradingView's.
 *
 * The chart "could not show Taiwanese stocks". It turned out TradingView has
 * had them all along -- TWSE:2330 is Taiwan Semiconductor and TPEX:6488 is
 * GlobalWafers, both confirmed against TradingView's own symbol resolver. What
 * the app sent was `2330.TW`, which is yfinance's spelling and is not a
 * TradingView symbol at all: their resolver returns nothing like it.
 *
 * So this was never a data problem, and the fix is not a second charting
 * engine. It is a translation, and the reason it needs tests is that a WRONG
 * translation is far worse than a missing chart: a symbol that silently
 * resolves to some other instrument shows a real, convincing, wrong price
 * history. Every mapping asserted here was checked against
 * symbol-search.tradingview.com rather than guessed.
 */

describe('台股', () => {
  it('上市股票用 TWSE:', () => {
    expect(tradingViewSymbol('2330.TW')).toBe('TWSE:2330')
  })

  it('上櫃股票用 TPEX: —— 跟上市不是同一個交易所', () => {
    expect(tradingViewSymbol('6488.TWO')).toBe('TPEX:6488')
  })

  it('小寫也要能翻譯', () => {
    // Symbols are uppercased when added to the watchlist, but they also arrive
    // from strategies and positions where nothing enforces that.
    expect(tradingViewSymbol('2330.tw')).toBe('TWSE:2330')
  })

  it('.TWO 不能被當成 .TW 處理', () => {
    // The bug this pins: matching on '.TW' with a prefix test would send every
    // OTC symbol to the wrong exchange, where a same-numbered listed company
    // may well exist -- a chart of the wrong company, showing real prices.
    expect(tradingViewSymbol('6488.TWO')).not.toBe('TWSE:6488')
  })
})

describe('其他市場', () => {
  it('美股原樣送出，因為它本來就會動', () => {
    expect(tradingViewSymbol('AAPL')).toBe('AAPL')
  })

  it('幣安的標的要冠上交易所，不然拿到的是別家的價格', () => {
    expect(tradingViewSymbol('BTCUSDT', 'binance')).toBe('BINANCE:BTCUSDT')
  })

  it('yfinance 的代號不會被誤加上 BINANCE:', () => {
    expect(tradingViewSymbol('AAPL', 'yfinance')).toBe('AAPL')
  })
})

describe('不亂猜', () => {
  it('沒有把握的後綴就原樣送出，不要編一個交易所出來', () => {
    // The app does not model Hong Kong anywhere else -- market_calendar reads
    // .HK as "cannot tell". Inventing HKEX: here would be a guess presented as
    // a fact, and if it were wrong it would draw a real chart of the wrong
    // company rather than fail visibly.
    expect(tradingViewSymbol('0700.HK')).toBe('0700.HK')
  })

  it('已經是 TradingView 格式的就不要再動它', () => {
    // Someone who typed TWSE:2330 has told us the exact symbol they want.
    expect(tradingViewSymbol('TWSE:2330')).toBe('TWSE:2330')
    expect(tradingViewSymbol('BINANCE:ETHUSDT', 'binance')).toBe('BINANCE:ETHUSDT')
  })

  it('空字串不會變成 TWSE: 之類的東西', () => {
    expect(tradingViewSymbol('')).toBe('')
    expect(tradingViewSymbol('   ')).toBe('')
  })

  it('只有後綴、沒有代號的東西不翻譯', () => {
    expect(tradingViewSymbol('.TW')).toBe('.TW')
  })
})
