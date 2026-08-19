import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { TradingViewWidget } from '../components/TradingViewWidget'
import { QueryError } from '../components/QueryError'
import type { DataSource, Order, Position, Quote, Strategy, WatchlistItem } from '../lib/types'

/** The old browser-local list. Read once, uploaded, then removed -- without
 * this the owner's existing watch list simply vanishes on the deploy that
 * moves it into the database, which is a bad way to learn it used to be
 * local. */
const LEGACY_WATCHLIST_KEY = 'trading_app_watchlist'

function readLegacyWatchlist(): string[] {
  try {
    const raw = localStorage.getItem(LEGACY_WATCHLIST_KEY)
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

  const [searchInput, setSearchInput] = useState('')
  const queryClient = useQueryClient()

  const watchlistQuery = useQuery({
    queryKey: ['watchlist'],
    queryFn: () => api.get<WatchlistItem[]>('/api/watchlist'),
  })
  const watchlist = useMemo(
    () => (watchlistQuery.data ?? []).map((item) => item.symbol),
    [watchlistQuery.data],
  )

  const addMutation = useMutation({
    mutationFn: (symbol: string) => api.post('/api/watchlist', { symbol }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['watchlist'] }),
  })
  const removeMutation = useMutation({
    mutationFn: (symbol: string) => api.delete(`/api/watchlist/${symbol}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['watchlist'] }),
  })

  // One-time move of whatever the browser was holding. Runs after the server
  // list has loaded so it cannot race it, and clears the key afterwards so a
  // symbol the owner later removes does not come back on the next visit.
  useEffect(() => {
    if (!watchlistQuery.isSuccess) return
    const legacy = readLegacyWatchlist()
    if (legacy.length === 0) return
    const known = new Set((watchlistQuery.data ?? []).map((item) => item.symbol))
    for (const symbol of legacy) {
      if (!known.has(symbol)) addMutation.mutate(symbol)
    }
    localStorage.removeItem(LEGACY_WATCHLIST_KEY)
    // addMutation is stable for this purpose and including it would re-run
    // the move on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watchlistQuery.isSuccess, watchlistQuery.data])

  const symbols = useMemo(() => {
    const set = new Set<string>()
    for (const s of strategiesQuery.data ?? []) set.add(s.symbol)
    for (const p of positionsQuery.data ?? []) set.add(p.symbol)
    for (const w of watchlist) set.add(w)
    return Array.from(set)
  }, [strategiesQuery.data, positionsQuery.data, watchlist])

  const [selectedSymbol, setSelectedSymbol] = useState('AAPL')

  // Which provider each symbol belongs to, so the chart can be asked for the
  // right exchange. Only Binance actually needs it -- the Taiwan boards are
  // decided by the suffix -- but reading the real value beats guessing crypto
  // from a symbol that happens to end in USDT.
  const sourceOf = useMemo(() => {
    const map = new Map<string, DataSource>()
    for (const item of watchlistQuery.data ?? []) map.set(item.symbol, item.data_source)
    for (const s of strategiesQuery.data ?? []) map.set(s.symbol, s.data_source)
    return map
  }, [watchlistQuery.data, strategiesQuery.data])

  const quotesQuery = useQuery({
    queryKey: ['market-quotes', symbols.join(',')],
    queryFn: () => api.get<Quote[]>(`/api/market/quote?symbols=${symbols.join(',')}`),
    enabled: symbols.length > 0,
  })

  function addToWatchlist(rawSymbol: string) {
    const symbol = rawSymbol.trim().toUpperCase()
    if (!symbol) return
    addMutation.mutate(symbol)
    setSelectedSymbol(symbol)
    setSearchInput('')
  }

  function removeFromWatchlist(symbol: string) {
    removeMutation.mutate(symbol)
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

      {/* Three across is 120px per card on a phone, which wraps every
          number onto four lines. One column until there is room. */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="待確認訂單" value={pendingQuery.data?.length ?? 0} />
        <StatCard label="持有部位" value={positionsQuery.data?.length ?? 0} />
        <StatCard
          label="啟用中策略"
          value={(strategiesQuery.data ?? []).filter((s) => s.is_active).length}
        />
      </div>

      <TradingViewWidget symbol={selectedSymbol} dataSource={sourceOf.get(selectedSymbol)} />

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

      <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0 [&_th]:whitespace-nowrap">
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
    </div>
  )
}
