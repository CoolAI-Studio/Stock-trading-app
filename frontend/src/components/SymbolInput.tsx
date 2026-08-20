import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { SymbolMatch, SymbolSearchResponse } from '../lib/types'

/**
 * A symbol box that tells you what to put in it.
 *
 * Every symbol field in this app was free text that had to be filled with
 * 「2330.TW」 by somebody who already knew that was the required form. The
 * owner's words: 「輸入欄位使用者不會知道要如何填，通常是打台積電或2330這種代表
 * 性指標」.
 *
 * Both natural inputs failed, and both failed quietly. 「台積電」 was accepted,
 * stored, and then requested from Yahoo on every poll -- no quote, no row, no
 * error, and no alert, from a watchlist entry that looked correctly set up.
 * A bare 「2330」 is worse: Yahoo resolves it to an unrelated Japanese OTC
 * company, so it prices, and the owner watches the wrong stock with complete
 * confidence.
 *
 * SUGGEST, NEVER SUBSTITUTE. This will not quietly rewrite what was typed. It
 * offers candidates; the person picks one. Until they do, what is in the box
 * is what would be submitted -- with a warning when that cannot work. For an
 * alerting product, a field that silently resolves to the wrong company is
 * worse than one that refuses the input.
 */

// Below this, a query matches most of the market and the list is noise. Two
// characters is enough for a Chinese name (台積) and for a code prefix (23).
const MIN_QUERY = 2

// Long enough that typing 台積電 is one request rather than three, short enough
// that the list feels attached to the keyboard.
const DEBOUNCE_MS = 250

/** Whether what is currently typed can ever produce a price.
 *
 * Mirrors services/symbol_search.looks_unpriceable -- deliberately, because
 * the server refuses these too and the box should say so before the submit
 * rather than after it. The server remains the authority; this is only there
 * so the answer arrives while the person is still looking at the field.
 */
function unpriceableReason(value: string): string | null {
  const text = value.trim()
  if (!text) return null

  // eslint-disable-next-line no-control-regex
  if (!/^[\x00-\x7F]*$/.test(text)) {
    return '這是公司名稱，不是代號。請從下面的搜尋結果選一個，例如台積電要用 2330.TW。'
  }
  if (/^\d{4,6}$/.test(text)) {
    return `台股代號要帶市場後綴，只寫「${text}」會被行情來源當成其他市場的股票。請從下面選出 ${text}.TW 或 ${text}.TWO。`
  }
  return null
}

export function SymbolInput({
  id,
  label,
  value,
  onChange,
  placeholder = '代號或公司名稱，例如 2330.TW、台積電、AAPL',
  hint,
}: {
  id: string
  label: string
  value: string
  onChange: (symbol: string, match?: SymbolMatch) => void
  placeholder?: string
  hint?: string
}) {
  const [query, setQuery] = useState(value)
  const [debounced, setDebounced] = useState(value)
  // Closed after a pick, so choosing 2330.TW does not immediately re-open the
  // list showing 2330.TW as a suggestion for itself.
  const [open, setOpen] = useState(false)

  // Kept in step when a parent resets the form; the box is otherwise its own
  // source of truth while somebody is typing in it.
  useEffect(() => setQuery(value), [value])

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query), DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [query])

  const trimmed = debounced.trim()
  const searchQuery = useQuery({
    queryKey: ['symbol-search', trimmed],
    queryFn: () =>
      api.get<SymbolSearchResponse>(`/api/symbols/search?q=${encodeURIComponent(trimmed)}`),
    enabled: open && trimmed.length >= MIN_QUERY,
    // The bundled table only changes when somebody reruns the refresh script,
    // so re-asking for the same string is pure waste on a free-tier box.
    staleTime: 5 * 60 * 1000,
  })

  const matches = searchQuery.data?.matches ?? []
  const problem = unpriceableReason(query)

  function pick(match: SymbolMatch) {
    setOpen(false)
    setQuery(match.symbol)
    onChange(match.symbol, match)
  }

  return (
    <div className="relative">
      <label htmlFor={id} className="text-sm text-slate-400">
        {label}
      </label>
      <input
        id={id}
        value={query}
        placeholder={placeholder}
        autoComplete="off"
        onChange={(e) => {
          setQuery(e.target.value)
          setOpen(true)
          onChange(e.target.value)
        }}
        onFocus={() => setOpen(true)}
        className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
      />
      {hint && <p className="mt-0.5 text-xs text-slate-500">{hint}</p>}

      {problem && (
        <p role="alert" className="mt-1 text-xs text-amber-300">
          {problem}
        </p>
      )}

      {open && trimmed.length >= MIN_QUERY && (
        <div className="absolute z-20 mt-1 w-full rounded border border-slate-700 bg-slate-900 shadow-lg">
          {searchQuery.isPending && <p className="p-2 text-xs text-slate-500">搜尋中…</p>}

          {searchQuery.isSuccess && matches.length === 0 && (
            <p className="p-2 text-xs text-slate-400">
              找不到「{trimmed}」。台股請試公司簡稱或四碼代號；美股可以打代號或英文公司名。
              {/* Both dates, because an empty result looked in both tables --
                  and 「找不到」 has three causes of which only one is a typo.
                  Without the dates, 「listed after the table was built」 reads
                  exactly like 「you typed it wrong」, and somebody retypes a
                  stock that exists until they give up on it. */}
              {(searchQuery.data?.listings_generated_at ||
                searchQuery.data?.us_listings_generated_at) && (
                <span className="block text-slate-600">
                  清單更新於
                  {searchQuery.data?.listings_generated_at &&
                    ` 台股 ${searchQuery.data.listings_generated_at}`}
                  {searchQuery.data?.us_listings_generated_at &&
                    ` 美股 ${searchQuery.data.us_listings_generated_at}`}
                  ，比這個日期更晚上市的公司不會在裡面。
                </span>
              )}
            </p>
          )}

          {matches.length > 0 && (
            <ul aria-label="搜尋結果" role="listbox" className="max-h-64 overflow-y-auto">
              {matches.map((match) => (
                <li key={`${match.symbol}-${match.market}`} role="option" aria-selected={false}>
                  <button
                    type="button"
                    onClick={() => pick(match)}
                    className="block w-full px-2 py-1.5 text-left hover:bg-slate-800"
                  >
                    <span className="flex items-baseline gap-2">
                      <span className="font-medium">{match.symbol}</span>
                      <span className="text-sm text-slate-300">{match.name}</span>
                      {/* Market AND currency together. The provider's own name
                          is identical for 2330.TW and TSM, so this pair is the
                          only thing on the row that says 220 means two
                          different numbers. */}
                      <span className="ml-auto whitespace-nowrap text-xs text-slate-500">
                        {match.currency ? `${match.market} · ${match.currency}` : match.market}
                      </span>
                    </span>
                    <span className="block text-xs text-slate-500">
                      {match.detail}
                      {/* Said out loud rather than left to the reader: a US
                          ticker is a guess from the shape of the input, not a
                          lookup, and the two must not read alike. */}
                      {!match.verified && <span className="ml-1 text-amber-400">（未核對）</span>}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
