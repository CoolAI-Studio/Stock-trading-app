import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../lib/api'
import { maskPersonalUrl } from '../lib/tradingviewUrl'
import type { WebhookSetup } from '../lib/types'

/**
 * 貼進 TradingView 的東西：一條網址、一段訊息，都有複製按鈕（#115）。
 *
 * ＊ 網址本身就是密碼。
 *
 * 規格（ONBOARDING.md 方案 4）要的是「直接複製就能用，不用去別的地方拿」。而一個直接
 * 複製就能用的憑證，也是一個截圖、螢幕分享就會被帶走的憑證——所以畫面上遮住最後那一
 * 段，複製按鈕給的是完整的那一條。他不需要看懂它，只需要貼得過去。
 *
 * ＊ 「重新產生」只放在 TradingView 頁。
 *
 * 引導裡是第一次設定的人，他不需要一顆按了會讓東西失效的按鈕。真的外洩的時候，他會在
 * 那一頁處理——旁邊就是收件紀錄，還在打舊網址的警報會出現在那裡。
 */
export function TradingViewSetupPanel({
  setup,
  allowRegenerate = false,
}: {
  setup: WebhookSetup
  allowRegenerate?: boolean
}) {
  const queryClient = useQueryClient()
  const [copied, setCopied] = useState<'url' | 'message' | null>(null)
  const [copyFailed, setCopyFailed] = useState(false)
  const [revealed, setRevealed] = useState(false)
  const [confirming, setConfirming] = useState(false)

  const regenerate = useMutation({
    mutationFn: () => api.post<WebhookSetup>('/api/webhooks/tradingview/setup/rotate'),
    onSuccess: (fresh) => {
      // 同一個快取鍵：這一頁和任何其他顯示這條網址的地方，一起換成新的。
      queryClient.setQueryData(['webhook-setup'], fresh)
      setConfirming(false)
      setRevealed(false)
    },
  })

  async function copy(value: string, which: 'url' | 'message') {
    try {
      await navigator.clipboard.writeText(value)
      setCopyFailed(false)
      setCopied(which)
      setTimeout(() => setCopied(null), 2000)
    } catch {
      // 不是 https、或瀏覽器擋掉剪貼簿的時候會走到這裡。說出另一條路——一顆按了看起來
      // 沒反應的按鈕，會讓他以為已經複製好了，然後貼上一段舊的東西。
      setCopied(null)
      setCopyFailed(true)
    }
  }

  return (
    <section className="space-y-3 rounded border border-slate-800 p-4">
      <h2 className="text-sm font-semibold text-slate-300">怎麼設定</h2>

      <div className="space-y-1">
        <p className="text-sm text-slate-400">Webhook URL（你這個帳號專屬的）</p>
        <div className="flex flex-wrap items-center gap-2">
          <code className="break-all rounded bg-slate-950 px-2 py-1 text-xs">
            {revealed ? setup.url : maskPersonalUrl(setup.url)}
          </code>
          <button
            onClick={() => copy(setup.url, 'url')}
            className="rounded bg-slate-700 px-2 py-1 text-xs font-medium text-white hover:bg-slate-600"
          >
            {copied === 'url' ? '已複製' : '複製網址'}
          </button>
          <button
            onClick={() => setRevealed((shown) => !shown)}
            className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:border-slate-500"
          >
            {revealed ? '遮起來' : '顯示完整網址'}
          </button>
        </div>
        <p className="text-xs text-slate-500">
          這條網址本身就是密碼：拿到它的人可以替你送訊號。畫面上先遮起來，複製的是完整的。
        </p>
        {copyFailed && (
          <p className="text-xs text-amber-300">
            複製不了（這個瀏覽器不讓網頁用剪貼簿）。按「顯示完整網址」，自己選取複製也可以。
          </p>
        )}
      </div>

      <div>
        <p className="text-sm text-slate-400">警報訊息</p>
        <pre className="overflow-x-auto rounded bg-slate-950 p-2 text-xs text-slate-300">
          {setup.example_message}
        </pre>
        <button
          onClick={() => copy(setup.example_message, 'message')}
          className="mt-1 rounded bg-slate-700 px-2 py-1 text-xs font-medium text-white hover:bg-slate-600"
        >
          {copied === 'message' ? '已複製' : '複製'}
        </button>
      </div>

      <ul className="list-inside list-disc space-y-1 text-xs text-slate-500">
        {setup.notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>

      {allowRegenerate && (
        <div className="space-y-2 border-t border-slate-800 pt-3">
          {confirming ? (
            <div className="space-y-2 rounded border border-amber-700 bg-amber-950/40 p-3 text-sm text-amber-200">
              <p>
                重新產生之後，<strong>舊網址會立刻失效</strong>——TradingView 裡每一則還用舊網址的
                警報都要換成新的，否則它們不會再送進來。還在打舊網址的，會記在下面的收件紀錄裡。
              </p>
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={() => regenerate.mutate()}
                  disabled={regenerate.isPending}
                  className="rounded bg-amber-600 px-3 py-1 font-medium text-white hover:bg-amber-500 disabled:opacity-50"
                >
                  {regenerate.isPending ? '產生中…' : '確定重新產生'}
                </button>
                <button
                  onClick={() => setConfirming(false)}
                  className="rounded border border-slate-600 px-3 py-1 text-slate-200 hover:border-slate-400"
                >
                  取消
                </button>
              </div>
            </div>
          ) : (
            <button
              onClick={() => setConfirming(true)}
              className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:border-slate-500"
            >
              重新產生網址
            </button>
          )}
          {regenerate.isError && (
            <p className="text-sm text-red-400">重新產生失敗，舊網址仍然有效。請再試一次。</p>
          )}
        </div>
      )}
    </section>
  )
}
