import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { RiskSettings } from '../lib/types'

const FIELDS: Array<{ key: keyof RiskSettings; label: string }> = [
  { key: 'capital', label: '本金' },
  { key: 'stop_loss_pct', label: '停損百分比' },
  { key: 'take_profit_pct', label: '停利百分比' },
  { key: 'max_position_qty', label: '最大持倉數量' },
  { key: 'max_order_notional', label: '單筆最大金額' },
  { key: 'max_pending_orders_per_symbol', label: '單一代號最大待確認訂單數' },
  { key: 'signal_cooldown_sec', label: '訊號冷卻時間（秒）' },
]

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

      <div className="space-y-3">
        {FIELDS.map(({ key, label }) => (
          <div key={key}>
            <label htmlFor={key} className="text-sm text-slate-400">
              {label}
            </label>
            <input
              id={key}
              value={String(form[key])}
              onChange={(e) => setForm({ ...form, [key]: e.target.value })}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
        ))}
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
