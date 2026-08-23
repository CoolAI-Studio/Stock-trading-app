import '@testing-library/jest-dom/vitest'
import { vi } from 'vitest'

/** lightweight-charts draws on an HTML5 canvas, and jsdom has no canvas.
 *
 * Stubbed globally rather than per test file: any page that renders a chart
 * would otherwise throw on mount and take the whole page's test suite with it,
 * which is a failure about jsdom rather than about the page.
 *
 * A test that is actually ABOUT the chart re-mocks this itself with recording
 * spies -- see components/PriceChart.test.tsx, which asserts on the series it
 * hands over. Whether TradingView's renderer draws correct pixels is their
 * test, not ours.
 */
vi.mock('lightweight-charts', () => ({
  createChart: () => ({
    addSeries: () => ({
      setData: () => {},
      priceScale: () => ({ applyOptions: () => {} }),
      applyOptions: () => {},
    }),
    removeSeries: () => {},
    panes: () => [{ setStretchFactor: () => {} }],
    removePane: () => {},
    timeScale: () => ({
      fitContent: () => {},
      // The chart now asks for more history when the view reaches the oldest
      // candle. A stub without these throws on mount, which would fail every
      // page that merely contains a chart for a reason that is about jsdom.
      subscribeVisibleLogicalRangeChange: () => {},
      unsubscribeVisibleLogicalRangeChange: () => {},
      getVisibleRange: () => null,
      setVisibleRange: () => {},
      applyOptions: () => {},
    }),
    applyOptions: () => {},
    remove: () => {},
  }),
  CandlestickSeries: 'CANDLESTICK',
  HistogramSeries: 'HISTOGRAM',
  LineSeries: 'LINE',
  ColorType: { Solid: 'solid' },
}))
