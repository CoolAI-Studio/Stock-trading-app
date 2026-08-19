import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from '../lib/api'
import { ActionError } from '../components/ActionError'
import { Pager } from '../components/Pager'
import { DeleteButton } from '../components/DeleteButton'
import { ExportButton } from '../components/ExportButton'
import { QueryError } from '../components/QueryError'
import type { Order, OrderSide, OrderSource, OrderStatus } from '../lib/types'

const SIDE_LABEL: Record<OrderSide, string> = { buy: '買進', sell: '賣出' }
const SOURCE_LABEL: Record<OrderSource, string> = {
  strategy: '策略訊號',
  tradingview: 'TradingView',
  manual: '手動',
}
const STATUS_LABEL: Record<OrderStatus, string> = {
  pending: '待確認',
  confirmed: '已確認',
  rejected: '已拒絕',
  expired: '已過期',
  failed: '失敗',
}

// The backend caps a page at 200 and defaults to 50. Fifty is plenty per
// screen; the point of asking explicitly is that the offset goes with it.
const PAGE_SIZE = 50

function useOrdersQuery(status?: string, offset = 0, symbol?: string) {
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) })
  if (status) params.set('status', status)
  if (symbol) params.set('symbol', symbol)
  const query = params.toString()
  return useQuery({
    queryKey: ['orders', status ?? 'all', offset, symbol ?? ''],
    queryFn: () => api.get<Order[]>(`/api/orders?${query}`),
  })
}

/** Why a sell order exists.
 *
 * A strategy's ordinary exit and a stop-loss being hit are very different
 * levels of urgency, and they looked identical in the pending list -- both
 * just 賣出 from 策略訊號. The backend has stamped the trigger on the order
 * since the exit scan was written; nothing rendered it.
 */
function TriggerBadge({ order }: { order: Order }) {
  const trigger = order.risk_notes?.trigger
  if (trigger !== 'stop_loss' && trigger !== 'take_profit') return null
  const isStop = trigger === 'stop_loss'
  return (
    <span
      className={`ml-2 rounded px-1.5 py-0.5 text-xs ${
        isStop
          ? 'border border-red-800 bg-red-950/50 text-red-300'
          : 'border border-emerald-800 bg-emerald-950/50 text-emerald-300'
      }`}
    >
      {isStop ? '停損觸發' : '停利觸發'}
    </span>
  )
}

function PendingOrderRow({ order }: { order: Order }) {
  const [fillPrice, setFillPrice] = useState(order.signal_price ?? '')
  // Prefilled with the whole order: filling in full is the common case and
  // should need no typing, while a partial fill is a single edit away.
  const [fillQuantity, setFillQuantity] = useState(order.quantity)
  const queryClient = useQueryClient()

  const confirmMutation = useMutation({
    mutationFn: () =>
      api.post(`/api/orders/${order.id}/confirm`, {
        fill_price: fillPrice,
        quantity: fillQuantity,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['orders'] })
      queryClient.invalidateQueries({ queryKey: ['positions'] })
    },
  })

  const rejectMutation = useMutation({
    mutationFn: () => api.post(`/api/orders/${order.id}/reject`, {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['orders'] }),
  })

  const busy = confirmMutation.isPending || rejectMutation.isPending
  // Caught here rather than at the backend's 422, because the correction is
  // one keystroke away and the round trip is not worth making anyone wait for.
  const quantityFault = fillQuantityFault(fillQuantity, order.quantity)

  return (
    <tr className="border-b border-slate-800">
      <td className="py-2 pr-4 font-medium">{order.symbol}</td>
      <td className={`py-2 pr-4 ${order.side === 'buy' ? 'text-emerald-400' : 'text-red-400'}`}>
        {SIDE_LABEL[order.side]}
        <TriggerBadge order={order} />
      </td>
      <td className="py-2 pr-4">{order.quantity}</td>
      <td className="py-2 pr-4 text-slate-400">{SOURCE_LABEL[order.source]}</td>
      <td className="py-2 pr-4">
        <label htmlFor={`fill-price-${order.id}`} className="sr-only">
          成交價
        </label>
        <input
          id={`fill-price-${order.id}`}
          aria-label="成交價"
          value={fillPrice}
          onChange={(event) => setFillPrice(event.target.value)}
          className="w-24 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-sm"
        />
      </td>
      <td className="py-2 pr-4">
        <label htmlFor={`fill-qty-${order.id}`} className="sr-only">
          成交數量
        </label>
        <input
          id={`fill-qty-${order.id}`}
          aria-label="成交數量"
          value={fillQuantity}
          onChange={(event) => setFillQuantity(event.target.value)}
          className="w-24 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-sm"
        />
      </td>
      <td className="flex flex-wrap items-center gap-2 py-2">
        <button
          disabled={busy || !fillPrice || quantityFault !== null}
          onClick={() => confirmMutation.mutate()}
          className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          確認
        </button>
        <button
          disabled={busy}
          onClick={() => rejectMutation.mutate()}
          className="rounded bg-slate-700 px-3 py-1 text-sm font-medium text-white hover:bg-slate-600 disabled:opacity-50"
        >
          拒絕
        </button>
        {quantityFault !== null ? (
          <p role="alert" className="text-xs text-amber-300">
            {quantityFault}
          </p>
        ) : (
          <ActionError error={confirmMutation.error ?? rejectMutation.error} />
        )}
      </td>
    </tr>
  )
}

