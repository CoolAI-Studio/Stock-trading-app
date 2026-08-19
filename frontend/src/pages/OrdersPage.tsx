import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from '../lib/api'
import { ActionError } from '../components/ActionError'
import { DeleteButton } from '../components/DeleteButton'
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

function useOrdersQuery(status?: string) {
  return useQuery({
    queryKey: ['orders', status ?? 'all'],
    queryFn: () => api.get<Order[]>(`/api/orders${status ? `?status=${status}` : ''}`),
  })
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
      <td className="py-2 pr-4">{STATUS_LABEL[order.status]}</td>
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
  const pendingQuery = useOrdersQuery('pending')
  const historyQuery = useOrdersQuery()

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
    </div>
  )
}
