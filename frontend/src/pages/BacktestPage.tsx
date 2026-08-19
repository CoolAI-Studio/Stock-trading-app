import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from '../lib/api'
import { DeleteButton } from '../components/DeleteButton'
import { EquityCurveChart } from '../components/EquityCurveChart'
import { RunComparison } from '../components/RunComparison'
import type {
  BacktestRun,
  BacktestRunDetail,
  BacktestSummary,
  BacktestTrade,
  BrokerCostPreset,
  ExitReason,
  FillPriceBasis,
  Strategy,
} from '../lib/types'

const FILL_BASIS_LABEL: Record<FillPriceBasis, string> = {
  next_open: '下一根 K 棒開盤價',
  close: '當根 K 棒收盤價',
}

/** Taiwan retail defaults, so the form is runnable without the owner having to
 * research brokerage fees first. Tax is left at 0 because it only applies to
 * Taiwan equities -- charging it silently on a US backtest would overstate the
 * costs as confidently as omitting it understates them. */
/** 1wk is not a word. The owner reads 週線. */
const TIMEFRAME_LABEL: Record<string, string> = {
  '1m': '1 分線',
  '5m': '5 分線',
  '15m': '15 分線',
  '1h': '小時線',
  '1d': '日線',
  '1wk': '週線',
  '1mo': '月線',
}

/** Which exits the strategy chose and which were forced. Kept distinct
 * because "my rules make money but the stop keeps cutting them" is the most
 * actionable thing this table can say, and it is invisible when every row
 * just reads "sold". */
const EXIT_REASON_LABEL: Record<ExitReason, string> = {
  signal: '策略訊號',
  stop_loss: '停損出場',
  take_profit: '停利出場',
}

const DEFAULTS = {
  fill_price_basis: 'next_open' as FillPriceBasis,
  commission_rate: '0.001425',
  minimum_fee: '0',
  slippage_rate: '0.0005',
  sell_tax_rate: '0',
  quantity: '1',
  initial_capital: '100000',
}