/** null when the typed quantity is usable. Over-filling is the one that
 * matters: the backend would accept it and grow the position past what the
 * broker actually delivered. */
function fillQuantityFault(typed: string, ordered: string): string | null {
  const value = Number(typed)
  if (typed.trim() === '' || Number.isNaN(value) || value <= 0) return '請填成交數量'
  if (value > Number(ordered)) return `成交數量不能超過委託數量 ${ordered}`
  return null
}

function NewOrderForm({ onDone }: { onDone: () => void }) {
  const [symbol, setSymbol] = useState('')
  const [side, setSide] = useState<OrderSide>('buy')
  const [quantity, setQuantity] = useState('')
  const [signalPrice, setSignalPrice] = useState('')
  const [error, setError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const createMutation = useMutation({
    mutationFn: () =>
      api.post<Order>('/api/orders', {
        symbol,
        side,
        quantity,
        signal_price: signalPrice || null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['orders'] })
      onDone()
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : '建立失敗'),
  })

  return (
    <div className="space-y-3 rounded border border-slate-800 p-4">
      <div>
        <label htmlFor="new-order-symbol" className="text-sm text-slate-400">
          代號
        </label>
        <input
          id="new-order-symbol"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
      </div>
      <div className="flex gap-4">
        {(['buy', 'sell'] as const).map((value) => (
          <label key={value} className="flex items-center gap-1 text-sm">
            <input
              type="radio"
              name="new-order-side"
              checked={side === value}
              onChange={() => setSide(value)}
            />
            {SIDE_LABEL[value]}
          </label>
        ))}
      </div>
      <div>
        <label htmlFor="new-order-quantity" className="text-sm text-slate-400">
          數量
        </label>
        <input
          id="new-order-quantity"
          value={quantity}
          onChange={(e) => setQuantity(e.target.value)}
          className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
      </div>
      <div>
        <label htmlFor="new-order-price" className="text-sm text-slate-400">
          預期價格（選填）
        </label>
        <input
          id="new-order-price"
          value={signalPrice}
          onChange={(e) => setSignalPrice(e.target.value)}
          className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
      </div>

      {error && <p className="text-red-400">{error}</p>}

      <button
        disabled={createMutation.isPending || !symbol || !quantity}
        onClick={() => createMutation.mutate()}
        className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
      >
        送出
      </button>
    </div>
  )
}

// A confirmation may fill less than was asked for, and that smaller number is
// what actually reached the position. Showing the order quantity alone made a
// 10-share buy that delivered 2 read as a completed 10.
function filledLabel(order: Order): string {
  if (order.filled_quantity === null) return order.quantity
  if (Number(order.filled_quantity) === Number(order.quantity)) return order.quantity
  return `${order.filled_quantity} / ${order.quantity}`
}

