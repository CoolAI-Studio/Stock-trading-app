import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
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
  const queryClient = useQueryClient()

  const confirmMutation = useMutation({
    mutationFn: () => api.post(`/api/orders/${order.id}/confirm`, { fill_price: fillPrice }),
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
      <td className="flex gap-2 py-2">
        <button
          disabled={busy || !fillPrice}
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
      </td>
    </tr>
  )
}

function HistoryRow({ order }: { order: Order }) {
  return (
    <tr className="border-b border-slate-800 text-slate-300">
      <td className="py-2 pr-4 font-medium">{order.symbol}</td>
      <td className="py-2 pr-4">{SIDE_LABEL[order.side]}</td>
      <td className="py-2 pr-4">{order.quantity}</td>
      <td className="py-2 pr-4">{order.fill_price ?? '—'}</td>
      <td className="py-2 pr-4">{STATUS_LABEL[order.status]}</td>
      <td className="py-2 text-slate-500">{new Date(order.created_at).toLocaleString()}</td>
    </tr>
  )
}

export function OrdersPage() {
  const [tab, setTab] = useState<'pending' | 'history'>('pending')
  const pendingQuery = useOrdersQuery('pending')
  const historyQuery = useOrdersQuery()

  const history = (historyQuery.data ?? []).filter((o) => o.status !== 'pending')

  return (
    <div className="space-y-4">
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

      {tab === 'pending' && (
        <table className="w-full text-left text-sm">
          <thead className="text-slate-500">
            <tr>
              <th className="pb-2 font-normal">代號</th>
              <th className="pb-2 font-normal">方向</th>
              <th className="pb-2 font-normal">數量</th>
              <th className="pb-2 font-normal">來源</th>
              <th className="pb-2 font-normal">成交價</th>
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
