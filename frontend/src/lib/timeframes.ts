/**
 * Which candle sizes each data source serves, asked of the server.
 *
 * NOT a list in this file. The set differs by source -- Yahoo refuses 12h
 * outright while Binance serves it -- and a second list in TypeScript would
 * drift the first time an interval is added. The drift would show up as a
 * button that answers 「暫時抓不到…可能是被限流了」 for a candle that was never
 * available: a transient sentence for a permanent condition, which is the
 * confusion this codebase keeps having to fix.
 *
 * The server declares it in services/market_data/base.py (SUPPORTED_TIMEFRAMES)
 * and refuses an unsupported pair with a sentence, so this is the same answer
 * twice over rather than two answers.
 */

import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import type { DataSource, TimeframesResponse } from './types'

/** Shown while the server has not answered yet.
 *
 * Daily only, and deliberately: it is the one interval every source serves, so
 * the chart can draw something immediately without the risk of offering a
 * candle the selected symbol turns out not to have. The full set replaces it a
 * moment later.
 */
export const FALLBACK_TIMEFRAME = { value: '1d', label: '日線', max_bars: 0 }

export function useTimeframes(dataSource?: DataSource) {
  const query = useQuery({
    queryKey: ['timeframes'],
    // Compiled into the backend; it cannot change while the page is open, and
    // refetching it is pure cost on a free dyno.
    staleTime: Infinity,
    queryFn: () => api.get<TimeframesResponse>('/api/market/timeframes'),
  })

  // Default to yfinance rather than to 「everything」: an undefined source means
  // a symbol whose provider the page has not resolved yet, and offering the
  // union would put 12h in front of somebody looking at a stock.
  const wanted = dataSource ?? 'yfinance'
  // Every step optional-chained. A backend older than this page answers 404 --
  // handled -- but a proxy, or a partially-deployed backend, can answer 200
  // with a shape this page does not know. Reaching into it would throw during
  // render and take the whole dashboard down over a row of buttons.
  const options =
    query.data?.sources?.find?.((entry) => entry?.data_source === wanted)?.timeframes ?? []

  return {
    options: options.length ? options : [FALLBACK_TIMEFRAME],
    isPending: query.isPending,
    isError: query.isError,
    /** Whether this source serves this candle at all. */
    supports: (value: string) => options.some((option) => option.value === value),
  }
}
