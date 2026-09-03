import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { TemplateAlertForm } from '../components/TemplateAlertForm'
import { ApiError, api } from '../lib/api'
import { markOnboardingSeen } from '../lib/onboarding'
import { isPushSupported, requestPushPermission, subscribeToPush } from '../lib/push'
import type { NotificationChannel, Strategy, StrategyGenerateResult } from '../lib/types'

type Step = 'choose' | 'templates' | 'ai' | 'channel' | 'done'

/**
 * 引導流程：從「帳號建好了」到「第一則提醒送到手機」。規格在 ONBOARDING.md。
 *
 * WHAT IT IS FOR. 建完帳號之後看到的本來是一個空的儀表板，畫面上沒有任何一句話
 * 說下一步該做什麼——而「下一步」在這之前需要打開一個程式碼編輯器。
 *
 * TWO RULES FROM THE SPEC ARE LOAD-BEARING HERE, and both are easy to undo by
 * accident later:
 *
 *   「我自己選」排在最上面。引導的預設路徑不可以是需要 AI 金鑰的那一條，
 *   否則設定就依賴一個本身也要設定的東西。任何一版只要拿掉 AI 之後引導走不完，
 *   就是做錯了——WelcomePage.test.tsx 有一條專門守這件事。
 *
 *   通知管道那一步可以跳過，但跳過的話畫面上要明說「現在不會有任何提醒送出」。
 *   沒有出口的提醒系統跟沒有在跑的提醒系統，後果一模一樣。
 */
