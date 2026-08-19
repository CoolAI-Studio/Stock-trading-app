import type { BacktestRun, BacktestSummary } from '../lib/types'

/**
 * Two runs, held against each other.
 *
 * The history list could show thirty runs and no way to compare any two of
 * them, so "is this version better?" was answered by scrolling and
 * remembering. That is how a 2% improvement gets credited to a code change
 * that was really a date-range change.
 *
 * WHAT THIS DELIBERATELY DOES NOT DO IS PICK A WINNER. Its first job is to
 * list everything that differs between the two runs, and to say plainly, when
 * more than one thing does, that the difference cannot be attributed to any of
 * them. A comparison tool that announces a winner while quietly holding two
 * variables is worse than no tool at all: it manufactures a conclusion, and
 * the owner then acts on it.
 */

/** Which direction is an improvement. Every headline here is better when it
 * rises EXCEPT drawdown and costs, and getting that wrong would colour a
 * deeper drawdown green -- the sort of error nobody re-reads a chart to
 * catch. */
type Direction = 'up-is-good' | 'down-is-good'

interface Metric {
  label: string
  /** null means the run could not produce this number (no trades, nothing
   * lost). Kept as null all the way here so it is never quietly read as 0. */
  value: (s: BacktestSummary) => string | null
  format: (n: number) => string
  direction: Direction
  hint?: string
}