function HistoryRow({ order }: { order: Order }) {
  const queryClient = useQueryClient()
  const deleteMutation = useMutation({
    mutationFn: () => api.delete(`/api/orders/${order.id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['orders'] }),
  })

  // Confirmed and pending rows are refused by the backend on purpose -- a
  // confirmed order moved a position and is counted by the capital gate --
  // so the button is not offered for them rather than being offered and
  // failing.
  const deletable = order.status !== 'confirmed' && order.status !== 'pending'

  return (
    <tr className="border-b border-slate-800 text-slate-300">
      <td className="py-2 pr-4 font-medium">{order.symbol}</td>
      <td className="py-2 pr-4">{SIDE_LABEL[order.side]}</td>
      <td className="py-2 pr-4" data-cell="quantity">
        {filledLabel(order)}
      </td>
      <td className="py-2 pr-4">{order.fill_price ?? '—'}</td>
      <td className="py-2 pr-4">
        {STATUS_LABEL[order.status]}
        <TriggerBadge order={order} />
        {/* 已拒絕 on its own does not say whether the owner pressed it or a
            risk gate did, which is the only part worth knowing. */}
        {order.reject_reason && (
          <p className="max-w-xs text-xs text-slate-500">{order.reject_reason}</p>
        )}
      </td>
      <td className="py-2 pr-4 text-slate-500">
        {new Date(order.created_at).toLocaleString()}
      </td>
      <td className="py-2">
        {deletable ? (
          <DeleteButton
            what={`${order.symbol} 這筆${STATUS_LABEL[order.status]}訂單`}
            onConfirm={() => deleteMutation.mutate()}
            pending={deleteMutation.isPending}
            error={deleteMutation.error}
          />
        ) : (
          <span className="text-xs text-slate-600" title="已成交的訂單動到持倉，刪掉帳目會對不起來">
            —
          </span>
        )}
      </td>
    </tr>
  )
}

export function OrdersPage() {
  const [tab, setTab] = useState<'pending' | 'history'>('pending')
  const [showForm, setShowForm] = useState(false)
  const queryClient = useQueryClient()
  const clearHistory = useMutation({
    mutationFn: () => api.delete<{ deleted: number }>('/api/orders'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['orders'] }),
  })
  const [historyOffset, setHistoryOffset] = useState(0)
  const [symbolFilter, setSymbolFilter] = useState('')
  const pendingQuery = useOrdersQuery('pending')
  const historyQuery = useOrdersQuery(undefined, historyOffset, symbolFilter || undefined)

  // Filtered client-side only to drop the pending rows, which the history tab
  // shows separately. Everything else -- the page window, the symbol -- is
  // done by the backend, so a page is a page rather than whatever survives a
  // local filter.
  const history = (historyQuery.data ?? []).filter((o) => o.status !== 'pending')

  const activeQuery = tab === 'pending' ? pendingQuery : historyQuery

  return (
    <div className="space-y-4">
      {activeQuery.isError && (
        <QueryError error={activeQuery.error} onRetry={() => activeQuery.refetch()} />
      )}

      <div className="flex items-center justify-between">
        <div className="flex gap-4 border-b border-slate-800">
          <button
            onClick={() => setTab('pending')}
            className={`pb-2 text-sm font-medium ${tab === 'pending' ? 'border-b-2 border-emerald-400 text-emerald-400' : 'text-slate-400'}`}
          >
            待確認
          </button>
          <button
            onClick={() => setTab('history')}
            className={`pb-2 text-sm font-medium ${tab === 'history' ? 'border-b-2 border-emerald-400 text-emerald-400' : 'text-slate-400'}`}
          >
            歷史紀錄
          </button>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500"
        >
          新增訂單
        </button>
      </div>

      {showForm && <NewOrderForm onDone={() => setShowForm(false)} />}

      {tab === 'pending' && (
        <table className="w-full text-left text-sm">
          <thead className="text-slate-500">
            <tr>
              <th className="pb-2 font-normal">代號</th>
              <th className="pb-2 font-normal">方向</th>
              <th className="pb-2 font-normal">委託數量</th>
              <th className="pb-2 font-normal">來源</th>
              <th className="pb-2 font-normal">成交價</th>
              <th className="pb-2 font-normal">成交數量</th>
              <th className="pb-2 font-normal">操作</th>
            </tr>
          </thead>
          <tbody>
            {(pendingQuery.data ?? []).map((order) => (
              <PendingOrderRow key={order.id} order={order} />
            ))}
          </tbody>
        </table>
      )}
      {tab === 'pending' && pendingQuery.data?.length === 0 && (
        <p className="text-slate-500">目前沒有待確認訂單。</p>
      )}

      {tab === 'history' && (
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor="orders-symbol-filter" className="text-sm text-slate-400">
              只看某一檔
            </label>
            <input
              id="orders-symbol-filter"
              value={symbolFilter}
              onChange={(e) => {
                setSymbolFilter(e.target.value.toUpperCase())
                // A filter that kept the old page number would show an empty
                // page 3 of a one-page result.
                setHistoryOffset(0)
              }}
              placeholder="全部"
              className="block w-40 rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
          {/* Every May there is a tax return, and the history page shows
              fifty rows. Reading them off the screen was the only way to
              produce a year's fills. */}
          <ExportButton resource="orders" label="匯出 CSV" />
          <DeleteButton
            what="全部已結束的訂單紀錄（已成交與待確認的不會被刪）"
            label="清空歷史"
            tone="loud"
            onConfirm={() => clearHistory.mutate()}
            pending={clearHistory.isPending}
            error={clearHistory.error}
          />
        </div>
      )}

      {tab === 'history' && (
        <table className="w-full text-left text-sm">
          <thead className="text-slate-500">
            <tr>
              <th className="pb-2 font-normal">代號</th>
              <th className="pb-2 font-normal">方向</th>
              <th className="pb-2 font-normal">數量</th>
              <th className="pb-2 font-normal">成交價</th>
              <th className="pb-2 font-normal">狀態</th>
              <th className="pb-2 font-normal">建立時間</th>
              <th className="pb-2 font-normal">操作</th>
            </tr>
          </thead>
          <tbody>
            {history.map((order) => (
              <HistoryRow key={order.id} order={order} />
            ))}
          </tbody>
        </table>
      )}
      {tab === 'history' && (
        <Pager
          offset={historyOffset}
          pageSize={PAGE_SIZE}
          shown={historyQuery.data?.length ?? 0}
          onChange={setHistoryOffset}
        />
      )}
    </div>
  )
}
