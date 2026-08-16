import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from '../lib/api'
import type { SampleStrategy, Strategy, StrategyValidateResult } from '../lib/types'

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
          {strategy.is_active ? 'active' : 'inactive'}
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
          {strategy.is_active ? 'Deactivate' : 'Activate'}
        </button>
      </td>
    </tr>
  )
}

function NewStrategyForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState('')
  const [symbol, setSymbol] = useState('')
  const [sourceCode, setSourceCode] = useState('')
  const [validation, setValidation] = useState<StrategyValidateResult | null>(null)
  const [createError, setCreateError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const validateMutation = useMutation({
    mutationFn: () =>
      api.post<StrategyValidateResult>('/api/strategies/validate', { source_code: sourceCode }),
    onSuccess: (result) => setValidation(result),
  })

  const createMutation = useMutation({
    mutationFn: () => api.post<Strategy>('/api/strategies', { name, symbol, source_code: sourceCode }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['strategies'] })
      onDone()
    },
    onError: (err) => setCreateError(err instanceof ApiError ? err.message : 'Failed to create'),
  })

  return (
    <div className="space-y-3 rounded border border-slate-800 p-4">
      <div>
        <label htmlFor="strategy-name" className="text-sm text-slate-400">
          Name
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
          Symbol
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
          Source code
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
            ? `Detected: ${validation.detected_name} (${validation.detected_symbol})`
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
          Validate
        </button>
        <button
          disabled={createMutation.isPending || !name || !symbol || !sourceCode}
          onClick={() => createMutation.mutate()}
          className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          Create
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
  useQuery({
    queryKey: ['strategy-samples'],
    queryFn: () => api.get<SampleStrategy[]>('/api/strategies/samples'),
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Strategies</h1>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500"
        >
          New strategy
        </button>
      </div>

      {showForm && <NewStrategyForm onDone={() => setShowForm(false)} />}

      <table className="w-full text-left text-sm">
        <thead className="text-slate-500">
          <tr>
            <th className="pb-2 font-normal">Name</th>
            <th className="pb-2 font-normal">Symbol</th>
            <th className="pb-2 font-normal">Status</th>
            <th className="pb-2 font-normal">Last signal</th>
            <th className="pb-2 font-normal">Error</th>
            <th className="pb-2 font-normal">Actions</th>
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
