import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { TradingViewWidget } from '../components/TradingViewWidget'
import { QueryError } from '../components/QueryError'
import type { Order, Position, Quote, Strategy } from '../lib/types'

const WATCHLIST_KEY = 'trading_app_watchlist'

function loadWatchlist(): string[] {
  try {
    const raw = localStorage.getItem(WATCHLIST_KEY)
    return raw ? (JSON.parse(raw) as string[]) : []
  } catch {
    return []
  }
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded border border-slate-800 p-4">
      <p className="text-sm text-slate-500">{label}</p>
      <p className="text-2xl font-semibold">{value}</p>
    </div>
  )
}

export function DashboardPage() {
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

  const [watchlist, setWatchlist] = useState<string[]>(loadWatchlist)
  const [searchInput, setSearchInput] = useState('')

  useEffect(() => {
    localStorage.setItem(WATCHLIST_KEY, JSON.stringify(watchlist))
  }, [watchlist])

  const symbols = useMemo(() => {
    const set = new Set<string>()
    for (const s of strategiesQuery.data ?? []) set.add(s.symbol)
    for (const p of positionsQuery.data ?? []) set.add(p.symbol)
    for (const w of watchlist) set.add(w)
    return Array.from(set)
  }, [strategiesQuery.data, positionsQuery.data, watchlist])

  const [selectedSymbol, setSelectedSymbol] = useState('AAPL')

  const quotesQuery = useQuery({
    queryKey: ['market-quotes', symbols.join(',')],
    queryFn: () => api.get<Quote[]>(`/api/market/quote?symbols=${symbols.join(',')}`),
    enabled: symbols.length > 0,
  })

  function addToWatchlist(rawSymbol: string) {
    const symbol = rawSymbol.trim().toUpperCase()
    if (!symbol) return
    setWatchlist((prev) => (prev.includes(symbol) ? prev : [...prev, symbol]))
    setSelectedSymbol(symbol)
    setSearchInput('')
  }

  function removeFromWatchlist(symbol: string) {
    setWatchlist((prev) => prev.filter((s) => s !== symbol))
  }

  const failed = [strategiesQuery, positionsQuery, pendingQuery].find((q) => q.isError)

  return (
    <div className="space-y-6">
      {failed && (
        <QueryError
          error={failed.error}
          onRetry={() => {
            strategiesQuery.refetch()
            positionsQuery.refetch()
            pendingQuery.refetch()
          }}
        />
      )}

      <div className="grid grid-cols-3 gap-4">
        <StatCard label="待確認訂單" value={pendingQuery.data?.length ?? 0} />
        <StatCard label="持有部位" value={positionsQuery.data?.length ?? 0} />
        <StatCard
          label="啟用中策略"
          value={(strategiesQuery.data ?? []).filter((s) => s.is_active).length}
        />
      </div>

      <TradingViewWidget symbol={selectedSymbol} />

      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          addToWatchlist(searchInput)
        }}
      >
        <label htmlFor="watch-symbol" className="sr-only">
          查詢代號
        </label>
        <input
          id="watch-symbol"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          placeholder="輸入代號查詢，例如 TSLA"
          className="w-64 rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
        <button
          type="submit"
          disabled={!searchInput.trim()}
          className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          加入自選
        </button>
      </form>

      <table className="w-full text-left text-sm">
        <thead className="text-slate-500">
          <tr>
            <th className="pb-2 font-normal">代號</th>
            <th className="pb-2 font-normal">價格</th>
            <th className="pb-2 font-normal">漲跌幅 %</th>
            <th className="pb-2 font-normal" />
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
              <td className="py-2 text-right">
                {watchlist.includes(quote.symbol) && (
                  <button
                    aria-label={`移除 ${quote.symbol}`}
                    onClick={(e) => {
                      e.stopPropagation()
                      removeFromWatchlist(quote.symbol)
                    }}
                    className="text-slate-500 hover:text-red-400"
                  >
                    ×
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
