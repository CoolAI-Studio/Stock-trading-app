import { StrictMode } from 'react'
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { TradingViewWidget } from './TradingViewWidget'

describe('TradingViewWidget', () => {
  it('inserts exactly one embed script even under StrictMode double-invoke', () => {
    const { container } = render(
      <StrictMode>
        <TradingViewWidget symbol="AAPL" />
      </StrictMode>,
    )

    const scripts = container.querySelectorAll(
      'script[src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js"]',
    )
    expect(scripts).toHaveLength(1)
  })

  it('re-inserts when the symbol prop changes', () => {
    const { container, rerender } = render(<TradingViewWidget symbol="AAPL" />)
    rerender(<TradingViewWidget symbol="TSLA" />)

    const script = container.querySelector('script')
    expect(script?.innerHTML).toContain('"symbol":"TSLA"')
  })
})

// --- the symbol the widget is actually asked for ----------------------------
//
// "The chart can't show Taiwanese stocks" was a spelling problem, not a data
// problem: TradingView has TWSE:2330 and TPEX:6488, and the app was sending
// yfinance's `2330.TW`, which resolves to nothing there. These pin the
// translation at the boundary where it matters -- what goes into the embed
// config -- so a regression shows up as a failing test rather than as a blank
// chart nobody can explain.

describe('送給 TradingView 的代號', () => {
  function embedConfig(container: HTMLElement): Record<string, unknown> {
    const script = container.querySelector('script')
    return JSON.parse(script?.innerHTML ?? '{}')
  }

  it('台股上市翻成 TWSE:', () => {
    const { container } = render(<TradingViewWidget symbol="2330.TW" />)
    expect(embedConfig(container).symbol).toBe('TWSE:2330')
  })

  it('台股上櫃翻成 TPEX:', () => {
    const { container } = render(<TradingViewWidget symbol="6488.TWO" />)
    expect(embedConfig(container).symbol).toBe('TPEX:6488')
  })

  it('美股維持原樣，本來就能看的不要弄壞', () => {
    const { container } = render(<TradingViewWidget symbol="AAPL" />)
    expect(embedConfig(container).symbol).toBe('AAPL')
  })

  it('幣安的標的冠上交易所', () => {
    const { container } = render(<TradingViewWidget symbol="BTCUSDT" dataSource="binance" />)
    expect(embedConfig(container).symbol).toBe('BINANCE:BTCUSDT')
  })
})
