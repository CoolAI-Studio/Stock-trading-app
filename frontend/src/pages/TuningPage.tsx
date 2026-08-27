/**
 * 調參：同一支策略跑一整個參數網格，把結果排出來。
 *
 * #34 的後端做完了，但三個端點對這個使用者等於不存在——他不會去打 API。這一頁是那
 * 件事唯一到得了他手上的路。
 *
 * ＊ 這一頁最容易做壞的地方，是把它做成一張「最佳參數」表。
 *
 * 後端在每一次掃描的結果裡都附了一句：在整個網格上挑最高的那一格，挑到的通常是雜
 * 訊。那句話如果沒有出現在畫面上，這一頁就是在教使用者做錯的事——而他不是工程師，
 * 他會相信排在第一列的那一組。所以 notes 是**主動顯示**的，不是折起來的說明。
 *
 * ＊ 欄位照策略自己宣告的參數畫。
 *
 * 一個不是工程師的人不會知道 self.params 裡有哪幾個鍵。叫他自己打名字，打錯一個字
 * 就會拿到一句他不知道怎麼修的錯誤訊息。
 */

import { useMutation, useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { api } from '../lib/api'
import { parseValues } from '../lib/tuning'

type DeclaredParams = Record<string, number | boolean | string>

interface StrategyListItem {
  id: number
  name: string
  symbol: string
}

interface StrategyDetail {
  source_code: string
}

interface ValidateResult {
  ok: boolean
  declared_params: DeclaredParams
  entry_point: string | null
}

interface SweepSummary {
  trade_count: number
  win_rate_pct: string | null
  net_pnl: string
  total_return_pct: string
  max_drawdown_pct: string
}

interface SweepRow {
  params: Record<string, number | boolean | string>
  summary: SweepSummary | null
  error: string | null
}

interface SweepResult {
  symbol: string
  timeframe: string
  bars_total: number
  first_bar_at: string | null
  last_bar_at: string | null
  rows: SweepRow[]
  notes: string[]
  truncated_note: string | null
}

function todayMinus(days: number): string {
  const when = new Date()
  when.setDate(when.getDate() - days)
  return when.toISOString().slice(0, 10)
}

export function TuningPage() {
  const [strategyId, setStrategyId] = useState('')
  const [values, setValues] = useState<Record<string, string>>({})
  const [start, setStart] = useState(todayMinus(365))
  const [end, setEnd] = useState(todayMinus(0))
  const [problem, setProblem] = useState<string | null>(null)
  const [result, setResult] = useState<SweepResult | null>(null)

  const strategies = useQuery({
    queryKey: ['strategies'],
    queryFn: () => api.get<StrategyListItem[]>('/api/strategies'),
  })

  const selected = strategyId || (strategies.data?.[0] ? String(strategies.data[0].id) : '')

  // 可調的參數要從原始碼問出來。策略列表沒有帶 declared_params——它得編譯過才知
  // 道，而編譯就是執行使用者的程式碼（#18），所以那件事不會發生在一個 GET 上。
  const declared = useQuery({
    queryKey: ['declared-params', selected],
    enabled: Boolean(selected),
    queryFn: async () => {
      const detail = await api.get<StrategyDetail>(`/api/strategies/${selected}`)
      return api.post<ValidateResult>('/api/strategies/validate', {
        source_code: detail.source_code,
      })
    },
  })

  const knobs = useMemo(
    () => Object.entries(declared.data?.declared_params ?? {}),
    [declared.data],
  )

  const sweep = useMutation({
    mutationFn: (grid: Record<string, (number | boolean | string)[]>) =>
      api.post<SweepResult>('/api/backtests/sweep', {
        strategy_id: Number(selected),
        start: `${start}T00:00:00Z`,
        end: `${end}T23:59:59Z`,
        grid,
      }),
    onSuccess: (data) => {
      setResult(data)
      setProblem(null)
    },
    onError: (err: unknown) => {
      setResult(null)
      setProblem(err instanceof Error ? err.message : '掃描失敗了，請再試一次。')
    },
  })

  function submit() {
    const grid: Record<string, (number | boolean | string)[]> = {}
    for (const [name] of knobs) {
      const parsed = parseValues(values[name] ?? '')
      // 沒填的不送。全部送出去的話，沒填的那個會變成空陣列而後端拒絕整個網格，
      // 而「只想調一個參數」是常態。
      if (parsed.length > 0) grid[name] = parsed
    }
    if (Object.keys(grid).length === 0) {
      setProblem('至少填一個參數要試的值，例如 5, 10, 20。')
      setResult(null)
      return
    }
    setProblem(null)
    sweep.mutate(grid)
  }

  const rows = result?.rows ?? []

  return (
    <div className="space-y-6 p-4">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold">調參</h1>
        <p className="text-sm text-slate-400">
          同一支策略，把幾組參數放在<strong>同一段歷史</strong>上跑一遍，比較看看。
        </p>
      </header>

      <section className="space-y-3">
        <label className="block text-sm">
          <span className="mb-1 block text-slate-300">策略</span>
          <select
            className="w-full rounded border border-slate-700 bg-slate-900 p-2"
            value={selected}
            onChange={(event) => {
              setStrategyId(event.target.value)
              setValues({})
              setResult(null)
            }}
          >
            {(strategies.data ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}（{item.symbol}）
              </option>
            ))}
          </select>
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm">
            <span className="mb-1 block text-slate-300">從</span>
            <input
              type="date"
              className="w-full rounded border border-slate-700 bg-slate-900 p-2"
              value={start}
              onChange={(event) => setStart(event.target.value)}
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block text-slate-300">到</span>
            <input
              type="date"
              className="w-full rounded border border-slate-700 bg-slate-900 p-2"
              value={end}
              onChange={(event) => setEnd(event.target.value)}
            />
          </label>
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-slate-300">要試哪些值</h2>
        {declared.isLoading && <p className="text-sm text-slate-400">正在讀這支策略的參數…</p>}
        {!declared.isLoading && knobs.length === 0 && (
          <p className="text-sm text-slate-400">
            這支策略沒有宣告可調的參數，所以沒有東西可以掃。
          </p>
        )}
        {knobs.map(([name, fallback]) => (
          <label key={name} className="block text-sm">
            <span className="mb-1 block text-slate-300">
              {name}
              <span className="ml-2 text-xs text-slate-500">預設 {String(fallback)}</span>
            </span>
            <input
              type="text"
              aria-label={name}
              placeholder="5, 10, 20"
              className="w-full rounded border border-slate-700 bg-slate-900 p-2"
              value={values[name] ?? ''}
              onChange={(event) => setValues({ ...values, [name]: event.target.value })}
            />
          </label>
        ))}

        <button
          type="button"
          className="rounded bg-sky-600 px-4 py-2 text-sm font-semibold disabled:opacity-50"
          onClick={submit}
          disabled={sweep.isPending}
        >
          {sweep.isPending ? '掃描中…' : '開始掃描'}
        </button>
      </section>

      {problem && (
        <p role="status" className="text-sm text-amber-400">
          {problem}
        </p>
      )}

      {result && (
        <section className="space-y-3">
          {/*
            這幾句是這一頁存在的理由的一半。後端每次都附上它們，而不顯示的話，這
            一頁就變成一張「最佳參數」表——而使用者不是工程師，他會相信第一列。
          */}
          {result.notes.map((note) => (
            <p key={note} role="note" className="rounded bg-slate-800 p-3 text-sm text-amber-300">
              {note.replaceAll('**', '')}
            </p>
          ))}
          {result.truncated_note && (
            <p role="note" className="rounded bg-slate-800 p-3 text-sm text-amber-300">
              {result.truncated_note}
            </p>
          )}

          <p className="text-xs text-slate-400">
            每一組都跑在<strong>同一批 {result.bars_total} 根 K 棒</strong>上
            （{result.first_bar_at?.slice(0, 10)} 到 {result.last_bar_at?.slice(0, 10)}）。
          </p>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-slate-400">
                <tr>
                  <th className="p-2">參數</th>
                  <th className="p-2">淨損益</th>
                  <th className="p-2">報酬率</th>
                  <th className="p-2">成交</th>
                  <th className="p-2">最大回檔</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={JSON.stringify(row.params)} className="border-t border-slate-800">
                    <td className="p-2">
                      {Object.entries(row.params)
                        .map(([name, value]) => `${name}=${String(value)}`)
                        .join('、')}
                    </td>
                    {row.summary ? (
                      <>
                        <td className="p-2">{row.summary.net_pnl}</td>
                        <td className="p-2">{row.summary.total_return_pct}%</td>
                        <td className="p-2">{row.summary.trade_count}</td>
                        <td className="p-2">{row.summary.max_drawdown_pct}%</td>
                      </>
                    ) : (
                      // **不畫成 0。** 畫成 0 的話它在排序時會沉到最底下，看起來
                      // 像一個結論——而它其實是沒有答案。後端已經分開了這兩件事。
                      <td className="p-2 text-slate-500" colSpan={4}>
                        沒跑完 —— {row.error}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  )
}