export function WelcomePage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [step, setStep] = useState<Step>('choose')
  const [skippedChannel, setSkippedChannel] = useState(false)
  const [pushError, setPushError] = useState<string | null>(null)
  const [wish, setWish] = useState('')
  const [answer, setAnswer] = useState('')
  const [proposal, setProposal] = useState<StrategyGenerateResult | null>(null)
  const [aiError, setAiError] = useState<string | null>(null)

  const channelsQuery = useQuery({
    queryKey: ['notification-channels'],
    queryFn: () => api.get<NotificationChannel[]>('/api/notifications/channels'),
    retry: false,
  })
  const strategiesQuery = useQuery({
    queryKey: ['strategies'],
    queryFn: () => api.get<Strategy[]>('/api/strategies'),
    retry: false,
  })

  const aiSettingsQuery = useQuery({
    queryKey: ['ai-settings'],
    queryFn: () => api.get<{ configured: boolean }>('/api/ai-settings'),
    retry: false,
  })

  const enabled = (channelsQuery.data ?? []).filter((channel) => channel.is_enabled)
  // **開著的不等於送得到。** 這是他讀到的最後一句話，也是他之後憑什麼相信這個系統
  // 的那一句——用引導的口氣說「通知會送到 X」，而 X 從來沒有成功送出過一次，就是把
  // 一件還沒有任何證據的事講成了事實。
  //
  // 兩個條件一起看：`last_sent_at` 在 dispatcher 裡成功和失敗都會寫（它其實是「最後
  // 一次試過」），所以只看它的話，一個憑證過期的管道也會算成送得到。
  const proven = enabled.filter((channel) => channel.last_sent_at && !channel.last_error)
  const alerts = strategiesQuery.data ?? []

  const askAi = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api.post<StrategyGenerateResult>('/api/strategies/generate', body),
    onSuccess: (result) => {
      setProposal(result)
      setAiError(result.ok || result.question ? null : (result.error ?? '這一次沒有想出東西來。'))
    },
    onError: (err) => {
      setProposal(null)
      setAiError(
        err instanceof ApiError
          ? err.message
          : '問不到 AI。可能是金鑰或模型設定有問題，也可能是對方暫時沒有回應。',
      )
    },
  })

  const acceptProposal = useMutation({
    mutationFn: async () => {
      // 建立 → 啟用，兩步。跟表單那條路一樣：他剛剛按下的是「建立這則提醒」，
      // 一個還要再去開一次的提醒，是一個不會響的鬧鐘。
      const created = await api.post<Strategy>('/api/strategies', {
        name: proposal?.detected_name || 'AI 建立的提醒',
        symbol: proposal?.detected_symbol ?? '',
        source_code: proposal?.source_code ?? '',
        // 這是提醒系統。AI 寫的程式碼更需要這一條，因為他讀不懂它會做什麼。
        alert_only: true,
      })
      await api.post(`/api/strategies/${created.id}/activate`)
      return created
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['strategies'] })
      setStep('channel')
    },
    onError: (err) => {
      setAiError(err instanceof ApiError ? err.message : '建立失敗，請再試一次。')
    },
  })

  const enablePush = useMutation({
    mutationFn: async () => {
      // **先要權限，再訂閱。** subscribeToPush() 在權限不是 granted 時第一件事就是丟
      // 錯（見 lib/push.ts 的註解：要權限必須由使用者手勢直接觸發，所以那件事不能藏
      // 在訂閱裡）。全新裝置上 Notification.permission 是 'default'，所以少了這一步，
      // 這顆按鈕在**每一台第一次打開的手機上都必定失敗**——而它是引導裡唯一不需要他
      // 去別的服務註冊的那條通知路。通知頁早就這樣做了（NotificationsPage 的
      // startCreate），只有引導這兩頁漏掉。
      const permission = await requestPushPermission()
      if (permission !== 'granted') {
        throw new Error(
          permission === 'denied'
            ? '通知權限被封鎖了，瀏覽器不會再問一次。請到裝置的「設定」→ 通知（或瀏覽器的網站設定）把這個網站的通知打開，再回來按一次。'
            : '沒有取得通知權限，所以沒有開啟這台裝置的推播 —— 開了也永遠收不到東西。請再按一次並選擇「允許」。',
        )
      }
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
    onError: (err) => {
      // 說出發生什麼、以及他可以做什麼。「失敗」兩個字不是訊息。
      // **任何帶訊息的錯誤都比那段通用文字精確。** 原本只認 ApiError，於是「你按了
      // 封鎖，要去裝置設定改回來」這種只有前端知道、而且指得出唯一解法的訊息會被換成
      // 一段「常見原因：…」的清單，讓他自己去猜是哪一個。
      setPushError(
        err instanceof Error && err.message
          ? err.message
          : '這個瀏覽器沒有讓推播開起來。常見原因：拒絕過通知權限（要去瀏覽器的網站設定改回來）、'
            + '無痕視窗、或 iPhone 上還沒有把這個網頁「加入主畫面」。也可以改用 Telegram 或 Email。',
      )
    },
  })

  return (
    <div className="mx-auto max-w-xl space-y-6 p-4">
      {step === 'choose' && (
        <div className="space-y-4">
          <div>
            <h1 className="text-lg font-semibold text-slate-100">先設一則提醒吧</h1>
            <p className="mt-1 text-sm text-slate-400">
              這個系統的工作是盯著行情，事情發生的時候通知你。要不要買賣還是你自己決定——
              它不會幫你下單。
            </p>
          </div>

          {/* 第一個選項是設定，不是功能。新使用者真正卡住的地方是「資料庫怎麼
              接上線」「AI 的 API 怎麼接上線」，而那些在設定引導那一頁講完整。 */}
          <Link
            to="/guide"
            onClick={() => markOnboardingSeen()}
            className="block w-full rounded bg-emerald-600 p-4 text-left text-white hover:bg-emerald-500"
          >
            <span className="block font-medium">先把設定弄完</span>
            <span className="mt-1 block text-sm text-emerald-100">
              資料庫接上線、AI 的 API 接上線、通知收得到。三件事，一頁講完，而且每一件都
              可以在那裡按一下確認它真的通。
            </span>
          </Link>

          {/* 不需要金鑰的那一條排在 AI 前面，而且 AI 沒接上線時根本不出現：
              給一個按了會走進死路的選項，比不給還糟。 */}
          <button
            onClick={() => setStep('templates')}
            className="w-full rounded bg-emerald-600 p-4 text-left text-white hover:bg-emerald-500"
          >
            <span className="block font-medium">我自己選一個現成的</span>
            <span className="mt-1 block text-sm text-emerald-100">
              填表格就好，不用寫程式，也不用任何金鑰。多數人選這個。
            </span>
          </button>

          {aiSettingsQuery.data?.configured && (
            <button
              onClick={() => setStep('ai')}
              className="w-full rounded border border-slate-700 bg-slate-900 p-4 text-left hover:border-slate-500"
            >
              <span className="block font-medium text-slate-100">讓 AI 幫我</span>
              <span className="mt-1 block text-sm text-slate-400">
                用一句話說你要什麼。每次發問的費用算在你自己的金鑰上。
              </span>
            </button>
          )}

          <button
            onClick={() => setStep('channel')}
            className="w-full rounded border border-slate-800 p-3 text-left text-sm text-slate-400 hover:border-slate-600"
          >
            先跳過，直接去設定通知管道
          </button>
          <button
            onClick={() => {
              // 記下來，否則 OnboardingGate 會把他抓回來——帳號還是空的。
              markOnboardingSeen()
              navigate('/', { replace: true })
            }}
            className="w-full p-2 text-center text-xs text-slate-500 underline hover:text-slate-300"
          >
            我知道自己在做什麼，直接進儀表板
          </button>
        </div>
      )}

      {step === 'templates' && (
        <div className="space-y-4">
          <h1 className="text-lg font-semibold text-slate-100">要盯什麼？</h1>
          <TemplateAlertForm onCreated={() => setStep('channel')} />
          <button
            onClick={() => setStep('choose')}
            className="text-xs text-slate-500 underline hover:text-slate-300"
          >
            回上一步
          </button>
        </div>
      )}

      {step === 'ai' && (
        <div className="space-y-4">
          <h1 className="text-lg font-semibold text-slate-100">讓 AI 幫你設定</h1>

          {aiSettingsQuery.data?.configured ? (
            <div className="space-y-3">
              <div className="space-y-1">
                <label htmlFor="ai-wish" className="text-sm text-slate-400">
                  用一句話說你要什麼
                </label>
                <input
                  id="ai-wish"
                  value={wish}
                  onChange={(event) => setWish(event.target.value)}
                  placeholder="例如：台積電跌到 900 提醒我"
                  className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
                />
              </div>

              {/* 反問是一個好結果，不是失敗。把它顯示成錯誤，會教他忽略它——
                  而讓模型自己猜，產生的是一支看起來完成、實際上做別的事的策略，
                  那正是他讀不出來的那一種錯。 */}
              {proposal?.question && (
                <div className="space-y-1 rounded border border-sky-800 bg-sky-950/40 p-3 text-sm text-sky-200">
                  <p>{proposal.question}</p>
                  <label htmlFor="ai-answer" className="block text-xs text-sky-300">
                    你的回答
                  </label>
                  <input
                    id="ai-answer"
                    value={answer}
                    onChange={(event) => setAnswer(event.target.value)}
                    className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
                  />
                </div>
              )}

              {aiError && (
                <div className="space-y-2">
                  <p className="text-sm text-red-400">{aiError}</p>
                  {/* 規格：任何一步失敗都要退回「我自己選」，不是停在那裡。
                      沒有這條出口，一個選填的功能就變成了必需品。 */}
                  <button
                    onClick={() => setStep('templates')}
                    className="w-full rounded border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:border-slate-500"
                  >
                    改用現成的範本（不用 AI）
                  </button>
                </div>
              )}

              {proposal?.ok && proposal.source_code ? (
                <div className="space-y-2 rounded border border-emerald-800 bg-emerald-950/40 p-3 text-sm text-emerald-100">
                  <p className="font-medium">
                    AI 想幫你建立：{proposal.detected_name ?? '（沒有名字）'}
                    {proposal.detected_symbol ? `（${proposal.detected_symbol}）` : ''}
                  </p>
                  {/* 程式碼沒問題、但代號永遠不會有報價，是這裡最容易溜過去的一種：
                      綠色的「偵測到」讀起來像通過，而拒絕要到存檔時才從另一個欄位
                      出現，中間沒有東西把兩件事連起來。 */}
                  {proposal.symbol_problem && (
                    <p className="rounded border border-amber-700 bg-amber-950/40 px-2 py-1 text-amber-200">
                      {proposal.symbol_problem}
                    </p>
                  )}
                  <p className="text-xs text-emerald-300">
                    它是一段程式碼，你不用看懂——建立之後它只會通知你，不會下單。
                    之後在「策略」頁可以隨時停掉或刪掉。
                  </p>
                  <div className="flex flex-wrap gap-2 pt-1">
                    <button
                      onClick={() => acceptProposal.mutate()}
                      disabled={acceptProposal.isPending}
                      className="rounded bg-emerald-600 px-3 py-1 font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
                    >
                      {acceptProposal.isPending ? '建立中…' : '建立這則提醒'}
                    </button>
                    <button
                      onClick={() => {
                        setProposal(null)
                        setAiError(null)
                      }}
                      className="rounded border border-slate-600 px-3 py-1 text-slate-200 hover:border-slate-400"
                    >
                      重來一次
                    </button>
                    <button
                      onClick={() => setStep('templates')}
                      className="rounded border border-slate-700 px-3 py-1 text-slate-300 hover:border-slate-500"
                    >
                      改用現成的範本
                    </button>
                  </div>
                </div>
              ) : null}

              <button
                onClick={() =>
                  askAi.mutate({
                    description: wish,
                    // ask() 是單輪的：沒有一起送過去的東西，模型看不到。
                    ...(proposal?.question ? { question: proposal.question, answer } : {}),
                  })
                }
                disabled={askAi.isPending || !wish.trim()}
                className="w-full rounded bg-emerald-600 px-3 py-2 font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                {askAi.isPending ? 'AI 想想看…' : '讓 AI 想一個'}
              </button>
            </div>
          ) : (
            <>
              <p className="text-sm text-slate-400">
                AI 需要一把你自己的金鑰。金鑰是你的、存在你自己的資料庫裡而且是加密的，
                隨時可以刪掉——刪掉之後其他功能一切照常。
              </p>
              {/* 設定引導，不是 /ai-settings：設定發生的地方只有一個，而引導那一頁
                  現在貼得上金鑰也測得了連線。 */}
              <Link
                to="/guide"
                className="block rounded bg-emerald-600 px-3 py-2 text-center font-medium text-white hover:bg-emerald-500"
              >
                去設定 AI 金鑰
              </Link>
            </>
          )}

          <button
            onClick={() => setStep('templates')}
            className="w-full rounded border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:border-slate-500"
          >
            算了，我自己選一個現成的
          </button>
        </div>
      )}

      {step === 'channel' && (
        <div className="space-y-4">
          <h1 className="text-lg font-semibold text-slate-100">這些提醒要送到哪裡？</h1>

          {enabled.length > 0 ? (
            <div className="rounded border border-emerald-800 bg-emerald-950/40 p-4 text-sm text-emerald-200">
              <p className="font-medium">已經有地方可以送了。</p>
              <ul className="mt-2 list-inside list-disc">
                {enabled.map((channel) => (
                  <li key={channel.id}>{channel.label}</li>
                ))}
              </ul>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-sm text-slate-400">
                沒有這一步，前面設的提醒不會有人知道。選一個就夠了。
              </p>

              {/* 推播排第一，因為它是唯一不用去別的地方拿一個值的：Telegram 要去
                  BotFather 要 token，Email 要一整組 SMTP 設定。這一個是按一下就好。 */}
              <button
                onClick={() => enablePush.mutate()}
                disabled={enablePush.isPending || !isPushSupported()}
                className="w-full rounded bg-emerald-600 px-3 py-2 font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                {enablePush.isPending ? '開啟中…' : '開啟這台裝置的推播（最快，按一下就好）'}
              </button>
              {!isPushSupported() && (
                <p className="text-xs text-slate-500">
                  這個瀏覽器不支援推播。用下面的 Telegram 或 Email。
                </p>
              )}
              {pushError && <p className="text-sm text-red-400">{pushError}</p>}

              <Link
                to="/notifications"
                className="block rounded border border-slate-700 px-3 py-2 text-center text-sm text-slate-200 hover:border-slate-500"
              >
                我想用 Telegram 或 Email
              </Link>

              <button
                onClick={() => {
                  setSkippedChannel(true)
                  setStep('done')
                }}
                className="w-full p-2 text-center text-xs text-slate-500 underline hover:text-slate-300"
              >
                這一步先跳過
              </button>
            </div>
          )}

          <button
            onClick={() => setStep('done')}
            className="w-full rounded border border-slate-700 px-3 py-2 text-sm text-slate-200 hover:border-slate-500"
          >
            完成
          </button>
        </div>
      )}

      {step === 'done' && (
        <div className="space-y-4">
          <h1 className="text-lg font-semibold text-slate-100">設好了</h1>
          <p className="text-sm text-slate-300">
            你現在有 <strong>{alerts.length}</strong> 則提醒
            {enabled.length > 0 ? (
              <>
                ，通知會送到 <strong>{enabled.map((c) => c.label).join('、')}</strong>。
              </>
            ) : (
              '。'
            )}
          </p>

          {enabled.length > 0 && proven.length === 0 && (
            <p className="rounded border border-amber-700 bg-amber-950/40 px-3 py-2 text-sm text-amber-200">
              這個管道<strong>還沒有成功送出過任何一則</strong>，所以現在還不能確定提醒到得了。
              到「設定引導 → 通知」按一次「傳一則測試」，收到了才算數。
            </p>
          )}

          {(skippedChannel || enabled.length === 0) && (
            <p className="rounded border border-amber-700 bg-amber-950/40 px-3 py-2 text-sm text-amber-200">
              你還沒有設定任何通知管道，所以<strong>現在不會有任何提醒送出</strong>
              ——策略照樣在跑，只是沒有人會知道結果。每一頁上方都會留著這句話，直到你設好為止。
            </p>
          )}

          <p className="text-xs text-slate-500">這些之後都可以改，刪掉重設也隨時可以。</p>
          <button
            onClick={() => {
              markOnboardingSeen()
              navigate('/', { replace: true })
            }}
            className="w-full rounded bg-emerald-600 px-3 py-2 font-medium text-white hover:bg-emerald-500"
          >
            開始使用
          </button>
        </div>
      )}
    </div>
  )
}
