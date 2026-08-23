import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { AiSettings } from '../lib/types'

/**
 * 供應者、網址、模型、金鑰，加上「存起來」和「測測看」。
 *
 * 抽成元件而不是留在 AiSettingsPage 裡，是因為**設定引導也需要同一件事**。原本
 * 引導的 AI 那一格只有一串指示：「到 AI 輔助那一頁貼上金鑰，再回來按測試」——
 * 引導最容易斷掉的就是這種一步，人離開了還回不回得來，不在我們手上。而
 * CLAUDE.md 的第一條規則是「永遠不要叫他去別的地方拿一個值」：金鑰確實要去供應
 * 商那裡拿（那件事誠實說出來就好），但「貼上」發生在哪一頁是我們決定的。
 *
 * **抽出來而不是複製一份。** 兩份實作會漂，而漂掉的那一天，引導頁會用一組
 * /ai-settings 已經不再接受的欄位存金鑰，然後說「存好了」。這個 repo 已經為
 * 「同一個想法的第二份實作」付過學費——指標之所以在後端算，就是這個理由。
 */

const PROVIDERS: [value: string, label: string][] = [
  ['openai_compatible', 'OpenAI 相容（OpenRouter、Groq、本地端…）'],
  ['anthropic', 'Anthropic'],
]

export function AiCredentialsForm() {
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

  // Fetched for what the FORM currently shows, not for what is saved: somebody
  // picks a provider and a model in the same visit, and listing the saved
  // provider's models while they look at a different one offers names that
  // cannot possibly work.
  const modelsQuery = useQuery({
    queryKey: ['ai-models', provider, baseUrl],
    queryFn: () =>
      api.get<{ models: { id: string; name: string; free: boolean }[]; error: string | null }>(
        `/api/ai-settings/models?provider=${encodeURIComponent(provider)}&base_url=${encodeURIComponent(baseUrl)}`,
      ),
    retry: false,
  })

  const models = modelsQuery.data?.models ?? []
  // The picker only replaces the text box when there is something to pick. A
  // picker that stops somebody typing a model they know is worse than none.
  const canPick = models.length > 0

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
          aria-label="API 網址"
          type="text"
          autoComplete="off"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          className="mt-1 block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-sm"
        />
      </label>

      <label className="block">
        <span className="text-sm text-slate-300">模型</span>
        {canPick ? (
          <select
            aria-label="模型"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="mt-1 block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-sm"
          >
            <option value="">（請選一個）</option>
            {/* The saved model may not be in the list -- a provider retires
                one, or it was typed by hand. Kept as an option so opening
                the page does not silently change what is in force. */}
            {model && !models.some((m) => m.id === model) && (
              <option value={model}>{model}（目前設定，清單裡沒有）</option>
            )}
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.free ? `【免費】${m.name}` : m.name}
              </option>
            ))}
          </select>
        ) : (
          <input
            aria-label="模型"
            type="text"
            // Without this a browser reads an unlabelled text box next to a
            // password field as a username and fills in an email address --
            // observed happening, with the owner's own address landing in
            // this field.
            autoComplete="off"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="例如 anthropic/claude-opus-5"
            className="mt-1 block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-sm"
          />
        )}
        {modelsQuery.data?.error && (
          <span className="mt-1 block text-xs text-amber-400">
            抓不到模型清單（{modelsQuery.data.error}）—— 可以直接自己輸入。
          </span>
        )}
        <span className="mt-1 block text-xs text-slate-500">
          {canPick
            ? '【免費】的模型不用付費，但常常忙線。要備援的話存檔後改成自己輸入，用逗號分隔多個。'
            : '用逗號分隔可以填好幾個，前面的不通就換下一個 —— 免費模型常常忙線，這是讓它還能用的關鍵。'}
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
          // new-password, not off: browsers ignore `off` on password fields
          // and offer a saved login anyway. This is the value they honour.
          autoComplete="new-password"
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
  )
}
