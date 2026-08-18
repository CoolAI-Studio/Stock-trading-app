import type { EquityPoint } from '../lib/types'

const WIDTH = 720
const HEIGHT = 220

function formatMoney(value: number): string {
  return value.toLocaleString('en-US', { maximumFractionDigits: 2 })
}

/** The account balance over the tested candles, drawn as one line against the
 * starting capital.
 *
 * Inline SVG rather than a charting library: this is a polyline and a
 * baseline, and the app pulls in no chart dependency anywhere else. */
export function EquityCurveChart({
  points,
  initialCapital,
}: {
  points: EquityPoint[]
  initialCapital: string
}) {
  const equities = points.map((p) => Number(p.equity))
  const start = Number(initialCapital)

  if (points.length < 2) {
    return (
      <p className="text-sm text-slate-500">
        只有 {points.length} 根 K 棒，畫不出曲線。把回測區間拉長就會有圖。
      </p>
    )
  }

  // The starting capital is folded into the range on purpose. Scaling to the
  // curve's own min/max would push the break-even line off the canvas whenever
  // the account never returned to it -- and a line that rises across a chart
  // reads as profit even when every point on it is a loss.
  const lo = Math.min(...equities, start)
  const hi = Math.max(...equities, start)
  const span = hi - lo || 1 // a perfectly flat account must not divide by zero

  const x = (i: number) => (i / (points.length - 1)) * WIDTH
  const y = (value: number) => HEIGHT - ((value - lo) / span) * HEIGHT

  const final = equities[equities.length - 1]
  const gained = final >= start
  const vertices = equities.map((value, i) => `${x(i).toFixed(2)},${y(value).toFixed(2)}`).join(' ')

  return (
    <div className="space-y-1">
      <svg
        role="img"
        aria-label={`權益曲線，從 ${formatMoney(start)} 到 ${formatMoney(final)}`}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="none"
        className="h-56 w-full rounded border border-slate-800 bg-slate-950"
      >
        <line
          x1={0}
          x2={WIDTH}
          y1={y(start)}
          y2={y(start)}
          strokeDasharray="4 4"
          className="stroke-slate-600"
          strokeWidth={1}
        />
        <polyline
          points={vertices}
          fill="none"
          strokeWidth={2}
          vectorEffect="non-scaling-stroke"
          className={gained ? 'stroke-emerald-400' : 'stroke-red-400'}
        />
      </svg>
      <p className="text-xs text-slate-500">
        虛線是起始本金 {formatMoney(start)}；線在虛線之上代表賺錢，之下代表賠錢。
      </p>
    </div>
  )
}
