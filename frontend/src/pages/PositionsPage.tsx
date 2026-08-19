import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from '../lib/api'
import { ActionError } from '../components/ActionError'
import { ExportButton } from '../components/ExportButton'
import type { Position, Strategy } from '../lib/types'

const GLOBAL_RISK_LABEL = '全域'
const RISK_OWNER_HELP =
  '「風險設定」那一欄是這個部位的停損、停利會照誰的數字算。標「全域」表示用風險設定頁的全域值（手動建立或 TradingView 進場的部位都是這種）；標策略名稱則是用那個策略自己的設定。同一檔股票由誰先建立部位就跟誰，後來別的策略再買進也不會換人，要等部位出清才重新認一次。'

/** Whose stop-loss / take-profit thresholds this position is scanned under.
 * Falls back to the id when the strategy list is momentarily stale -- the
 * column may not go blank, since blank reads as 全域. */
function riskOwnerLabel(position: Position, strategies: Strategy[]): string {
  if (position.strategy_id === null) return GLOBAL_RISK_LABEL
  return strategies.find((s) => s.id === position.strategy_id)?.name ?? `#${position.strategy_id}`
}

function AdjustPositionForm({ position, onDone }: { position: Position; onDone: () => void }) {
  const [quantity, setQuantity] = useState(position.quantity)
  const [avgEntryPrice, setAvgEntryPrice] = useState(position.avg_entry_price)
  const [error, setError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const adjustMutation = useMutation({
    mutationFn: () =>
      api.patch<Position>(`/api/positions/${position.symbol}`, {
        quantity,
        avg_entry_price: avgEntryPrice,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['positions'] })
      onDone()
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : '儲存失敗'),
  })

  return (
    <div className="flex flex-wrap items-end gap-2 rounded border border-slate-800 p-3">
      <div>
        <label htmlFor={`adjust-qty-${position.symbol}`} className="text-sm text-slate-400">
          數量
        </label>
        <input
          id={`adjust-qty-${position.symbol}`}
          value={quantity}
          onChange={(e) => setQuantity(e.target.value)}
          className="block w-32 rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
      </div>
      <div>
        <label htmlFor={`adjust-avg-${position.symbol}`} className="text-sm text-slate-400">
          平均成本
        </label>
        <input
          id={`adjust-avg-${position.symbol}`}
          value={avgEntryPrice}
          onChange={(e) => setAvgEntryPrice(e.target.value)}
          className="block w-32 rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
      </div>
      {error && <p className="text-red-400">{error}</p>}
      <button
        disabled={adjustMutation.isPending}
        onClick={() => adjustMutation.mutate()}
        className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
      >
        儲存
      </button>
      <button
        onClick={onDone}
        className="rounded bg-slate-800 px-3 py-1 text-sm font-medium text-slate-300 hover:bg-slate-700"
      >
        取消
      </button>
    </div>
  )
}

/** Grey when there is no quote: an unpriced position is neither winning nor
 * losing, and colouring it green would say it was. */
function unrealizedTone(pnl: string | null): string {
  if (pnl === null) return 'text-slate-500'
  return Number(pnl) >= 0 ? 'text-emerald-400' : 'text-red-400'
}

