import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from '../lib/api'
import type { SampleStrategy, Strategy, StrategyValidateResult } from '../lib/types'

const SAMPLE_LABELS: Record<string, string> = {
  ma5_cross: '5 日均線交叉',
  rsi_threshold: 'RSI 超買超賣',
}

function sampleLabel(sample: SampleStrategy): string {
  const key = sample.filename.replace(/\.py$/, '')
  return SAMPLE_LABELS[key] ?? key
}

function StrategyRow({ strategy }: { strategy: Strategy }) {
  const queryClient = useQueryClient()
  const toggleMutation = useMutation({
    mutationFn: () =>
      api.post<Strategy>(`/api/strategies/${strategy.id}/${strategy.is_active ? 'deactivate' : 'activate'}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['strategies'] }),
  })

  return (
    <tr className="border-b border-slate-800">
      <td className="py-2 pr-4 font-medium">{strategy.name}</td>
      <td className="py-2 pr-4">{strategy.symbol}</td>
      <td className="py-2 pr-4">
        <span className={strategy.is_active ? 'text-emerald-400' : 'text-slate-500'}>
          {strategy.is_active ? '啟用中' : '已停用'}
        </span>
      </td>
      <td className="py-2 pr-4 text-slate-400">{strategy.last_signal ?? '—'}</td>
      <td className="py-2 pr-4 text-red-400">
        {strategy.consecutive_errors > 0 ? strategy.last_error : ''}
      </td>
      <td className="py-2">
        <button
          disabled={toggleMutation.isPending}
          onClick={() => toggleMutation.mutate()}
          className="rounded bg-slate-700 px-3 py-1 text-sm font-medium text-white hover:bg-slate-600 disabled:opacity-50"
        >
          {strategy.is_active ? '停用' : '啟用'}
        </button>
      </td>
    </tr>
  )
}

function NewStrategyForm({ onDone, samples }: { onDone: () => void; samples: SampleStrategy[] }) {
  const [name, setName] = useState('')
  const [symbol, setSymbol] = useState('')
  const [sourceCode, setSourceCode] = useState('')
  const [validation, setValidation] = useState<StrategyValidateResult | null>(null)
  const [createError, setCreateError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const validateMutation = useMutation({
    mutationFn: () =>
      api.post<StrategyValidateResult>('/api/strategies/validate', { source_code: sourceCode }),
    onSuccess: (result) => {
      setValidation(result)
      // Auto-fill from the code's own self.name/self.symbol so loading a
      // sample (or pasting existing code) doesn't require retyping them --
      // but never overwrite something the user already typed themselves.
      if (result.ok) {
        if (!name && result.detected_name) setName(result.detected_name)
        if (!symbol && result.detected_symbol) setSymbol(result.detected_symbol)
      }
    },
  })

  function applySample(sample: SampleStrategy) {
    setSourceCode(sample.source_code)
    setValidation(null)
    setCreateError(null)
  }

  const createMutation = useMutation({
    mutationFn: () => api.post<Strategy>('/api/strategies', { name, symbol, source_code: sourceCode }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['strategies'] })
      onDone()
    },
    onError: (err) => setCreateError(err instanceof ApiError ? err.message : '建立失敗'),
  })

  return (
    <div className="space-y-3 rounded border border-slate-800 p-4">
      {samples.length > 0 && (
        <div className="space-y-1">
          <p className="text-sm text-slate-400">不知道怎麼寫策略？從範例載入：</p>
          <div className="flex flex-wrap gap-2">
            {samples.map((sample) => (
              <button
                key={sample.filename}
                type="button"
                onClick={() => applySample(sample)}
                className="rounded border border-slate-700 px-3 py-1 text-sm hover:bg-slate-800"
              >
                {sampleLabel(sample)}
              </button>
            ))}
          </div>
        </div>
      )}
      <div>
        <label htmlFor="strategy-name" className="text-sm text-slate-400">
          名稱
        </label>
        <input
          id="strategy-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
      </div>
      <div>
        <label htmlFor="strategy-symbol" className="text-sm text-slate-400">
          股票代號
        </label>
        <input
          id="strategy-symbol"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
      </div>
      <div>
        <label htmlFor="strategy-code" className="text-sm text-slate-400">
          原始碼
        </label>
        <textarea
          id="strategy-code"
          value={sourceCode}
          onChange={(e) => setSourceCode(e.target.value)}
          rows={10}
          className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-sm"
        />
      </div>

      {validation && (
        <p className={validation.ok ? 'text-emerald-400' : 'text-red-400'}>
          {validation.ok
            ? `偵測到：${validation.detected_name}（${validation.detected_symbol}）`
            : validation.error}
        </p>
      )}
      {createError && <p className="text-red-400">{createError}</p>}

      <div className="flex gap-2">
        <button
          disabled={validateMutation.isPending || !sourceCode}
          onClick={() => validateMutation.mutate()}
          className="rounded bg-slate-700 px-3 py-1 text-sm font-medium hover:bg-slate-600 disabled:opacity-50"
        >
          驗證
        </button>
        <button
          disabled={createMutation.isPending || !name || !symbol || !sourceCode}
          onClick={() => createMutation.mutate()}
          className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          建立
        </button>
      </div>
    </div>
  )
}

export function StrategiesPage() {
  const [showForm, setShowForm] = useState(false)
  const strategiesQuery = useQuery({
    queryKey: ['strategies'],
    queryFn: () => api.get<Strategy[]>('/api/strategies'),
  })
  const samplesQuery = useQuery({
    queryKey: ['strategy-samples'],
    queryFn: () => api.get<SampleStrategy[]>('/api/strategies/samples'),
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">策略</h1>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500"
        >
          新增策略
        </button>
      </div>

      {showForm && (
        <NewStrategyForm onDone={() => setShowForm(false)} samples={samplesQuery.data ?? []} />
      )}

      <table className="w-full text-left text-sm">
        <thead className="text-slate-500">
          <tr>
            <th className="pb-2 font-normal">名稱</th>
            <th className="pb-2 font-normal">股票代號</th>
            <th className="pb-2 font-normal">狀態</th>
            <th className="pb-2 font-normal">最新訊號</th>
            <th className="pb-2 font-normal">錯誤</th>
            <th className="pb-2 font-normal">操作</th>
          </tr>
        </thead>
        <tbody>
          {(strategiesQuery.data ?? []).map((strategy) => (
            <StrategyRow key={strategy.id} strategy={strategy} />
          ))}
        </tbody>
      </table>
    </div>
  )
}
