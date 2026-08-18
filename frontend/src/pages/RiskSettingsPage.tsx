import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { RISK_FIELDS, riskFieldHelp } from '../lib/riskFields'
import type { RiskSettings } from '../lib/types'

export function RiskSettingsPage() {
  const queryClient = useQueryClient()
  const settingsQuery = useQuery({
    queryKey: ['risk-settings'],
    queryFn: () => api.get<RiskSettings>('/api/risk-settings'),
  })
  const [form, setForm] = useState<RiskSettings | null>(null)

  useEffect(() => {
    if (settingsQuery.data) setForm(settingsQuery.data)
  }, [settingsQuery.data])

  const saveMutation = useMutation({
    mutationFn: (payload: RiskSettings) => api.put<RiskSettings>('/api/risk-settings', payload),
    onSuccess: (updated) => {
      queryClient.setQueryData(['risk-settings'], updated)
    },
  })

  if (!form) return null

  return (
    <div className="max-w-md space-y-4">
      <h1 className="text-lg font-semibold">風險設定</h1>

      <div className="space-y-2">
        <p className="text-sm text-slate-400">
          這一頁是全域預設值，手動下單和 TradingView
          訊號一律照這裡的數字走。個別策略可以在「策略」頁打開「使用個別風險設定」自己覆蓋，沒有打開的策略一律沿用這裡，改了這裡它們就跟著改。
        </p>
        {/* Not a footnote: 本金 has been stored and displayed since v1 while
            doing nothing at all, so a number typed in months ago is about to
            start rejecting orders without anybody having changed it. */}
        <p className="rounded border border-amber-700 bg-amber-950/40 px-3 py-2 text-sm text-amber-200">
          注意：本金現在會真的擋單了。以前這個欄位只是存起來顯示，什麼都不會做；從現在起，買進後持倉總成本會超過本金的訊號會被拒絕。如果這個數字是你很久以前隨手填的，請先確認它還是你要的——填 0 表示不限制。
        </p>
      </div>

      <div className="space-y-3">
        {RISK_FIELDS.map((field) => {
          const help = riskFieldHelp(field)
          return (
            <div key={field.key}>
              <label htmlFor={field.key} className="text-sm text-slate-400">
                {field.label}
              </label>
              <input
                id={field.key}
                value={String(form[field.key])}
                onChange={(e) => setForm({ ...form, [field.key]: e.target.value })}
                className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
              />
              {help && <p className="mt-1 text-xs text-slate-500">{help}</p>}
            </div>
          )
        })}
      </div>

      <button
        disabled={saveMutation.isPending}
        onClick={() => saveMutation.mutate(form)}
        className="rounded bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
      >
        儲存
      </button>
      {saveMutation.isSuccess && <span className="ml-3 text-sm text-emerald-400">已儲存。</span>}
    </div>
  )
}
