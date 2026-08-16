import { useEffect, useRef } from 'react'

// Tall enough that the price pane isn't squeezed to a sliver -- the widget's
// own toolbar and symbol-info row already take a fixed ~65px off the top.
const CHART_HEIGHT_PX = 650

/** Embeds TradingView's free "Advanced Chart" widget for display only --
 * price data used for strategy math comes from the backend's own
 * yfinance/Binance providers, never from this widget. */
export function TradingViewWidget({ symbol }: { symbol: string }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    // TradingView's embed script replaces its own <script> tag mid-init
    // and isn't safe to interrupt. React 18/19 StrictMode double-invokes
    // mount effects in dev (same DOM node, no unmount in between), which
    // would otherwise wipe the container out from under the first
    // in-flight script and throw inside TradingView's own code. Skipping
    // a re-run for the same symbol on the same node avoids that without
    // needing to fight StrictMode.
    if (container.dataset.tvSymbol === symbol) return
    container.dataset.tvSymbol = symbol

    container.innerHTML = ''

    const script = document.createElement('script')
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js'
    script.async = true
    // Explicit pixel width/height, not autosize: autosize relies on the
    // embedded iframe reading its parent's percentage height, which
    // silently resolved to a ~150px default in this script-injected setup
    // (verified live -- the container was genuinely 600px tall but the
    // iframe inside it only rendered 150px). Fixed dimensions sidestep
    // that entirely.
    script.innerHTML = JSON.stringify({
      width: '100%',
      height: CHART_HEIGHT_PX,
      symbol,
      interval: 'D',
      timezone: 'Etc/UTC',
      theme: 'dark',
      style: '1',
      locale: 'zh_TW',
      allow_symbol_change: true,
    })
    container.appendChild(script)

    // No cleanup on purpose: clearing the dataset guard here would run
    // between StrictMode's two dev-mode invocations and defeat it (see
    // above). A symbol change already falls through the guard on its own
    // since the dataset value won't match; a true unmount removes this
    // whole subtree via React regardless.
  }, [symbol])

  return (
    <div
      ref={containerRef}
      className="tradingview-widget-container"
      style={{ height: CHART_HEIGHT_PX }}
    />
  )
}
