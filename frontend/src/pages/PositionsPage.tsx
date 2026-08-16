import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { Position } from '../lib/types'

export function PositionsPage() {
  const positionsQuery = useQuery({
    queryKey: ['positions'],
    queryFn: () => api.get<Position[]>('/api/positions'),
  })

  const positions = positionsQuery.data ?? []

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">部位</h1>

      {positions.length === 0 && positionsQuery.isSuccess && (
        <p className="text-slate-500">目前沒有持有部位。</p>
      )}

      {positions.length > 0 && (
        <table className="w-full text-left text-sm">
          <thead className="text-slate-500">
            <tr>
              <th className="pb-2 font-normal">代號</th>
              <th className="pb-2 font-normal">數量</th>
              <th className="pb-2 font-normal">平均成本</th>
              <th className="pb-2 font-normal">已實現損益</th>
              <th className="pb-2 font-normal">建倉時間</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((position) => (
              <tr key={position.symbol} className="border-b border-slate-800">
                <td className="py-2 pr-4 font-medium">{position.symbol}</td>
                <td className="py-2 pr-4">{position.quantity}</td>
                <td className="py-2 pr-4">{position.avg_entry_price}</td>
                <td
                  className={`py-2 pr-4 ${Number(position.realized_pnl) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}
                >
                  {position.realized_pnl}
                </td>
                <td className="py-2 text-slate-500">
                  {position.opened_at ? new Date(position.opened_at).toLocaleString() : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