function PositionRow({ position, riskOwner }: { position: Position; riskOwner: string }) {
  const [adjusting, setAdjusting] = useState(false)
  const queryClient = useQueryClient()

  const flattenMutation = useMutation({
    mutationFn: () => api.delete(`/api/positions/${position.symbol}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['positions'] }),
  })

  function handleFlatten() {
    if (window.confirm(`確定要出清「${position.symbol}」的部位嗎？此操作無法復原。`)) {
      flattenMutation.mutate()
    }
  }

  return (
    <>
      <tr className="border-b border-slate-800">
        <td className="py-2 pr-4 font-medium">{position.symbol}</td>
        <td className="py-2 pr-4">{position.quantity}</td>
        <td className="py-2 pr-4">{position.avg_entry_price}</td>
        <td className="py-2 pr-4" data-testid="current-price">
          {position.current_price ?? '—'}
        </td>
        {/* The reason this page exists: am I up or down right now. Both
            figures are null together when no quote has reached this symbol,
            and an em-dash says that -- a zero would read as "flat". */}
        <td
          data-testid="unrealized"
          className={`py-2 pr-4 ${unrealizedTone(position.unrealized_pnl)}`}
        >
          {position.unrealized_pnl === null
            ? '—'
            : `${position.unrealized_pnl}（${position.unrealized_pnl_pct}%）`}
        </td>
        <td
          className={`py-2 pr-4 ${Number(position.realized_pnl) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}
        >
          {position.realized_pnl}
        </td>
        <td className="py-2 pr-4 text-slate-500">
          {position.opened_at ? new Date(position.opened_at).toLocaleString() : '—'}
        </td>
        <td className="py-2 pr-4">
          <span
            className={
              riskOwner === GLOBAL_RISK_LABEL
                ? 'rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-400'
                : 'rounded border border-sky-700 bg-sky-950/40 px-2 py-0.5 text-xs text-sky-300'
            }
          >
            {riskOwner}
          </span>
        </td>
        <td className="flex flex-wrap items-center gap-2 py-2">
          <ActionError error={flattenMutation.error} />
          <button
            onClick={() => setAdjusting((v) => !v)}
            className="rounded bg-slate-700 px-3 py-1 text-sm font-medium text-white hover:bg-slate-600"
          >
            調整
          </button>
          <button
            disabled={flattenMutation.isPending}
            onClick={handleFlatten}
            className="rounded bg-red-900 px-3 py-1 text-sm font-medium text-red-200 hover:bg-red-800 disabled:opacity-50"
          >
            出清
          </button>
        </td>
      </tr>
      {adjusting && (
        <tr>
          <td colSpan={7} className="pb-4">
            <AdjustPositionForm position={position} onDone={() => setAdjusting(false)} />
          </td>
        </tr>
      )}
    </>
  )
}

function NewPositionForm({ onDone }: { onDone: () => void }) {
  const [symbol, setSymbol] = useState('')
  const [quantity, setQuantity] = useState('')
  const [avgEntryPrice, setAvgEntryPrice] = useState('')
  const [error, setError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const createMutation = useMutation({
    mutationFn: () =>
      api.patch<Position>(`/api/positions/${symbol.toUpperCase()}`, {
        quantity,
        avg_entry_price: avgEntryPrice,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['positions'] })
      onDone()
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : '建立失敗'),
  })

  return (
    <div className="space-y-3 rounded border border-slate-800 p-4">
      <div>
        <label htmlFor="new-position-symbol" className="text-sm text-slate-400">
          代號
        </label>
        <input
          id="new-position-symbol"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
      </div>
      <div>
        <label htmlFor="new-position-qty" className="text-sm text-slate-400">
          數量
        </label>
        <input
          id="new-position-qty"
          value={quantity}
          onChange={(e) => setQuantity(e.target.value)}
          className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
      </div>
      <div>
        <label htmlFor="new-position-avg" className="text-sm text-slate-400">
          平均成本
        </label>
        <input
          id="new-position-avg"
          value={avgEntryPrice}
          onChange={(e) => setAvgEntryPrice(e.target.value)}
          className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
      </div>

      {error && <p className="text-red-400">{error}</p>}

      <button
        disabled={createMutation.isPending || !symbol || !quantity || !avgEntryPrice}
        onClick={() => createMutation.mutate()}
        className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
      >
        建立
      </button>
    </div>
  )
}

export function PositionsPage() {
  const [showForm, setShowForm] = useState(false)
  const positionsQuery = useQuery({
    queryKey: ['positions'],
    queryFn: () => api.get<Position[]>('/api/positions'),
  })
  // Only for turning the attributed strategy id into a name; a position whose
  // strategy has been deleted comes back unattributed anyway.
  const strategiesQuery = useQuery({
    queryKey: ['strategies'],
    queryFn: () => api.get<Strategy[]>('/api/strategies'),
  })

  const positions = positionsQuery.data ?? []
  const strategies = strategiesQuery.data ?? []

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">部位</h1>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500"
        >
          新增部位
        </button>
      </div>

      {showForm && <NewPositionForm onDone={() => setShowForm(false)} />}

      {positions.length === 0 && positionsQuery.isSuccess && (
        <p className="text-slate-500">目前沒有持有部位。</p>
      )}

      {/* The owner agreed to first-opener-wins on condition they could see
          which strategy a position landed under, so the rule is spelled out
          next to the column rather than left in a tooltip. */}
      {positions.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-slate-500">{RISK_OWNER_HELP}</p>
          <ExportButton resource="positions" label="匯出 CSV" />
        </div>
      )}

      {positions.length > 0 && (
        <table className="w-full text-left text-sm">
          <thead className="text-slate-500">
            <tr>
              <th className="pb-2 font-normal">代號</th>
              <th className="pb-2 font-normal">數量</th>
              <th className="pb-2 font-normal">平均成本</th>
              <th className="pb-2 font-normal">現價</th>
              <th className="pb-2 font-normal">未實現損益</th>
              <th className="pb-2 font-normal">已實現損益</th>
              <th className="pb-2 font-normal">建倉時間</th>
              <th className="pb-2 font-normal">風險設定</th>
              <th className="pb-2 font-normal">操作</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((position) => (
              <PositionRow
                key={position.symbol}
                position={position}
                riskOwner={riskOwnerLabel(position, strategies)}
              />
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
