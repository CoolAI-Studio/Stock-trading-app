import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError, api } from '../lib/api'
import type { Strategy, StrategyTemplate } from '../lib/types'

/**
 * 一則提醒，用表單設定出來——完全不用寫程式。
 *
 * WHY THIS COMPONENT EXISTS. CLAUDE.md 把它列為核心功能：「不用寫 Python 就能
 * 設定的簡單價格提醒，是核心功能，不是加分項。」而在這之前，想要「台積電跌到
 * 900 塊叫我」的唯一一條路是打開一個程式碼編輯器，把範例裡的 `self.buy_below =
 * 950.0` 改掉。範例把改動縮到「改三個數字」，但畫面上仍然是程式碼——而「改三個
 * 數字」和「不用寫程式」對這個 app 的使用者是兩件不同的事。
 *
 * 底下沒有新的引擎：送出去的是 /api/strategies/from-template，伺服器拿範本填空
 * 之後，走的是跟手寫策略完全一樣的那條路（沙箱、參數型別檢查、代號與行情來源
 * 的比對）。這裡少掉的只有那個文字框。
 *
 * 這個元件同時是引導流程（ONBOARDING.md 階段 2A）會用的那一個，所以它不假設
 * 自己被放在哪一頁：拿到 onCreated 就交出去，畫面怎麼走由外面決定。
 */
export function TemplateAlertForm({ onCreated }: { onCreated?: (strategy: Strategy) => void }) {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<StrategyTemplate | null>(null)
  const [symbol, setSymbol] = useState('')
  const [name, setName] = useState('')
  const [values, setValues] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [created, setCreated] = useState<Strategy | null>(null)

  const templatesQuery = useQuery({
    queryKey: ['strategy-templates'],
    queryFn: () => api.get<StrategyTemplate[]>('/api/strategies/templates'),
  })

  const createMutation = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api.post<Strategy>('/api/strategies/from-template', body),
    onSuccess: (strategy) => {
      setCreated(strategy)
      queryClient.invalidateQueries({ queryKey: ['strategies'] })
      onCreated?.(strategy)
    },
    onError: (err) => {
      // The backend already wrote a sentence this person can read
      // (「binance 上沒有 2330.TW」). Swallowing it and printing 「失敗」
      // would throw away the only useful part of the answer.
      setError(err instanceof ApiError ? err.message : '建立失敗，請再試一次。')
    },
  })

  function choose(template: StrategyTemplate) {
    setSelected(template)
    setError(null)
    setValues(Object.fromEntries(template.fields.map((f) => [f.key, String(f.default)])))
  }

  function submit() {
    if (!selected) return
    setError(null)
    const cleanedSymbol = symbol.trim()
    if (!cleanedSymbol) {
      setError('請填股票代號，例如 2330.TW。沒有代號就不知道要盯什麼。')
      return
    }
    const params: Record<string, number | string> = {}
    for (const field of selected.fields) {
      const raw = values[field.key] ?? String(field.default)
      params[field.key] = field.kind === 'number' ? Number(raw) : raw
    }
    createMutation.mutate({
      template: selected.key,
      // 名字沒填就替他取一個。這一格是給他之後認得出來用的，不是一道關卡——
      // 為了一個他不在乎的欄位擋住送出，是把設定變成填表。
      name: name.trim() || `${selected.title} ${cleanedSymbol}`,
      symbol: cleanedSymbol,
      params,
    })
  }

  if (created) {
    return (
      <div className="rounded border border-emerald-800 bg-emerald-950/40 p-4 text-sm text-emerald-200">
        <p className="font-medium">已經建立，而且已經開始盯著了。</p>
        <p className="mt-1">
          「{created.name}」現在是開著的。條件成立的時候會用你設定的通知管道通知你——
          <strong>如果還沒有設定任何通知管道，這則提醒就沒有地方可以送。</strong>
        </p>
        <button
          onClick={() => {
            setCreated(null)
            setSelected(null)
            setSymbol('')
            setName('')
          }}
          className="mt-3 rounded bg-emerald-700 px-3 py-1 font-medium text-white hover:bg-emerald-600"
        >
          再設一則
        </button>
      </div>
    )
  }

  if (!selected) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-slate-400">
          選一種提醒。每一種都是填表格，不用寫程式。
        </p>
        {templatesQuery.isError && (
          <p className="text-sm text-red-400">讀不到範本清單，請重新整理看看。</p>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          {(templatesQuery.data ?? []).map((template) => (
            <button
              key={template.key}
              onClick={() => choose(template)}
              className="rounded border border-slate-700 bg-slate-900 p-4 text-left hover:border-emerald-600"
            >
              <span className="block font-medium text-slate-100">{template.title}</span>
              <span className="mt-1 block text-sm text-slate-300">{template.summary}</span>
              <span className="mt-2 block text-xs text-slate-500">{template.good_for}</span>
            </button>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4 rounded border border-slate-700 bg-slate-900 p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-medium text-slate-100">{selected.title}</h2>
          <p className="text-sm text-slate-400">{selected.summary}</p>
        </div>
        <button
          onClick={() => setSelected(null)}
          className="shrink-0 rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:border-slate-500"
        >
          換一個
        </button>
      </div>

      <div className="space-y-1">
        <label htmlFor="template-symbol" className="text-sm text-slate-400">
          股票代號
        </label>
        <input
          id="template-symbol"
          value={symbol}
          onChange={(event) => setSymbol(event.target.value)}
          placeholder="2330.TW"
          className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
        />
        <p className="text-xs text-slate-500">
          台股要加 .TW（上櫃是 .TWO），美股直接填代號，例如 AAPL。
        </p>
      </div>

      {selected.fields.map((field) => (
        <div key={field.key} className="space-y-1">
          <label htmlFor={`template-${field.key}`} className="text-sm text-slate-400">
            {field.label}
          </label>
          <input
            id={`template-${field.key}`}
            type={field.kind === 'number' ? 'number' : 'text'}
            value={values[field.key] ?? ''}
            onChange={(event) =>
              setValues((current) => ({ ...current, [field.key]: event.target.value }))
            }
            className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
          />
          {/* 說明和欄位放在一起，不是放在說明文件裡：一個沒有說明的數字欄位，
              對這個使用者就是一個填不下去的欄位。 */}
          <p className="text-xs text-slate-500">{field.help}</p>
        </div>
      ))}

      <div className="space-y-1">
        <label htmlFor="template-name" className="text-sm text-slate-400">
          名字（可以不填）
        </label>
        <input
          id="template-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder={`${selected.title} ${symbol || '2330.TW'}`}
          className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
        />
      </div>

      {error && <p className="text-sm text-red-400">{error}</p>}

      <button
        onClick={submit}
        disabled={createMutation.isPending}
        className="w-full rounded bg-emerald-600 py-2 font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
      >
        {createMutation.isPending ? '建立中…' : '建立提醒'}
      </button>
    </div>
  )
}