function isoDay(date: Date): string {
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${m}-${d}`
}

function defaultRange(): { start: string; end: string } {
  const today = new Date()
  const lastYear = new Date(today)
  lastYear.setFullYear(lastYear.getFullYear() - 1)
  return { start: isoDay(lastYear), end: isoDay(today) }
}

/** 0.001425 tells nobody anything; 0.1425% does. */
function asPercent(rate: string): string {
  const value = Number(rate)
  if (!Number.isFinite(value)) return '—'
  return `${Number((value * 100).toFixed(6))}%`
}

/** A threshold that may be switched off. '0' is not 0% -- it means the
 * simulation did not apply one at all, and the two read completely
 * differently to someone deciding whether to trust the result. */
function threshold(rate: string | undefined): string {
  if (rate === undefined || Number(rate) === 0) return '沒有模擬'
  return asPercent(rate)
}

function money(value: string): string {
  const n = Number(value)
  return Number.isFinite(n) ? n.toLocaleString('en-US', { maximumFractionDigits: 2 }) : value
}

function signedMoney(value: string): string {
  const n = Number(value)
  if (!Number.isFinite(n)) return value
  return `${n >= 0 ? '+' : ''}${n.toLocaleString('en-US', { maximumFractionDigits: 2 })}`
}

function signedPercent(value: string): string {
  const n = Number(value)
  if (!Number.isFinite(n)) return value
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

function toneOf(value: string): string {
  return Number(value) >= 0 ? 'text-emerald-400' : 'text-red-400'
}

function StatCard({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded border border-slate-800 p-4">
      <p className="text-sm text-slate-500">{label}</p>
      <p className={`text-2xl font-semibold ${tone ?? ''}`}>{value}</p>
    </div>
  )
}

/** Turns a backend failure into something the owner can act on. The 422 body
 * is already written for them, so it is passed through untouched; the rest
 * would otherwise surface as bare English. */
function runErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return error instanceof Error ? error.message : '回測失敗，請稍後再試。'
  }
  if (error.status === 422) return error.message
  if (error.status === 404) return '找不到這個策略，可能已經被刪掉了。請重新整理頁面。'
  return `回測失敗（${error.status}）：${error.message}`
}

function AssumptionsBox({ run }: { run: BacktestRunDetail }) {
  const a = run.result.assumptions
  const rows: [string, string][] = [
    ['成交價基準', FILL_BASIS_LABEL[a.fill_price_basis]],
    ['手續費率（單邊）', asPercent(a.commission_rate)],
    ['滑價率', asPercent(a.slippage_rate)],
    ['賣出交易稅率', asPercent(a.sell_tax_rate)],
    ['每次下單數量', money(a.quantity)],
    ['起始本金', money(a.initial_capital)],
    // Shown even when off, and shown as the words rather than as "0%". The
    // live loop always has a stop; silence here would be read as "it was
    // applied" by anyone who knows that, and they would take a number about a
    // strategy that rides every loss to the bottom as evidence about theirs.
    ['停損比例', threshold(a.stop_loss_pct)],
    ['停利比例', threshold(a.take_profit_pct)],
  ]

  return (
    <section
      aria-label="這次回測的假設"
      className="space-y-2 rounded border border-slate-800 bg-slate-900/40 p-4"
    >
      <h3 className="text-sm font-semibold text-slate-300">這次回測的假設</h3>
      <p className="text-xs text-slate-500">
        上面的數字是在這些條件下算出來的。換一組成本，結果就會不一樣。
      </p>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm md:grid-cols-3">
        {rows.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-2">
            <dt className="text-slate-500">{label}</dt>
            <dd className="text-slate-200">{value}</dd>
          </div>
        ))}
      </dl>
      {run.result.assumption_notes.length > 0 && (
        <ul className="list-inside list-disc text-xs text-slate-400">
          {run.result.assumption_notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </section>
  )
}

function Headline({ summary }: { summary: BacktestSummary }) {
  return (
    <section aria-label="績效總覽" className="grid grid-cols-2 gap-4 md:grid-cols-3">
      <StatCard
        label="總報酬率"
        value={signedPercent(summary.total_return_pct)}
        tone={toneOf(summary.total_return_pct)}
      />
      <StatCard label="最終權益" value={money(summary.final_equity)} />
      <StatCard
        label="已實現損益"
        value={signedMoney(summary.net_pnl)}
        tone={toneOf(summary.net_pnl)}
      />
      <StatCard
        label="最大回撤"
        value={`-${Number(summary.max_drawdown_pct).toFixed(2)}%`}
        tone="text-red-400"
      />
      <StatCard label="交易次數" value={String(summary.trade_count)} />
      <StatCard
        label="勝率"
        // "0% 勝率" reads as every trade losing, which is a different and much
        // worse thing than never having traded at all.
        value={summary.win_rate_pct === null ? '沒有交易' : `${Number(summary.win_rate_pct)}%`}
      />
    </section>
  )
}

/** The comparison that decides whether the strategy was worth running.
 *
 * +18% reads as a good year until the stock itself did +40% over the same
 * bars. In a bull run almost anything is profitable; this is the only line
 * that separates "the strategy works" from "the market went up". */
function Benchmark({ summary }: { summary: BacktestSummary }) {
  if (summary.buy_and_hold_return_pct === null || summary.excess_return_pct === null) return null
  const beat = Number(summary.excess_return_pct) >= 0

  return (
    <section
      aria-label="與買進持有比較"
      className={`rounded border px-4 py-3 text-sm ${
        beat
          ? 'border-emerald-800 bg-emerald-950/30 text-emerald-200'
          : 'border-amber-700 bg-amber-950/30 text-amber-200'
      }`}
    >
      <p>
        <strong>{beat ? '這支策略贏過單純買進持有' : '這支策略輸給單純買進持有'}</strong>
        ：同一段期間，什麼都不做買著抱是 {signedPercent(summary.buy_and_hold_return_pct)}，
        策略是 {signedPercent(summary.total_return_pct)}，差距{' '}
        {signedPercent(summary.excess_return_pct)}。
      </p>
      {!beat && (
        <p className="mt-1 text-xs">
          輸給買進持有的策略，等於花了手續費和盯盤的時間去換更差的結果。
        </p>
      )}
    </section>
  )
}

/** Everything the backend already computed and the page never showed.
 *
 * Eighteen figures were being calculated, stored and returned; six reached
 * the screen. The absent ones include the two that most often change the
 * verdict -- what the costs ate, and how many signals never became trades. */
function Details({ summary }: { summary: BacktestSummary }) {
  const rows: Array<[string, string, string?]> = [
    ['獲利因子', factor(summary.profit_factor), '總獲利 ÷ 總虧損。小於 1 就是賠錢的策略'],
    [
      '持倉時間比例',
      summary.exposure_pct === null ? '—' : `${Number(summary.exposure_pct).toFixed(1)}%`,
      '這段期間有多少時候錢真的在市場裡',
    ],
    ['成本總額', money(summary.total_costs), '手續費、滑價與交易稅合計吃掉的錢'],
    ['平均獲利', summary.average_win === null ? '—' : signedMoney(summary.average_win)],
    ['平均虧損', summary.average_loss === null ? '—' : signedMoney(summary.average_loss)],
    ['贏 / 輸 筆數', `${summary.wins} / ${summary.losses}`],
    [
      '停損出場次數',
      String(summary.stop_loss_exits),
      '被停損強制出場，不是策略自己決定賣的',
    ],
    ['停利出場次數', String(summary.take_profit_exits), '碰到停利價自動出場'],
    ['出現訊號', String(summary.signals)],
    [
      '被略過的訊號',
      String(summary.skipped_signals),
      '已經有部位還想買、或空手還想賣，這種訊號不會成交',
    ],
    ['沒成交的訊號', String(summary.unfilled_signals), '缺下一根開盤價或錢不夠'],
    ['實際測到的 K 棒', `${summary.bars_tested} / ${summary.bars_total}`, '差額是暖身用掉的'],
  ]

  return (
    <section aria-label="細項統計" className="space-y-1">
      <h2 className="text-sm font-semibold text-slate-300">細項統計</h2>
      <table className="w-full text-left text-sm">
        <tbody>
          {rows.map(([label, value, hint]) => (
            <tr key={label} className="border-b border-slate-800/60">
              <td className="py-1.5 pr-4 text-slate-400">
                {label}
                {hint && <span className="ml-2 text-xs text-slate-600">{hint}</span>}
              </td>
              <td className="py-1.5 text-right tabular-nums">{value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function factor(value: string | null): string {
  // None means nothing lost, which is not a ratio -- printing a big number
  // would read as brilliance rather than as too few trades to judge.
  if (value === null) return '沒有虧損的交易'
  return Number(value).toFixed(2)
}

function TradeTable({ trades }: { trades: BacktestTrade[] }) {
  if (trades.length === 0) return null

  return (
    <table aria-label="交易明細" className="w-full text-left text-sm">
      <thead className="text-slate-500">
        <tr>
          <th className="pb-2 font-normal">結果</th>
          <th className="pb-2 font-normal">進場時間</th>
          <th className="pb-2 font-normal">出場時間</th>
          <th className="pb-2 font-normal">數量</th>
          <th className="pb-2 font-normal">進場價</th>
          <th className="pb-2 font-normal">出場價</th>
          <th className="pb-2 font-normal">損益</th>
          <th className="pb-2 font-normal">報酬率</th>
          <th className="pb-2 font-normal">出場原因</th>
        </tr>
      </thead>
      <tbody>
        {trades.map((t) => {
          const tone = toneOf(t.pnl)
          return (
            <tr key={`${t.opened_at}-${t.closed_at}`} className="border-b border-slate-800">
              <td className={`py-2 pr-4 ${tone}`}>{Number(t.pnl) >= 0 ? '獲利' : '虧損'}</td>
              <td className="py-2 pr-4 text-slate-400">
                {new Date(t.opened_at).toLocaleDateString()}
              </td>
              <td className="py-2 pr-4 text-slate-400">
                {new Date(t.closed_at).toLocaleDateString()}
              </td>
              <td className="py-2 pr-4">{money(t.quantity)}</td>
              <td className="py-2 pr-4">{money(t.entry_price)}</td>
              <td className="py-2 pr-4">{money(t.exit_price)}</td>
              <td className={`py-2 pr-4 ${tone}`}>{signedMoney(t.pnl)}</td>
              <td className={`py-2 pr-4 ${tone}`}>{signedPercent(t.return_pct)}</td>
              <td className="py-2 text-slate-400">{EXIT_REASON_LABEL[t.exit_reason]}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function RunResult({ run }: { run: BacktestRunDetail }) {
  const { result } = run

  // A run that reached no testable candle would otherwise render as a
  // confident flat 0% -- indistinguishable from a strategy that traded and
  // broke even. The engine's own note says which of the several causes it was.
  if (result.summary.bars_tested === 0) {
    return (
      <div className="space-y-2 rounded border border-amber-800 bg-amber-950/30 p-4">
        <p className="font-medium text-amber-300">這次回測沒有測到任何一根 K 棒。</p>
        {result.notes.map((note) => (
          <p key={note} className="text-sm text-amber-200/80">
            {note}
          </p>
        ))}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <Headline summary={result.summary} />
      <Benchmark summary={result.summary} />
      <AssumptionsBox run={run} />
      <EquityCurveChart
        points={result.equity_curve}
        initialCapital={result.assumptions.initial_capital}
      />
      {result.notes.length > 0 && (
        <ul className="list-inside list-disc space-y-1 text-sm text-slate-400">
          {result.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
      <Details summary={result.summary} />
      <TradeTable trades={result.trades} />
    </div>
  )
}

export function BacktestPage() {
  const queryClient = useQueryClient()
  const [strategyId, setStrategyId] = useState<string>('')
  const [range, setRange] = useState(defaultRange)
  const [costs, setCosts] = useState(DEFAULTS)
  // Which preset the numbers came from, or 'custom' once they are edited by
  // hand. Purely for the dropdown's own display -- the request always carries
  // the numbers, never the preset id, so a preset changing later cannot
  // silently re-price a saved run.
  const [presetId, setPresetId] = useState('custom')
  // Both blank by default, meaning "whatever the strategy itself says". The
  // backend has accepted these overrides all along; the form had no boxes, so
  // trying an idea on another stock meant editing the saved strategy first.
  const [symbolOverride, setSymbolOverride] = useState('')
  const [timeframeOverride, setTimeframeOverride] = useState('')
  // Also blank by default, and blank has to keep meaning "whatever this
  // strategy would actually run under" -- the backend resolves that from the
  // strategy's own risk settings. Pre-filling them with the global 5%/10%
  // would look helpful and quietly detach the run from the strategy the
  // moment the owner changed one and forgot they had.
  const [stopLossOverride, setStopLossOverride] = useState('')
  const [takeProfitOverride, setTakeProfitOverride] = useState('')

  const presetsQuery = useQuery({
    queryKey: ['broker-costs'],
    queryFn: () => api.get<BrokerCostPreset[]>('/api/broker-costs'),
  })
  const presets = presetsQuery.data ?? []

  function applyPreset(id: string) {
    setPresetId(id)
    const preset = presets.find((p) => p.id === id)
    if (!preset) return
    setCosts((prev) => ({
      ...prev,
      commission_rate: preset.commission_rate,
      minimum_fee: preset.minimum_fee,
      sell_tax_rate: preset.sell_tax_rate,
    }))
  }
  const [run, setRun] = useState<BacktestRunDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Ids only, never the rows: the history query refetches after every run, and
  // a held copy of a row would go stale (or point at a run the prune has since
  // evicted) while still looking like a live selection.
  const [compareIds, setCompareIds] = useState<number[]>([])

  const strategiesQuery = useQuery({
    queryKey: ['strategies'],
    queryFn: () => api.get<Strategy[]>('/api/strategies'),
  })
  const historyQuery = useQuery({
    queryKey: ['backtests'],
    queryFn: () => api.get<BacktestRun[]>('/api/backtests'),
  })

  const strategies = strategiesQuery.data ?? []
  const selectedId = strategyId || (strategies[0] ? String(strategies[0].id) : '')
  const selectedStrategy = strategies.find((s) => String(s.id) === selectedId)

  const runMutation = useMutation({
    mutationFn: () =>
      api.post<BacktestRunDetail>('/api/backtests', {
        strategy_id: Number(selectedId),
        // Whole days: ending at the chosen day's midnight would cut that day's
        // own candle out of the range the owner thought they asked for.
        start: `${range.start}T00:00:00Z`,
        end: `${range.end}T23:59:59Z`,
        // Omitted entirely when blank rather than sent empty: the backend
        // reads absent as "use the strategy's own", and an empty string as a
        // validation error.
        ...(symbolOverride.trim() ? { symbol: symbolOverride.trim().toUpperCase() } : {}),
        ...(timeframeOverride ? { timeframe: timeframeOverride } : {}),
        // trim() rather than truthiness: '0' is a real answer ("run this
        // without a stop") and truthiness would throw it away, which is
        // exactly the question the backend went to the trouble of keeping
        // askable.
        ...(stopLossOverride.trim() ? { stop_loss_pct: stopLossOverride.trim() } : {}),
        ...(takeProfitOverride.trim() ? { take_profit_pct: takeProfitOverride.trim() } : {}),
        ...costs,
      }),
    onSuccess: (result) => {
      setRun(result)
      setError(null)
      queryClient.invalidateQueries({ queryKey: ['backtests'] })
    },
    onError: (err) => {
      setRun(null)
      setError(runErrorMessage(err))
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/api/backtests/${id}`),
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: ['backtests'] })
      // The open run is the one being looked at; leaving it on screen after
      // its row is gone reads as the delete having failed.
      setRun((current) => (current?.id === id ? null : current))
    },
  })

  const clearMutation = useMutation({
    mutationFn: () => api.delete<{ deleted: number }>('/api/backtests'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['backtests'] })
      setRun(null)
    },
  })

  const openMutation = useMutation({
    mutationFn: (id: number) => api.get<BacktestRunDetail>(`/api/backtests/${id}`),
    onSuccess: (result) => {
      setRun(result)
      setError(null)
    },
    onError: (err) => setError(runErrorMessage(err)),
  })

  function toggleCompare(id: number) {
    setCompareIds((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id)
      // Two at a time. Picking a third drops the one chosen first, so the
      // checkbox never silently refuses a click -- a box that ticks and does
      // nothing is worse than one that displaces something visible.
      return [...prev, id].slice(-2)
    })
  }

  const history = historyQuery.data ?? []
  // A and B are decided by WHEN THE RUNS HAPPENED, not by click order.
  // Otherwise the same two runs read as an improvement or a regression
  // depending on which box was ticked first, and "B − A" means nothing.
  const comparePair = compareIds
    .map((id) => history.find((row) => row.id === id))
    .filter((row): row is BacktestRun => row !== undefined)
    .sort((x, y) => new Date(x.created_at).getTime() - new Date(y.created_at).getTime())

  function costField(key: keyof typeof DEFAULTS, label: string, hint?: string) {
    return (
      <div>
        <label htmlFor={`bt-${key}`} className="text-sm text-slate-400">
          {label}
        </label>
        <input
          id={`bt-${key}`}
          value={costs[key]}
          onChange={(e) => {
            setCosts((prev) => ({ ...prev, [key]: e.target.value }))
            setPresetId('custom')
          }}
          className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
        {hint && <p className="text-xs text-slate-500">{hint}</p>}
      </div>
    )
  }

  /** Separate from costField because these three-state boxes are not part of
   * DEFAULTS: blank means "inherit", and a preset must not touch them. */
  function thresholdField(
    key: string,
    label: string,
    value: string,
    setValue: (next: string) => void,
  ) {
    return (
      <div>
        <label htmlFor={`bt-${key}`} className="text-sm text-slate-400">
          {label}
        </label>
        <input
          id={`bt-${key}`}
          value={value}
          placeholder="留白＝用策略的設定"
          onChange={(e) => setValue(e.target.value)}
          className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
        <p className="text-xs text-slate-500">
          {value.trim() === '' ? '沿用策略設定' : threshold(value.trim())}
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold">回測</h1>
        <p className="text-sm text-slate-500">
          拿歷史 K 棒把策略重跑一遍，不用真的下單就能看出它過去表現如何。
          回測跑的是策略本人的程式碼，跟實際執行時走同一套流程。
        </p>
      </div>

      <div className="space-y-3 rounded border border-slate-800 p-4">
        <div className="grid gap-3 md:grid-cols-3">
          <div>
            <label htmlFor="bt-strategy" className="text-sm text-slate-400">
              策略
            </label>
            <select
              id="bt-strategy"
              value={selectedId}
              onChange={(e) => setStrategyId(e.target.value)}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            >
              {strategies.map((s) => (
                <option key={s.id} value={String(s.id)}>
                  {s.name}（{s.symbol}）
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="bt-symbol" className="text-sm text-slate-400">
              測試代號（選填）
            </label>
            <input
              id="bt-symbol"
              value={symbolOverride}
              onChange={(e) => setSymbolOverride(e.target.value)}
              placeholder={selectedStrategy ? selectedStrategy.symbol : '沿用策略自己的代號'}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
            <p className="mt-1 text-xs text-slate-500">
              留空就用策略自己的代號。填別的可以拿同一套規則去試另一檔，不用先改存檔。
            </p>
          </div>
          <div>
            <label htmlFor="bt-timeframe" className="text-sm text-slate-400">
              K 棒週期（選填）
            </label>
            <select
              id="bt-timeframe"
              value={timeframeOverride}
              onChange={(e) => setTimeframeOverride(e.target.value)}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            >
              <option value="">沿用策略自己的</option>
              <option value="5m">5 分線</option>
              <option value="15m">15 分線</option>
              <option value="1h">小時線</option>
              <option value="1d">日線</option>
              <option value="1wk">週線</option>
            </select>
            {/* on_bar strategies declare their own candle and the backend
                refuses a conflicting one -- rightly, since replaying that code
                at another size scores behaviour the owner cannot run. Saying
                so here beats letting them find out through a 422. */}
            <p className="mt-1 text-xs text-slate-500">
              只對「逐筆報價（on_tick）」策略有效。用 on_bar 的策略自己就宣告了週期，改這裡會被擋下來。
            </p>
          </div>
          <div>
            <label htmlFor="bt-start" className="text-sm text-slate-400">
              開始日期
            </label>
            <input
              id="bt-start"
              type="date"
              value={range.start}
              onChange={(e) => setRange((prev) => ({ ...prev, start: e.target.value }))}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
          <div>
            <label htmlFor="bt-end" className="text-sm text-slate-400">
              結束日期
            </label>
            <input
              id="bt-end"
              type="date"
              value={range.end}
              onChange={(e) => setRange((prev) => ({ ...prev, end: e.target.value }))}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
        </div>

        <div className="grid gap-3 md:grid-cols-3">
          <div>
            <label htmlFor="bt-fill_price_basis" className="text-sm text-slate-400">
              成交價基準
            </label>
            <select
              id="bt-fill_price_basis"
              value={costs.fill_price_basis}
              onChange={(e) =>
                setCosts((prev) => ({
                  ...prev,
                  fill_price_basis: e.target.value as FillPriceBasis,
                }))
              }
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            >
              <option value="next_open">{FILL_BASIS_LABEL.next_open}</option>
              <option value="close">{FILL_BASIS_LABEL.close}</option>
            </select>
            <p className="text-xs text-slate-500">
              收盤價要等 K 棒收完才知道，所以最早能成交的時間點是下一根開盤。
            </p>
          </div>
          <div className="md:col-span-3">
            <label htmlFor="bt-broker" className="text-sm text-slate-400">
              券商
            </label>
            <select
              id="bt-broker"
              value={presetId}
              onChange={(e) => applyPreset(e.target.value)}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            >
              <option value="custom">自訂（下面自己填）</option>
              {presets.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.label}
                </option>
              ))}
            </select>
            {/* The note matters more than the label: the discount tier is the
                part most likely to be wrong for any given person, and a
                preset read as a promise is worse than no preset. */}
            <p className="mt-1 text-xs text-slate-500">
              {presets.find((p) => p.id === presetId)?.note ??
                '選一家券商會自動帶入手續費、最低手續費與交易稅；任何一格改過就會變成「自訂」。折扣依個人方案而異，請以你實際拿到的費率為準。'}
            </p>
          </div>
          {costField('commission_rate', '手續費率（單邊）', `＝ ${asPercent(costs.commission_rate)}`)}
          {costField(
            'minimum_fee',
            '最低手續費（單邊）',
            costs.minimum_fee === '0' ? '不設下限' : `每筆至少 ${costs.minimum_fee} 元`,
          )}
          {costField('slippage_rate', '滑價率', `＝ ${asPercent(costs.slippage_rate)}`)}
          {costField('sell_tax_rate', '賣出交易稅率', `＝ ${asPercent(costs.sell_tax_rate)}`)}
          {costField('quantity', '每次下單數量')}
          {costField('initial_capital', '起始本金')}
          {thresholdField(
            'stop_loss_pct',
            '停損比例',
            stopLossOverride,
            setStopLossOverride,
          )}
          {thresholdField(
            'take_profit_pct',
            '停利比例',
            takeProfitOverride,
            setTakeProfitOverride,
          )}
        </div>
        <p className="text-xs text-slate-500">
          停損／停利留白，就用這支策略實際執行時的設定跑 —— 回測與盯盤是同一套規則。
          想看「沒有停損會怎樣」就填 0；想試別的數字直接填（0.08 ＝ 8%），不用去改策略。
        </p>

        {strategiesQuery.isSuccess && strategies.length === 0 && (
          <p className="text-sm text-amber-300">
            還沒有任何策略可以回測。請先到「策略」頁建立一支。
          </p>
        )}

        <div className="flex items-center gap-3">
          <button
            disabled={runMutation.isPending || strategies.length === 0}
            onClick={() => runMutation.mutate()}
            className="rounded bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            {runMutation.isPending ? '回測中…' : '開始回測'}
          </button>
          {runMutation.isPending && (
            <span className="text-sm text-slate-400">
              要回放整段歷史的每一根 K 棒，需要一點時間，請不要重複點擊。
            </span>
          )}
        </div>

        {error && <p className="text-red-400">{error}</p>}
      </div>

      {run && <RunResult run={run} />}

      {comparePair.length === 2 && <RunComparison a={comparePair[0]} b={comparePair[1]} />}

      {history.length > 0 && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-slate-300">過去的回測</h2>
            {/* Row by row is not enough here: the list keeps only the most
                recent thirty and evicts silently, so clearing out a batch of
                parameter experiments is the normal way to protect a baseline
                run from being pushed off the end. */}
            <DeleteButton
              what="全部的回測紀錄"
              label="清空全部"
              tone="loud"
              onConfirm={() => clearMutation.mutate()}
              pending={clearMutation.isPending}
              error={clearMutation.error}
            />
          </div>
          <table aria-label="過去的回測" className="w-full text-left text-sm">
            <thead className="text-slate-500">
              <tr>
                <th className="pb-2 font-normal">比較</th>
                <th className="pb-2 font-normal">策略</th>
                <th className="pb-2 font-normal">代號</th>
                <th className="pb-2 font-normal">週期</th>
                <th className="pb-2 font-normal">區間</th>
                <th className="pb-2 font-normal">總報酬率</th>
                <th className="pb-2 font-normal">執行時間</th>
                <th className="pb-2 font-normal">操作</th>
              </tr>
            </thead>
            <tbody>
              {history.map((row) => (
                <tr key={row.id} className="border-b border-slate-800">
                  <td className="py-2 pr-4">
                    <input
                      type="checkbox"
                      aria-label="選來比較"
                      checked={compareIds.includes(row.id)}
                      onChange={() => toggleCompare(row.id)}
                      className="h-4 w-4 accent-emerald-500"
                    />
                  </td>
                  <td className="py-2 pr-4 font-medium">{row.strategy_name}</td>
                  <td className="py-2 pr-4">{row.symbol}</td>
                  <td className="py-2 pr-4 text-slate-400">
                    {TIMEFRAME_LABEL[row.timeframe] ?? row.timeframe}
                  </td>
                  <td className="py-2 pr-4 text-slate-400">
                    {new Date(row.range_start).toLocaleDateString()} –{' '}
                    {new Date(row.range_end).toLocaleDateString()}
                  </td>
                  <td className={`py-2 pr-4 ${toneOf(row.summary.total_return_pct)}`}>
                    {signedPercent(row.summary.total_return_pct)}
                  </td>
                  <td className="py-2 pr-4 text-slate-500">
                    {new Date(row.created_at).toLocaleString()}
                  </td>
                  <td className="flex flex-wrap items-center gap-2 py-2">
                    <button
                      disabled={openMutation.isPending}
                      onClick={() => openMutation.mutate(row.id)}
                      className="rounded bg-slate-700 px-3 py-1 text-sm font-medium text-white hover:bg-slate-600 disabled:opacity-50"
                    >
                      查看
                    </button>
                    <DeleteButton
                      what={`${row.strategy_name}（${row.symbol}）這筆回測`}
                      onConfirm={() => deleteMutation.mutate(row.id)}
                      pending={deleteMutation.isPending && deleteMutation.variables === row.id}
                      error={deleteMutation.variables === row.id ? deleteMutation.error : null}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
