import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, api } from '../lib/api'
import { isPushSupported, subscribeToPush } from '../lib/push'
import type { NotificationChannel } from '../lib/types'

type TabKey = 'database' | 'ai' | 'notifications'

interface SystemShape {
  database?: { kind: string; ephemeral: boolean; status: string; detail: string }
  platform?: { name: string; env_where: string }
}

/**
 * 設定引導：把資料庫接上線、把 AI 的 API 接上線、把通知接上線。
 *
 * WHAT MAKES THIS DIFFERENT FROM A HELP PAGE. 三件事的共同點是「填了不算完成，
 * 通得過才算」，所以每一個分頁上的狀態都是問後端問來的現況，不是畫面上記著的一個
 * 布林值——而每一個分頁都有一個真的會去試的動作（重新檢查、測試 AI、傳一則測試）。
 *
 * 這也是為什麼指示要用「這個部署實際所在的平台」的說法：/api/system/status 會回
 * platform.env_where，而對一個部署在 Fly.io 的人說「Render 後台」，比含糊更糟——
 * 他會真的去找那一頁，然後找不到。
 */
export function SetupGuidePage() {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<TabKey | null>(null)
  const [aiResult, setAiResult] = useState<{ ok: boolean; error?: string | null } | null>(null)
  const [channelResult, setChannelResult] = useState<{ ok: boolean; error?: string | null } | null>(
    null,
  )
  const [pushError, setPushError] = useState<string | null>(null)

  const systemQuery = useQuery({
    queryKey: ['system-status'],
    queryFn: () => api.get<SystemShape>('/api/system/status'),
    retry: false,
  })
  const aiQuery = useQuery({
    queryKey: ['ai-settings'],
    queryFn: () => api.get<{ configured: boolean }>('/api/ai-settings'),
    retry: false,
  })
  const channelsQuery = useQuery({
    queryKey: ['notification-channels'],
    queryFn: () => api.get<NotificationChannel[]>('/api/notifications/channels'),
    retry: false,
  })

  const database = systemQuery.data?.database
  const platform = systemQuery.data?.platform
  const channels = (channelsQuery.data ?? []).filter((channel) => channel.is_enabled)

  const done: Record<TabKey, boolean> = {
    // 本機的檔案資料庫是一個選擇，不是一件沒做完的事。只有「在會被清空的地方
    // 放著一個檔案」才算沒完成。
    database: database ? !database.ephemeral : false,
    ai: aiQuery.data?.configured === true,
    notifications: channels.length > 0,
  }

  // 一打開就停在第一個還沒完成的那一件事。全部完成時停在第一頁。
  const firstUnfinished: TabKey =
    (['database', 'ai', 'notifications'] as TabKey[]).find((key) => !done[key]) ?? 'database'
  const active = tab ?? firstUnfinished

  const testAi = useMutation({
    mutationFn: () => api.post<{ ok: boolean; error?: string | null }>('/api/ai-settings/test', {}),
    onSuccess: (result) => setAiResult(result),
    onError: (err) =>
      setAiResult({
        ok: false,
        error: err instanceof ApiError ? err.message : '問不到，可能是金鑰或網路的問題。',
      }),
  })

  const testChannel = useMutation({
    mutationFn: (id: number) =>
      api.post<{ ok: boolean; error?: string | null }>(`/api/notifications/channels/${id}/test`, {}),
    onSuccess: (result) => setChannelResult(result),
    onError: (err) =>
      setChannelResult({
        ok: false,
        error: err instanceof ApiError ? err.message : '送不出去。',
      }),
  })

  const enablePush = useMutation({
    mutationFn: async () => {
      const { public_key } = await api.get<{ public_key: string }>(
        '/api/notifications/push/vapid-public-key',
      )
      const config = await subscribeToPush(public_key)
      return api.post('/api/notifications/channels', {
        channel_type: 'web_push',
        label: '這台裝置',
        config,
      })
    },
    onSuccess: () => {
      setPushError(null)
      queryClient.invalidateQueries({ queryKey: ['notification-channels'] })
    },
    onError: (err) =>
      setPushError(
        err instanceof ApiError
          ? err.message
          : '這個瀏覽器沒有讓推播開起來。常見原因：拒絕過通知權限、無痕視窗、'
            + '或 iPhone 上還沒把這個網頁「加入主畫面」。也可以改用 Telegram 或 Email。',
      ),
  })

  function tabLabel(key: TabKey, text: string) {
    return `${done[key] ? '✅' : '⭕'} ${text}`
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-slate-100">設定引導</h1>
        <p className="mt-1 text-sm text-slate-400">
          三件事。每一件都不是「填了就算」——這裡的狀態是問系統問來的，而每一頁上的
          按鈕會真的去試一次。
        </p>
      </div>

      <div role="tablist" className="flex flex-wrap gap-2 border-b border-slate-800 pb-2">
        {(
          [
            ['database', '資料庫'],
            ['ai', 'AI 的 API'],
            ['notifications', '通知'],
          ] as [TabKey, string][]
        ).map(([key, text]) => (
          <button
            key={key}
            role="tab"
            aria-selected={active === key}
            onClick={() => setTab(key)}
            className={
              active === key
                ? 'rounded bg-slate-800 px-3 py-1 text-sm font-medium text-slate-100'
                : 'rounded px-3 py-1 text-sm text-slate-400 hover:text-slate-200'
            }
          >
            {tabLabel(key, text)}
          </button>
        ))}
      </div>

      {active === 'database' && (
        <div className="space-y-4">
          <div
            className={
              database?.ephemeral
                ? 'rounded border border-amber-700 bg-amber-950/40 p-3 text-sm text-amber-200'
                : 'rounded border border-emerald-800 bg-emerald-950/40 p-3 text-sm text-emerald-200'
            }
          >
            <p className="font-medium">
              現在的狀況：
              {database?.kind === 'postgres'
                ? 'Postgres'
                : database?.kind === 'sqlite'
                  ? '本機檔案（SQLite）'
                  : '看不出來'}
            </p>
            <p className="mt-1">{database?.detail}</p>
          </div>

          {database?.ephemeral && (
            <ol className="list-inside list-decimal space-y-2 text-sm text-slate-300">
              <li>
                去開一個 Postgres。免費的例如{' '}
                <a href="https://neon.tech" className="underline" target="_blank" rel="noreferrer">
                  Neon
                </a>{' '}
                或{' '}
                <a
                  href="https://supabase.com"
                  className="underline"
                  target="_blank"
                  rel="noreferrer"
                >
                  Supabase
                </a>
                ；付費方案或自己架的一樣可以，這個系統不在乎是誰家的。
              </li>
              <li>複製它給你的連線字串（`postgresql://` 開頭）。</li>
              <li>
                貼進 <strong>DATABASE_URL</strong>：{platform?.env_where}
              </li>
              <li>存檔之後服務會自己重新啟動，然後回到這一頁按「重新檢查」。</li>
            </ol>
          )}

          {database?.kind === 'sqlite' && !database.ephemeral && (
            <p className="text-sm text-slate-400">
              要繼續用本機檔案，不用做任何事。想換成 Postgres 也隨時可以：把連線字串放進
              DATABASE_URL（{platform?.env_where}）。
            </p>
          )}

          <button
            onClick={() => systemQuery.refetch()}
            className="rounded border border-slate-600 px-3 py-1 text-sm text-slate-200 hover:border-slate-400"
          >
            重新檢查
          </button>
        </div>
      )}

      {active === 'ai' && (
        <div className="space-y-4">
          {aiQuery.data?.configured ? (
            <>
              <div className="rounded border border-emerald-800 bg-emerald-950/40 p-3 text-sm text-emerald-200">
                <p className="font-medium">AI 的金鑰已經設定好了。</p>
                <p className="mt-1">按下面的按鈕確認它真的通——設定好不等於問得到。</p>
              </div>
              <button
                onClick={() => testAi.mutate()}
                disabled={testAi.isPending}
                className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                {testAi.isPending ? '問問看…' : '測試 AI'}
              </button>
              {aiResult?.ok && <p className="text-sm text-emerald-300">通了，AI 有回應。</p>}
              {aiResult && !aiResult.ok && (
                <p className="text-sm text-red-400">{aiResult.error ?? '沒有通。'}</p>
              )}
            </>
          ) : (
            <>
              <div className="rounded border border-slate-700 bg-slate-900 p-3 text-sm text-slate-300">
                <p className="font-medium text-slate-100">
                  還沒接上線，所以<strong>需要 AI 的功能現在是關著的</strong>。
                </p>
                <p className="mt-1">
                  其他功能完全不受影響——提醒、盯盤、通知、回測都照常。AI 只是幫忙的那一層。
                </p>
              </div>
              <ol className="list-inside list-decimal space-y-2 text-sm text-slate-300">
                <li>
                  去拿一把金鑰。
                  <a
                    href="https://openrouter.ai/keys"
                    className="underline"
                    target="_blank"
                    rel="noreferrer"
                  >
                    OpenRouter
                  </a>
                  一把可以用很多家的模型；直接用 OpenAI 或 Anthropic 的金鑰也可以。
                </li>
                <li>
                  金鑰是<strong>你自己的</strong>，每次發問的費用算在你自己帳上。它存在你自己的
                  資料庫裡而且是加密的，隨時可以刪掉。
                </li>
                <li>
                  到 <Link to="/ai-settings" className="underline">AI 輔助</Link>{' '}
                  那一頁貼上金鑰、選一個模型。
                </li>
                <li>回到這一頁按「測試 AI」，通了才算完成。</li>
              </ol>
            </>
          )}
        </div>
      )}

      {active === 'notifications' && (
        <div className="space-y-4">
          {channels.length > 0 ? (
            <>
              <div className="rounded border border-emerald-800 bg-emerald-950/40 p-3 text-sm text-emerald-200">
                <p className="font-medium">有 {channels.length} 個管道是開著的。</p>
                <ul className="mt-1 list-inside list-disc">
                  {channels.map((channel) => (
                    <li key={channel.id}>{channel.label}</li>
                  ))}
                </ul>
              </div>
              {/* 「設定好了」和「收得到」是兩件事。這個按鈕問的是後者。 */}
              <button
                onClick={() => testChannel.mutate(channels[0].id)}
                disabled={testChannel.isPending}
                className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                {testChannel.isPending ? '送出中…' : '傳一則測試'}
              </button>
              {channelResult?.ok && (
                <p className="text-sm text-emerald-300">
                  送出去了。<strong>沒有真的收到就不算完成</strong>——沒收到的話，常見原因是
                  Telegram 還沒跟 bot 說過話、Email 進了垃圾桶、或推播被瀏覽器擋掉。
                </p>
              )}
              {channelResult && !channelResult.ok && (
                <p className="text-sm text-red-400">{channelResult.error ?? '沒有送出去。'}</p>
              )}
            </>
          ) : (
            <>
              <p className="text-sm text-slate-300">
                沒有通知管道的話，前面設的提醒不會有人知道。選一個就夠了。
              </p>
              {/* 推播排第一：Telegram 要去 BotFather 拿 token，Email 要一整組 SMTP
                  設定，兩個都是「去別的地方拿一個值」。這一個按一下就好。 */}
              <button
                onClick={() => enablePush.mutate()}
                disabled={enablePush.isPending || !isPushSupported()}
                className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                {enablePush.isPending ? '開啟中…' : '開啟這台裝置的推播（最快）'}
              </button>
              {pushError && <p className="text-sm text-red-400">{pushError}</p>}
              <p className="text-sm text-slate-400">
                想用 Telegram、Email 或 LINE 的話，到{' '}
                <Link to="/notifications" className="underline">
                  通知
                </Link>{' '}
                那一頁設定。
              </p>
            </>
          )}
        </div>
      )}
    </div>
  )
}