function pct(n: number): string {
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

function plain(n: number): string {
  return n.toFixed(2)
}

function money(n: number): string {
  return n.toLocaleString('en-US', { maximumFractionDigits: 2 })
}

function whole(n: number): string {
  return String(n)
}

const METRICS: Metric[] = [
  { label: '總報酬率', value: (s) => s.total_return_pct, format: pct, direction: 'up-is-good' },
  {
    label: '超額報酬',
    value: (s) => s.excess_return_pct,
    format: pct,
    direction: 'up-is-good',
    hint: '扣掉買進持有之後還剩多少',
  },
  {
    label: '最大回撤',
    value: (s) => s.max_drawdown_pct,
    format: plain,
    direction: 'down-is-good',
    hint: '從高點掉下來最深的一次，越小越好',
  },
  { label: '勝率', value: (s) => s.win_rate_pct, format: plain, direction: 'up-is-good' },
  {
    label: '獲利因子',
    value: (s) => s.profit_factor,
    format: plain,
    direction: 'up-is-good',
    hint: '總獲利 ÷ 總虧損，小於 1 就是賠錢',
  },
  {
    label: '交易次數',
    value: (s) => String(s.trade_count),
    format: whole,
    // Neither more nor fewer trades is better in itself, so it is scored as
    // "up is good" only to have a colour rule at all -- the number is here to
    // explain the others (a strategy that traded twice has not been tested),
    // not to be judged.
    direction: 'up-is-good',
    hint: '多寡本身沒有好壞，但太少就是樣本不夠',
  },
  {
    label: '成本總額',
    value: (s) => s.total_costs,
    format: money,
    direction: 'down-is-good',
  },
]

function asPercentText(rate: string): string {
  const value = Number(rate)
  if (!Number.isFinite(value)) return rate
  if (value === 0) return '沒有模擬'
  return `${Number((value * 100).toFixed(6))}%`
}

function day(iso: string): string {
  return new Date(iso).toLocaleDateString()
}

/** Everything that is not the same between the two runs, in the owner's
 * words. The list IS the feature -- the deltas below it only mean something
 * once this is short. */
function differences(a: BacktestRun, b: BacktestRun): string[] {
  const out: string[] = []

  if (a.code_hash !== b.code_hash) out.push('程式碼不同（這通常就是你想比的那件事）')
  if (a.symbol !== b.symbol) out.push(`股票不同：${a.symbol} → ${b.symbol}`)
  if (a.timeframe !== b.timeframe) out.push(`K 棒週期不同：${a.timeframe} → ${b.timeframe}`)
  if (a.range_start !== b.range_start || a.range_end !== b.range_end) {
    out.push(`區間不同：${day(a.range_start)}–${day(a.range_end)} → ${day(b.range_start)}–${day(b.range_end)}`)
  }

  const costs: [string, keyof BacktestRun['assumptions']][] = [
    ['手續費率', 'commission_rate'],
    ['滑價率', 'slippage_rate'],
    ['賣出交易稅率', 'sell_tax_rate'],
    ['每次下單數量', 'quantity'],
    ['起始本金', 'initial_capital'],
    ['成交價基準', 'fill_price_basis'],
    ['停損比例', 'stop_loss_pct'],
    ['停利比例', 'take_profit_pct'],
  ]
  for (const [label, key] of costs) {
    const left = a.assumptions[key]
    const right = b.assumptions[key]
    if (String(left) === String(right)) continue
    const isRate = key.endsWith('_rate') || key.endsWith('_pct')
    out.push(
      isRate
        ? `${label}不同：${asPercentText(String(left))} → ${asPercentText(String(right))}`
        : `${label}不同：${String(left)} → ${String(right)}`,
    )
  }

  // Same requested range does not mean same data. Providers cap history by
  // interval and hand back what they have, so two runs asked for the same year
  // can have covered different periods -- a confound that is invisible in the
  // header and changes every number below it.
  if (a.summary.bars_tested !== b.summary.bars_tested) {
    out.push(
      `實際測到的 K 棒數不同：${a.summary.bars_tested} → ${b.summary.bars_tested}` +
        '（同樣的區間不代表同樣的資料，行情來源給的歷史長度會變）',
    )
  }

  return out
}

function deltaTone(delta: number, direction: Direction): string {
  if (delta === 0) return 'text-slate-400'
  const better = direction === 'up-is-good' ? delta > 0 : delta < 0
  return better ? 'text-emerald-400' : 'text-red-400'
}

function cell(value: string | null, format: (n: number) => string): string {
  if (value === null) return '—'
  const n = Number(value)
  return Number.isFinite(n) ? format(n) : value
}

export function RunComparison({ a, b }: { a: BacktestRun; b: BacktestRun }) {
  const diffs = differences(a, b)
  const rows = METRICS.map((metric) => {
    const left = metric.value(a.summary)
    const right = metric.value(b.summary)
    const ln = left === null ? null : Number(left)
    const rn = right === null ? null : Number(right)
    const delta =
      ln === null || rn === null || !Number.isFinite(ln) || !Number.isFinite(rn) ? null : rn - ln
    return { metric, left, right, delta }
  })

  const identical = diffs.length === 0 && rows.every((r) => r.delta === 0)

  return (
    <section className="space-y-4 rounded border border-slate-700 bg-slate-900/60 p-4">
      <div>
        <h2 className="text-sm font-semibold text-slate-200">
          比較兩次回測
          <span className="ml-2 text-xs font-normal text-slate-500">
            A ＝ {day(a.created_at)} 跑的 #{a.id}；B ＝ {day(b.created_at)} 跑的 #{b.id}
          </span>
        </h2>
      </div>

      {/* Deliberately above the numbers. Read after them, it is an excuse;
          read before them, it is the thing that decides whether the numbers
          mean anything. */}
      <div aria-label="兩次的差異" className="space-y-1 rounded bg-slate-950/60 p-3 text-sm">
        <p className="text-xs font-semibold text-slate-400">這兩次哪裡不一樣</p>
        {a.code_hash === b.code_hash && (
          <p className="text-slate-400">程式碼相同（跑的是同一份策略）</p>
        )}
        {diffs.length === 0 ? (
          <p className="text-slate-400">設定完全相同。</p>
        ) : (
          <ul className="list-disc space-y-0.5 pl-5 text-slate-300">
            {diffs.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        )}
      </div>

      {diffs.length > 1 && (
        <p
          role="status"
          className="rounded border border-amber-800/60 bg-amber-950/30 p-3 text-sm text-amber-200"
        >
          這兩次同時有 {diffs.length} 個地方不一樣，所以下面的差額 <strong>看不出</strong>{' '}
          是哪一個造成的。
          想知道某一項的影響，就只改那一項再跑一次。
        </p>
      )}

      {identical && (
        <p className="text-sm text-slate-400">兩次的結果完全一樣，沒有差別可以看。</p>
      )}

      <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
        <table className="w-full text-left text-sm">
          <thead className="text-slate-500">
            <tr>
              <th className="pb-2 font-normal">指標</th>
              <th className="pb-2 text-right font-normal">A</th>
              <th className="pb-2 text-right font-normal">B</th>
              <th className="pb-2 text-right font-normal">B − A</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ metric, left, right, delta }) => (
              <tr key={metric.label} className="border-b border-slate-800/60">
                <td className="py-1.5 pr-4 text-slate-400">
                  {metric.label}
                  {metric.hint && <span className="ml-2 text-xs text-slate-600">{metric.hint}</span>}
                </td>
                <td className="py-1.5 text-right tabular-nums">{cell(left, metric.format)}</td>
                <td className="py-1.5 text-right tabular-nums">{cell(right, metric.format)}</td>
                <td
                  className={`py-1.5 text-right tabular-nums ${
                    delta === null ? 'text-slate-600' : deltaTone(delta, metric.direction)
                  }`}
                >
                  {delta === null
                    ? '—'
                    : `${delta >= 0 ? '+' : ''}${metric.format(Math.abs(delta)).replace(/^[+-]/, '')}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
