import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { useWebSocket } from '../lib/useWebSocket'
import { TradingViewWidget } from '../components/TradingViewWidget'
import type { Order, Position, Quote, Strategy } from '../lib/types'

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded border border-slate-800 p-4">
      <p className="text-sm text-slate-500">{label}</p>
      <p className="text-2xl font-semibold">{value}</p>
    </div>
  )
}

export function DashboardPage() {
  useWebSocket(true)

  const strategiesQuery = useQuery({
    queryKey: ['strategies'],
    queryFn: () => api.get<Strategy[]>('/api/strategies'),
  })
  const positionsQuery = useQuery({
    queryKey: ['positions'],
    queryFn: () => api.get<Position[]>('/api/positions'),
  })
  const pendingQuery = useQuery({
    queryKey: ['orders', 'pending'],
    queryFn: () => api.get<Order[]>('/api/orders?status=pending'),
  })

  const symbols = useMemo(() => {
    const set = new Set<string>()
    for (const s of strategiesQuery.data ?? []) set.add(s.symbol)
    for (const p of positionsQuery.data ?? []) set.add(p.symbol)
    return Array.from(set)
  }, [strategiesQuery.data, positionsQuery.data])

  const [selectedSymbol, setSelectedSymbol] = useState('AAPL')

  const quotesQuery = useQuery({
    queryKey: ['market-quotes', symbols.join(',')],
    queryFn: () => api.get<Quote[]>(`/api/market/quote?symbols=${symbols.join(',')}`),
    enabled: symbols.length > 0,
  })

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-3 gap-4">
        <StatCard label="Pending orders" value={pendingQuery.data?.length ?? 0} />
        <StatCard label="Open positions" value={positionsQuery.data?.length ?? 0} />
        <StatCard
          label="Active strategies"
          value={(strategiesQuery.data ?? []).filter((s) => s.is_active).length}
        />
      </div>

      <TradingViewWidget symbol={selectedSymbol} />

      <table className="w-full text-left text-sm">
        <thead className="text-slate-500">
          <tr>
            <th className="pb-2 font-normal">Symbol</th>
            <th className="pb-2 font-normal">Price</th>
            <th className="pb-2 font-normal">Change %</th>
          </tr>
        </thead>
        <tbody>
          {(quotesQuery.data ?? []).map((quote) => (
            <tr
              key={quote.symbol}
              onClick={() => setSelectedSymbol(quote.symbol)}
              className="cursor-pointer border-b border-slate-800 hover:bg-slate-900"
            >
              <td className="py-2 pr-4 font-medium">{quote.symbol}</td>
              <td className="py-2 pr-4">{quote.price}</td>
              <td
                className={`py-2 pr-4 ${Number(quote.change_pct ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}
              >
                {quote.change_pct ?? '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
