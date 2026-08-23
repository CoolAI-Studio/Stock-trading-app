import { useQuery } from '@tanstack/react-query'
import { AiCredentialsForm } from '../components/AiCredentialsForm'
import { api } from '../lib/api'
import type { AiSettings } from '../lib/types'

/**
 * Turning the AI on, checking it works, and turning it off.
 *
 * AI was the only secret in this app configured through an environment
 * variable. Nothing on any screen said the feature existed; adding a key meant
 * Render's Environment page, which the app never mentions; and CHANGING one
 * meant a redeploy, because Render restarts the service on every environment
 * change -- a minute of downtime to fix a typo in a model name, on the product
 * whose whole promise is not going down.
 *
 * Follows what the notification channels already do: write-only over the API,
 * a masked tail so you can tell which key it is, and a button that finds out
 * whether it actually works.
 */

const SOURCE_NOTE: Record<string, string> = {
  database: '目前用的是你在這一頁存的設定。',
  env: '目前用的是部署時填在 Render 環境變數裡的設定（AI_API_KEY / AI_MODEL）。在這裡存一份會蓋過它。',
  none: '',
}

export function AiSettingsPage() {
  // 同一個 query key，所以跟表單共用一份答案、不會多打一次後端。這一頁自己需
  // 要它，只是為了上面那段「這是選填的」和金鑰是誰的錢的說明。
  const query = useQuery({
    queryKey: ['ai-settings'],
    queryFn: () => api.get<AiSettings>('/api/ai-settings'),
    retry: false,
  })

  return (
    <div className="max-w-2xl space-y-4">
      <h1 className="text-xl font-semibold text-slate-100">AI 輔助</h1>

      {query.isError && (
        <p role="alert" className="rounded border border-red-800 bg-red-950/40 px-3 py-2 text-red-300">
          讀不到 AI 設定。先確認後端有在跑，再重新整理 —— 現在顯示的空白不代表你沒設定過。
        </p>
      )}

      <div className="space-y-2 rounded border border-slate-700 bg-slate-900 p-4 text-sm text-slate-300">
        <p>
          <span className="font-medium text-slate-100">這是選填的。</span>
          不填就是沒有 AI 輔助，其他功能一切照常 —— 報價、策略、提醒都不經過 AI。
        </p>
        <p>
          金鑰是<span className="font-medium text-slate-100">你自己的</span>，
          每一次發問都算在你自己的帳上。這個 app 不會替你花錢，也看不到你的用量 ——
          那要去供應者自己的後台看。
        </p>
        {query.data && SOURCE_NOTE[query.data.source] && (
          <p className="text-slate-400">{SOURCE_NOTE[query.data.source]}</p>
        )}
      </div>

      <AiCredentialsForm />
    </div>
  )
}
