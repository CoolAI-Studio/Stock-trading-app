import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { AiCredentialsForm } from '../components/AiCredentialsForm'
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

  // 使用者選的方案。null＝還沒選，畫面就把兩個選項都攤開。
  // 這只是「現在要看哪一段說明」，不是事實來源——那一格完成了沒，仍然由後端
  // 回報的現況決定（見 done.database）。
  const [dbPlan, setDbPlan] = useState<'local' | 'cloud' | null>(null)

  const database = systemQuery.data?.database
  const platform = systemQuery.data?.platform
  const channels = (channelsQuery.data ?? []).filter((channel) => channel.is_enabled)

  // 本機的檔案只有在「不會被清空」的時候才是一個正當選項。已經是 Postgres 的
  // 人不用選，那一格已經完成了。
  const canChooseLocal = database?.kind === 'sqlite' && !database.ephemeral
  // 會被清空的那一種沒得選，直接給步驟。
  const showCloudSteps = database?.ephemeral === true || dbPlan === 'cloud'

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

          {/* 本機和雲端是兩個可以選的方案，不是系統替他判斷的結果。
              只有「在會被清空的地方放著一個檔案」不給選——那不是偏好問題，
              把它列成一個選項等於把「資料會不見」包裝成一個可以勾的方案。 */}
          {canChooseLocal && (
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => setDbPlan('local')}
                aria-pressed={dbPlan === 'local'}
                className={`rounded px-3 py-1 text-sm ${
                  dbPlan === 'local'
                    ? 'bg-sky-700 font-medium text-white'
                    : 'border border-slate-600 text-slate-200 hover:border-slate-400'
                }`}
              >
                就用本機這個檔案
              </button>
              <button
                onClick={() => setDbPlan('cloud')}
                aria-pressed={dbPlan === 'cloud'}
                className={`rounded px-3 py-1 text-sm ${
                  dbPlan === 'cloud'
                    ? 'bg-sky-700 font-medium text-white'
                    : 'border border-slate-600 text-slate-200 hover:border-slate-400'
                }`}
              >
                改用雲端資料庫
              </button>
            </div>
          )}

          {dbPlan === 'local' && (
            <p className="text-sm text-slate-400">
              好，這一格<strong className="text-slate-200">不用再設定</strong>了。
              資料就存在這台機器上的那個檔案裡，沒有別人碰得到。
              唯一要記得的是<strong className="text-slate-200">備份</strong>：那個檔案不見了，
              提醒、策略、紀錄就一起不見了。
            </p>
          )}

          {/* 步驟本來寫死在「會被清空」那個條件裡，所以在本機跑的人想搬上雲端，
              只拿得到一句「把連線字串放進 DATABASE_URL」。對不寫程式的人，那句
              話就是流程到此結束。 */}
          {showCloudSteps && (
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
            <div className="rounded border border-emerald-800 bg-emerald-950/40 p-3 text-sm text-emerald-200">
              <p className="font-medium">AI 的金鑰已經設定好了。</p>
              <p className="mt-1">按下面的「測試連線」確認它真的通——設定好不等於問得到。</p>
            </div>
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
              {/* 金鑰只有供應商生得出來，所以這一步老實說「去哪裡拿」——CLAUDE.md
                  的規則是 app 生得出來的就給按鈕，生不出來的不要假裝。但「貼上」
                  就在下面，不再把人送去別的頁面。 */}
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
                <li>貼進下面的「API 金鑰」，選一個模型，按儲存。</li>
                <li>然後按「測試連線」——通了這一格才算完成。</li>
              </ol>
            </>
          )}

          {/* 跟 /ai-settings 是同一個元件。兩份實作會漂，而漂掉的那天這裡會用一
              組後端已經不收的欄位存金鑰，然後說「存好了」。 */}
          <AiCredentialsForm />
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
