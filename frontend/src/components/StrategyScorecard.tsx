import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { StrategyPerformance } from '../lib/types'

/** What a strategy has actually done since it went live.
 *
 * The backtest produces a full report and a month of real running produced
 * nothing. The orders page says 策略訊號 without saying which strategy, so two
 * running at once were indistinguishable, and no page answered "has this made
 * or lost money".
 */
export function StrategyScorecard({ strategyId }: { strategyId: number }) {
  const { data, isError } = useQuery({
    queryKey: ['strategy-performance', strategyId],
    queryFn: () => api.get<StrategyPerformance>(`/api/strategies/${strategyId}/performance`),
  })

  // Shape-checked, not just null-checked: this sits inside the edit panel, and
  // a response that is not the report -- an error body, an older API -- would
  // otherwise throw during render and take the whole form down with it. A
  // missing scorecard is a small loss; a form that will not open is not.
  if (isError || !data || typeof data.filled_orders !== 'number' || !Array.isArray(data.notes)) {
    return null
  }

  return (
    <section aria-label="上線後表現" className="space-y-2 rounded border border-slate-800 p-3">
      <h3 className="text-sm font-semibold text-slate-300">上線後表現</h3>

      {data.filled_orders === 0 ? (
        <p className="text-sm text-slate-400">
          {data.total_orders === 0
            ? '還沒發出過任何訂單。'
            : `發過 ${data.total_orders} 筆訂單，但一筆都還沒成交。`}
        </p>
      ) : (
        <dl className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
          <Figure
            label="已實現損益"
            value={data.realized_pnl ?? '—'}
            tone={Number(data.realized_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}
          />
          <Figure label="成交筆數" value={`${data.filled_orders} / ${data.total_orders}`} />
          <Figure label="買進總額" value={data.bought_value} />
          <Figure label="賣出總額" value={data.sold_value} />
        </dl>
      )}

      {/* Not a footnote: this figure and the backtest's use different bases,
          and a number read without that will be compared with one that is not
          comparable. */}
      <ul className="list-inside list-disc space-y-0.5 text-xs text-slate-500">
        {data.notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </section>
  )
}

function Figure({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className={`tabular-nums ${tone ?? ''}`}>{value}</dd>
    </div>
  )
}
