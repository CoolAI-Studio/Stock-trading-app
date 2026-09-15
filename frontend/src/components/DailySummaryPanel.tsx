import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import type { DailySummaryState } from '../lib/types'

/**
 * 收盤摘要的開關（ONBOARDING.md 方案 2，#117）。
 *
 * 清單就是上面的自選股，所以這一格不收代號——但它要說得出打開之後會發生什麼，否則他打開了
 * 也無從判斷有沒有用：每個市場會摘要哪幾檔、哪一則根本不會送、哪幾檔永遠不會出現、上一次
 * 送出是什麼時候、沒送成又是為什麼——以及整理好了**有沒有地方送**。
 */
export function DailySummaryPanel() {
  const queryClient = useQueryClient()
  const state = useQuery({
    queryKey: ['daily-summary'],
    queryFn: () => api.get<DailySummaryState>('/api/daily-summary'),
    retry: false,
  })
  const toggle = useMutation({
    mutationFn: (enabled: boolean) =>
      api.put<DailySummaryState>('/api/daily-summary', { is_enabled: enabled }),
    onSuccess: (fresh) => queryClient.setQueryData(['daily-summary'], fresh),
  })

  const data = state.data
  // 後端比這個畫面舊（還沒有這支端點）、或回來的不是這個形狀：這一格就不出現，而不是把整個
  // 儀表板一起弄壞。
  if (!data || !Array.isArray(data.markets)) return null

  // 通知頁在沒有全勾的時候存的是一份清單，而收盤摘要出現之前存的清單裡不會有它。沒有這兩句，
  // 他打開了、每天都「送出」、一則都沒收到，而這一格上沒有任何一個字不對勁。
  // 後端還不會回 `channels` 的話就不說——不知道，不等於沒有。
  const channels = data.is_enabled ? data.channels : undefined

  return (
    <section aria-label="收盤摘要" className="space-y-2 rounded border border-slate-800 p-4 text-sm">
      <label className="flex items-center gap-2 font-medium text-slate-200">
        <input
          type="checkbox"
          checked={data.is_enabled}
          disabled={toggle.isPending}
          onChange={(event) => toggle.mutate(event.target.checked)}
        />
        收盤後傳一則自選股的漲跌摘要
      </label>
      <p className="text-xs text-slate-500">
        一個市場一天一則：台股 13:30、美股 16:00（紐約時間）收盤後大約半小時內送出。休市日不送；
        設了勿擾時段的管道，會等勿擾結束才送。
      </p>
      {channels && channels.enabled === 0 && (
        <p className="text-xs text-amber-300">
          還沒有任何通知管道，摘要整理好了也沒有地方送。
          <Link to="/notifications" className="ml-1 underline hover:text-amber-100">
            設定通知管道
          </Link>
        </p>
      )}
      {channels && channels.enabled > 0 && channels.receiving === 0 && (
        <p className="text-xs text-amber-300">
          你的通知管道都沒有勾「每日收盤摘要」，所以這一則不會送到。
          <Link to="/notifications" className="ml-1 underline hover:text-amber-100">
            到通知頁勾起來
          </Link>
        </p>
      )}
      <ul className="space-y-1 text-slate-300">
        {data.markets.map((market) => (
          <li key={market.market}>
            {market.label}：
            {market.symbols.length > 0
              ? market.symbols.join('、')
              : `自選股裡沒有${market.label}，這一則不會送`}
          </li>
        ))}
      </ul>
      {data.unsupported.length > 0 && (
        <p className="text-xs text-slate-500">
          {data.unsupported.join('、')} 不收盤，不會出現在任何一則摘要裡。
        </p>
      )}
      {data.last_sent_at && (
        <p className="text-xs text-slate-500">
          上一次送出：{new Date(data.last_sent_at).toLocaleString()}
        </p>
      )}
      {data.last_error && <p className="text-xs text-amber-300">{data.last_error}</p>}
      {toggle.isError && <p className="text-xs text-red-400">沒有存起來，請再按一次。</p>}
    </section>
  )
}
