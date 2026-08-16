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
