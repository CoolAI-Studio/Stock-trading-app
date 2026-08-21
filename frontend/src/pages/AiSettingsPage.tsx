import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
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

const PROVIDERS: [value: string, label: string][] = [
  ['openai_compatible', 'OpenAI 相容（OpenRouter、Groq、本地端…）'],
  ['anthropic', 'Anthropic'],
]

export function AiSettingsPage() {
  const queryClient = useQueryClient()
  const [provider, setProvider] = useState('openai_compatible')
  const [baseUrl, setBaseUrl] = useState('https://openrouter.ai/api/v1')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null)

  const query = useQuery({
    queryKey: ['ai-settings'],
    queryFn: () => api.get<AiSettings>('/api/ai-settings'),
    retry: false,
  })

  // Seeded ONCE, when the server first answers, so the boxes show what is
  // actually in force rather than a blank form over a working configuration.
  //
  // Once, not on every change: this query is invalidated after a save and
  // refetched on a window focus, and re-seeding then would wipe whatever the
  // person had typed since -- mid-edit, with no undo, for no reason they could
  // see.
  const seeded = useRef(false)
  useEffect(() => {
    if (!query.data || seeded.current) return
    seeded.current = true
    setProvider(query.data.provider)
    setBaseUrl(query.data.base_url)
    setModel(query.data.model)
  }, [query.data])

  const save = useMutation({
    mutationFn: () =>
      api.put<AiSettings>('/api/ai-settings', {
        provider,
        base_url: baseUrl,
        model,
        // null means 「leave the key alone」. Correcting a model name is the
        // commonest edit, and demanding the secret for it would send somebody
        // to a password manager to change a string that is not secret.
        api_key: apiKey.trim() || null,
      }),
    onSuccess: () => {
      setApiKey('')
      setTestResult(null)
      queryClient.invalidateQueries({ queryKey: ['ai-settings'] })
      // The assistant on the status page appears or disappears with this.
      queryClient.invalidateQueries({ queryKey: ['system-status'] })
    },
  })

  const test = useMutation({
    mutationFn: () =>
      api.post<{ ok: boolean; reply: string | null; error: string | null }>(
        '/api/ai-settings/test',
        {},
      ),
    onSuccess: (result) =>
      setTestResult(
        result.ok
          ? { ok: true, text: '可以用。金鑰和模型都通了。' }
          : { ok: false, text: result.error ?? '測試失敗。' },
      ),
    onError: (err) =>
      setTestResult({ ok: false, text: err instanceof Error ? err.message : '測試失敗。' }),
  })

  const clear = useMutation({
    mutationFn: () => api.delete('/api/ai-settings'),
    onSuccess: () => {
      setApiKey('')
      setTestResult(null)
      queryClient.invalidateQueries({ queryKey: ['ai-settings'] })
      queryClient.invalidateQueries({ queryKey: ['system-status'] })
    },
  })

  const configured = query.data?.configured ?? false

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

      <div className="space-y-3 rounded border border-slate-700 bg-slate-900 p-4">
        <label className="block">
          <span className="text-sm text-slate-300">供應者</span>
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            className="mt-1 block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-sm"
          >
            {PROVIDERS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="text-sm text-slate-300">API 網址</span>
          <input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            className="mt-1 block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-sm"
          />
        </label>

        <label className="block">
          <span className="text-sm text-slate-300">模型</span>
          <input
            aria-label="模型"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="例如 anthropic/claude-sonnet-4.5"
            className="mt-1 block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-sm"
          />
          <span className="mt-1 block text-xs text-slate-500">
            用逗號分隔可以填好幾個，前面的不通就換下一個 —— 免費模型常常忙線，這是讓它還能用的關鍵。
          </span>
        </label>

        <label className="block">
          <span className="text-sm text-slate-300">
            API 金鑰
            {query.data?.key_preview && (
              <span className="ml-2 font-mono text-xs text-emerald-400">
                目前：{query.data.key_preview}
              </span>
            )}
          </span>
          <input
            aria-label="API 金鑰"
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={configured ? '留白就是不更動現有金鑰' : 'sk-...'}
            className="mt-1 block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-sm"
          />
        </label>

        <div className="flex flex-wrap gap-2 pt-1">
          <button
            type="button"
            disabled={save.isPending}
            onClick={() => save.mutate()}
            className="rounded bg-sky-700 px-3 py-1 text-sm font-medium text-white hover:bg-sky-600 disabled:opacity-50"
          >
            儲存
          </button>
          {/* Only once there is something to test. A button that always
              answers 「還沒設定」 teaches people it does not work. */}
          {configured && (
            <button
              type="button"
              disabled={test.isPending}
              onClick={() => test.mutate()}
              className="rounded bg-slate-700 px-3 py-1 text-sm font-medium text-white hover:bg-slate-600 disabled:opacity-50"
            >
              {test.isPending ? '測試中…' : '測試連線'}
            </button>
          )}
          {configured && query.data?.source === 'database' && (
            <button
              type="button"
              disabled={clear.isPending}
              onClick={() => clear.mutate()}
              className="rounded bg-red-900 px-3 py-1 text-sm font-medium text-red-100 hover:bg-red-800 disabled:opacity-50"
            >
              清除設定
            </button>
          )}
        </div>

        {save.isError && (
          <p role="alert" className="text-sm text-red-400">
            存不起來：{save.error instanceof Error ? save.error.message : '請再試一次。'}
          </p>
        )}
        {testResult && (
          <p
            role={testResult.ok ? undefined : 'alert'}
            className={`text-sm ${testResult.ok ? 'text-emerald-400' : 'text-red-400'}`}
          >
            {testResult.text}
          </p>
        )}
      </div>
    </div>
  )
}
