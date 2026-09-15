import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../lib/api'
import type { SharedWebhookState } from '../lib/types'

const PATH = '/api/webhooks/tradingview/shared'
const DAY_MS = 24 * 60 * 60 * 1000
// 這麼近還收過訊號，就不是「可能還有」而是「一定還有」警報在用它。
const RECENT_DAYS = 7

/**
 * 舊的共用網址（訊息裡帶 secret 的那一條）的開關（#115 的後續）。
 *
 * 舊警報的訊息裡明文寫著共用密碼，會跟著 TradingView 的通知信、截圖流出去。每一則警報
 * 都換成專屬網址之後把它關掉，外洩的舊密碼就沒用了。
 *
 * 但它不能自動關：三個月前設好、早就忘了的警報還打在它上面（#50）。所以這一格要說得出
 * 他決定時需要的那一件事——**還有沒有東西在用它**——而最近還收過訊號的時候，確認那一步
 * 要把這件事講在最前面。重新打開不用確認：打開不會讓任何東西停掉。
 */
export function SharedWebhookPanel() {
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const state = useQuery({
    queryKey: ['webhook-shared'],
    queryFn: () => api.get<SharedWebhookState>(PATH),
    retry: false,
  })
  const change = useMutation({
    mutationFn: (enabled: boolean) => api.put<SharedWebhookState>(PATH, { enabled }),
    onSuccess: (fresh) => {
      queryClient.setQueryData(['webhook-shared'], fresh)
      // 設定說明裡「舊的警報照樣有效」那一句跟著這個開關變。
      queryClient.invalidateQueries({ queryKey: ['webhook-setup'] })
      setConfirming(false)
    },
  })

  const data = state.data
  // 後端比這個畫面舊（還沒有這支端點）、或回來的不是這個形狀：這一格不出現，整頁照常。
  if (!data || typeof data.enabled !== 'boolean') return null

  const lastUsed = data.last_used_at ? new Date(data.last_used_at) : null
  const daysAgo = lastUsed ? Math.floor((Date.now() - lastUsed.getTime()) / DAY_MS) : null
  const recentlyUsed = daysAgo !== null && daysAgo < RECENT_DAYS

  return (
    <section aria-label="舊的共用網址" className="space-y-2 rounded border border-slate-800 p-4 text-sm">
      <h2 className="text-sm font-semibold text-slate-300">舊的共用網址（訊息裡帶 secret 的警報）</h2>

      {data.enabled ? (
        <p className="text-slate-300">
          開著。以前照舊說明設定、訊息裡有 secret 那一行的警報，打的是這一條。
        </p>
      ) : (
        <p className="text-slate-300">
          已經關掉。訊息裡帶 secret 的警報不會再進來，被擋下來的會記在下面的收件紀錄裡。
        </p>
      )}
      <p className="text-xs text-slate-500">
        {lastUsed
          ? `最後一次收到：${lastUsed.toLocaleString()}`
          : '這一版更新之後，還沒有收到過任何訊號。'}
      </p>

      {data.enabled && !confirming && (
        <div className="space-y-2">
          <p className="text-xs text-slate-500">
            每一則警報都換成上面的專屬網址之後，就可以把它關掉：之後舊警報訊息裡的 secret
            就算外洩了，也沒有用。
          </p>
          <button
            onClick={() => setConfirming(true)}
            className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:border-slate-500"
          >
            關掉舊的共用網址
          </button>
        </div>
      )}

      {data.enabled && confirming && (
        <div className="space-y-2 rounded border border-amber-700 bg-amber-950/40 p-3 text-amber-200">
          {recentlyUsed && (
            <p>
              <strong>它{daysAgo === 0 ? '今天' : ` ${daysAgo} 天前`}還收到過訊號。</strong>
              先把那些警報換成專屬網址，否則關掉之後它們就不會再響。
            </p>
          )}
          <p>關掉之後，訊息裡帶 secret 的警報不會再進來。隨時可以回來重新打開。</p>
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => change.mutate(false)}
              disabled={change.isPending}
              className="rounded bg-amber-600 px-3 py-1 font-medium text-white hover:bg-amber-500 disabled:opacity-50"
            >
              確定關掉
            </button>
            <button
              onClick={() => setConfirming(false)}
              className="rounded border border-slate-600 px-3 py-1 text-slate-200 hover:border-slate-400"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {!data.enabled && (
        <button
          onClick={() => change.mutate(true)}
          disabled={change.isPending}
          className="rounded bg-slate-700 px-3 py-1 text-xs font-medium text-white hover:bg-slate-600 disabled:opacity-50"
        >
          重新打開
        </button>
      )}

      {change.isError && <p className="text-xs text-red-400">沒有存起來，請再按一次。</p>}
    </section>
  )
}
