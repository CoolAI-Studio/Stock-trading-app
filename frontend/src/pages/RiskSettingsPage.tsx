import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { RiskSettings } from '../lib/types'

const FIELDS: Array<{ key: keyof RiskSettings; label: string }> = [
  { key: 'capital', label: 'Capital' },
  { key: 'stop_loss_pct', label: 'Stop-loss %' },
  { key: 'take_profit_pct', label: 'Take-profit %' },
  { key: 'max_position_qty', label: 'Max position qty' },
  { key: 'max_order_notional', label: 'Max order notional' },
  { key: 'max_pending_orders_per_symbol', label: 'Max pending orders/symbol' },
  { key: 'signal_cooldown_sec', label: 'Signal cooldown (sec)' },
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
      <h1 className="text-lg font-semibold">Risk Settings</h1>

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
        Save
      </button>
      {saveMutation.isSuccess && <span className="ml-3 text-sm text-emerald-400">Saved.</span>}
    </div>
  )
}
