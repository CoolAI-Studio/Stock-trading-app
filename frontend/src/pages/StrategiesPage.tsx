import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from '../lib/api'
import type {
  OrderSide,
  SampleStrategy,
  Strategy,
  StrategyAlert,
  StrategyDetail,
  StrategyGenerateResult,
  StrategyValidateResult,
} from '../lib/types'

const GENERATE_FAILED = '產生策略失敗，請稍後再試一次。'
const OVERWRITE_CONFIRM = '原始碼欄位已經有內容，AI 產生的程式碼會整個蓋掉它。要繼續嗎？'

const SIDE_LABEL: Record<OrderSide, string> = { buy: '買進', sell: '賣出' }

const ALERT_ONLY_LABEL = '只提醒，不產生訂單'
const ALERT_ONLY_HELP = '勾選後，這個策略的買賣訊號只會發通知給你，不會產生需要確認的訂單。'

const SAMPLE_LABELS: Record<string, string> = {
  ma5_cross: '5 日均線交叉',
  rsi_threshold: 'RSI 超買超賣',
}

function sampleLabel(sample: SampleStrategy): string {
  const key = sample.filename.replace(/\.py$/, '')
  return SAMPLE_LABELS[key] ?? key
}

function AlertOnlyField({
  id,
  checked,
  onChange,
}: {
  id: string
  checked: boolean
  onChange: (value: boolean) => void
}) {
  return (
    <div>
      <label htmlFor={id} className="flex items-center gap-2 text-sm">
        <input
          id={id}
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
        />
        {ALERT_ONLY_LABEL}
      </label>
      <p className="text-xs text-slate-500">{ALERT_ONLY_HELP}</p>
    </div>
  )
}

function EditStrategyForm({ strategy, onDone }: { strategy: Strategy; onDone: () => void }) {
  const [name, setName] = useState(strategy.name)
  const [symbol, setSymbol] = useState(strategy.symbol)
  // Comes straight off the list row: unlike the source, alert_only is already
  // in hand, so the box never renders in the wrong state while the detail
  // request is in flight.
  const [alertOnly, setAlertOnly] = useState(strategy.alert_only)
  const [sourceCode, setSourceCode] = useState<string | null>(null)
  const [validation, setValidation] = useState<StrategyValidateResult | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  // The list response omits source_code, so the editor has to fetch it. Until
  // it arrives sourceCode stays null and the textarea is disabled -- rendering
  // an empty box first reads as "my code is gone", which is what this whole
  // form previously did.
  const detailQuery = useQuery({
    queryKey: ['strategy', strategy.id],
    queryFn: () => api.get<StrategyDetail>(`/api/strategies/${strategy.id}`),
  })

  useEffect(() => {
    if (detailQuery.data && sourceCode === null) {
      setSourceCode(detailQuery.data.source_code)
    }
  }, [detailQuery.data, sourceCode])

  const validateMutation = useMutation({
    mutationFn: () =>
      api.post<StrategyValidateResult>('/api/strategies/validate', { source_code: sourceCode }),
    onSuccess: (result) => setValidation(result),
  })

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload: Record<string, unknown> = { name, symbol, alert_only: alertOnly }
      // Only send source_code once the real one has loaded, so a save that
      // races the fetch can't overwrite the stored code with an empty box.
      if (sourceCode !== null) payload.source_code = sourceCode
      return api.patch<Strategy>(`/api/strategies/${strategy.id}`, payload)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['strategies'] })
      queryClient.invalidateQueries({ queryKey: ['strategy', strategy.id] })
      onDone()
    },
    onError: (err) => setSaveError(err instanceof ApiError ? err.message : '儲存失敗'),
  })

  return (
    <div className="space-y-3 rounded border border-slate-800 p-4">
      <div>
        <label htmlFor={`edit-strategy-name-${strategy.id}`} className="text-sm text-slate-400">
          名稱
        </label>
        <input
          id={`edit-strategy-name-${strategy.id}`}
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
      </div>
      <div>
        <label htmlFor={`edit-strategy-symbol-${strategy.id}`} className="text-sm text-slate-400">
          股票代號
        </label>
        <input
          id={`edit-strategy-symbol-${strategy.id}`}
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
      </div>
      <AlertOnlyField
        id={`edit-strategy-alert-only-${strategy.id}`}
        checked={alertOnly}
        onChange={setAlertOnly}
      />
      <div>
        <label htmlFor={`edit-strategy-code-${strategy.id}`} className="text-sm text-slate-400">
          原始碼
        </label>
        <textarea
          id={`edit-strategy-code-${strategy.id}`}
          value={sourceCode ?? ''}
          onChange={(e) => setSourceCode(e.target.value)}
          disabled={sourceCode === null}
          rows={10}
          placeholder={detailQuery.isError ? '讀取原始碼失敗，請重新整理' : '載入中…'}
          className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-sm disabled:opacity-50"
        />
      </div>

      {validation && (
        <p className={validation.ok ? 'text-emerald-400' : 'text-red-400'}>
          {validation.ok
            ? `偵測到：${validation.detected_name}（${validation.detected_symbol}）`
            : validation.error}
        </p>
      )}
      {saveError && <p className="text-red-400">{saveError}</p>}

      <div className="flex gap-2">
        <button
          disabled={validateMutation.isPending || !sourceCode}
          onClick={() => validateMutation.mutate()}
          className="rounded bg-slate-700 px-3 py-1 text-sm font-medium hover:bg-slate-600 disabled:opacity-50"
        >
          驗證
        </button>
        <button
          disabled={saveMutation.isPending || !name || !symbol || sourceCode === null}
          onClick={() => saveMutation.mutate()}
          className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          儲存
        </button>
        <button
          onClick={onDone}
          className="rounded bg-slate-800 px-3 py-1 text-sm font-medium text-slate-300 hover:bg-slate-700"
        >
          取消
        </button>
      </div>
    </div>
  )
}

function StrategyRow({ strategy }: { strategy: Strategy }) {
  const [editing, setEditing] = useState(false)
  const queryClient = useQueryClient()
  const toggleMutation = useMutation({
    mutationFn: () =>
      api.post<Strategy>(`/api/strategies/${strategy.id}/${strategy.is_active ? 'deactivate' : 'activate'}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['strategies'] }),
  })

  const deleteMutation = useMutation({
    mutationFn: () => api.delete(`/api/strategies/${strategy.id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['strategies'] }),
  })

  function handleDelete() {
    if (window.confirm(`確定要刪除策略「${strategy.name}」嗎？此操作無法復原。`)) {
      deleteMutation.mutate()
    }
  }

  return (
    <>
      <tr className="border-b border-slate-800">
        <td className="py-2 pr-4 font-medium">{strategy.name}</td>
        <td className="py-2 pr-4">{strategy.symbol}</td>
        <td className="py-2 pr-4">
          <span className={strategy.is_active ? 'text-emerald-400' : 'text-slate-500'}>
            {strategy.is_active ? '啟用中' : '已停用'}
          </span>
        </td>
        {/* Both modes are spelled out rather than only badging the odd one
            out: an unlabelled row read as the wrong mode is expensive in
            either direction -- a missed order, or an order that was never
            meant to exist. */}
        <td className="py-2 pr-4">
          <span
            className={
              strategy.alert_only
                ? 'rounded border border-amber-700 bg-amber-950/40 px-2 py-0.5 text-xs text-amber-300'
                : 'rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-300'
            }
          >
            {strategy.alert_only ? '只提醒' : '會下單'}
          </span>
        </td>
        <td className="py-2 pr-4 text-slate-400">{strategy.last_signal ?? '—'}</td>
        <td className="py-2 pr-4 text-red-400">
          {strategy.consecutive_errors > 0 ? strategy.last_error : ''}
        </td>
        <td className="flex gap-2 py-2">
          <button
            disabled={toggleMutation.isPending}
            onClick={() => toggleMutation.mutate()}
            className="rounded bg-slate-700 px-3 py-1 text-sm font-medium text-white hover:bg-slate-600 disabled:opacity-50"
          >
            {strategy.is_active ? '停用' : '啟用'}
          </button>
          <button
            onClick={() => setEditing((v) => !v)}
            className="rounded bg-slate-700 px-3 py-1 text-sm font-medium text-white hover:bg-slate-600"
          >
            編輯
          </button>
          <button
            disabled={deleteMutation.isPending}
            onClick={handleDelete}
            className="rounded bg-red-900 px-3 py-1 text-sm font-medium text-red-200 hover:bg-red-800 disabled:opacity-50"
          >
            刪除
          </button>
        </td>
      </tr>
      {editing && (
        <tr>
          <td colSpan={7} className="pb-4">
            <EditStrategyForm strategy={strategy} onDone={() => setEditing(false)} />
          </td>
        </tr>
      )}
    </>
  )
}

function NewStrategyForm({ onDone, samples }: { onDone: () => void; samples: SampleStrategy[] }) {
  const [name, setName] = useState('')
  const [symbol, setSymbol] = useState('')
  const [sourceCode, setSourceCode] = useState('')
  const [alertOnly, setAlertOnly] = useState(false)
  const [description, setDescription] = useState('')
  const [validation, setValidation] = useState<StrategyValidateResult | null>(null)
  const [createError, setCreateError] = useState<string | null>(null)
  const [generateError, setGenerateError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  // Fill in from the code's own self.name/self.symbol so a sample, pasted
  // code or an AI answer doesn't have to be retyped -- but never overwrite
  // something the user already typed themselves.
  function prefillDetected(result: StrategyValidateResult) {
    if (!name && result.detected_name) setName(result.detected_name)
    if (!symbol && result.detected_symbol) setSymbol(result.detected_symbol)
  }

  const validateMutation = useMutation({
    mutationFn: () =>
      api.post<StrategyValidateResult>('/api/strategies/validate', { source_code: sourceCode }),
    onSuccess: (result) => {
      setValidation(result)
      if (result.ok) prefillDetected(result)
    },
  })

  const generateMutation = useMutation({
    mutationFn: () =>
      api.post<StrategyGenerateResult>('/api/strategies/generate', {
        description: description.trim(),
        symbol: symbol.trim() || null,
      }),
    onSuccess: (result) => {
      // The endpoint already compiled and tick-tested what it wrote, so its
      // answer doubles as the 驗證 outcome and the owner lands exactly where
      // 建立 expects them. Code that failed validation still goes in the box:
      // it can be read and fixed, which beats an error with nothing attached.
      if (result.source_code) {
        setSourceCode(result.source_code)
        setValidation(result)
        prefillDetected(result)
      } else {
        setGenerateError(result.error ?? GENERATE_FAILED)
      }
    },
    onError: (err) => setGenerateError(err instanceof ApiError ? err.message : GENERATE_FAILED),
  })

  function handleGenerate() {
    if (!description.trim()) return
    if (sourceCode && !window.confirm(OVERWRITE_CONFIRM)) return
    setValidation(null)
    setCreateError(null)
    setGenerateError(null)
    generateMutation.mutate()
  }

  function applySample(sample: SampleStrategy) {
    setSourceCode(sample.source_code)
    setValidation(null)
    setCreateError(null)
    setGenerateError(null)
  }

  const createMutation = useMutation({
    mutationFn: () =>
      api.post<Strategy>('/api/strategies', {
        name,
        symbol,
        source_code: sourceCode,
        alert_only: alertOnly,
      }),
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
      <div className="space-y-2 rounded border border-slate-800 p-3">
        <p className="text-sm font-semibold text-slate-300">用 AI 產生策略</p>
        {/* Always on screen, with no way to dismiss it: an active strategy's
            signals turn into real orders. */}
        <p className="rounded border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-300">
          AI 產生的程式碼務必自己讀過、看懂每一行在做什麼，再決定要不要啟用。這不是投資建議；策略一旦啟用，系統就會照它給的訊號送出真實委託，盈虧由你自己承擔。
        </p>
        <label htmlFor="strategy-ai-description" className="text-sm text-slate-400">
          想要的策略（用中文描述就可以）
        </label>
        <textarea
          id="strategy-ai-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={3}
          placeholder="例如：台積電，5 日均線由下往上穿過 20 日均線就買進，反向穿過就賣出"
          className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-sm"
        />
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={generateMutation.isPending || !description.trim()}
            onClick={handleGenerate}
            className="rounded bg-slate-700 px-3 py-1 text-sm font-medium hover:bg-slate-600 disabled:opacity-50"
          >
            {generateMutation.isPending ? '產生中…' : 'AI 產生策略'}
          </button>
          {generateMutation.isPending && (
            <span className="text-sm text-slate-400">
              AI 正在寫程式，通常要數十秒，請耐心等一下。不要重複點擊——每按一次都會用掉當天的額度。
            </span>
          )}
        </div>
        {generateError && <p className="text-sm text-red-400">{generateError}</p>}
      </div>

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
      <AlertOnlyField id="strategy-alert-only" checked={alertOnly} onChange={setAlertOnly} />
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

      {/* Both are locked while the AI writes: the answer is about to replace
          the source box, and 建立 would close the form over the top of an
          answer the daily quota has already been spent on. */}
      <div className="flex gap-2">
        <button
          disabled={validateMutation.isPending || generateMutation.isPending || !sourceCode}
          onClick={() => validateMutation.mutate()}
          className="rounded bg-slate-700 px-3 py-1 text-sm font-medium hover:bg-slate-600 disabled:opacity-50"
        >
          驗證
        </button>
        <button
          disabled={
            createMutation.isPending ||
            generateMutation.isPending ||
            !name ||
            !symbol ||
            !sourceCode
          }
          onClick={() => createMutation.mutate()}
          className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          建立
        </button>
      </div>
    </div>
  )
}

function AlertHistory({ strategies }: { strategies: Strategy[] }) {
  // Already newest-first from the API; rendered in that order so the last
  // thing a strategy said is the first thing on screen.
  const alertsQuery = useQuery({
    queryKey: ['strategy-alerts'],
    queryFn: () => api.get<StrategyAlert[]>('/api/alerts'),
  })
  const alerts = alertsQuery.data ?? []
  const nameFor = (strategyId: number) =>
    strategies.find((s) => s.id === strategyId)?.name ?? `#${strategyId}`

  return (
    <div className="space-y-2">
      <h2 className="text-sm font-semibold text-slate-300">提醒紀錄</h2>
      <p className="text-xs text-slate-500">
        只提醒策略發出過的訊號都記在這裡。這些都沒有下單，可以拿來回頭檢視這個策略準不準，再決定要不要讓它真的下單。
      </p>
      {alerts.length === 0 && alertsQuery.isSuccess && (
        <p className="text-slate-500">目前沒有提醒紀錄。</p>
      )}
      {alerts.length > 0 && (
        <table aria-label="提醒紀錄" className="w-full text-left text-sm">
          <thead className="text-slate-500">
            <tr>
              <th className="pb-2 font-normal">時間</th>
              <th className="pb-2 font-normal">策略</th>
              <th className="pb-2 font-normal">股票代號</th>
              <th className="pb-2 font-normal">方向</th>
              <th className="pb-2 font-normal">價格</th>
              <th className="pb-2 font-normal">通知</th>
            </tr>
          </thead>
          <tbody>
            {alerts.map((alert) => (
              <tr key={alert.id} className="border-b border-slate-800 text-slate-300">
                <td className="py-2 pr-4 text-slate-500">
                  {new Date(alert.created_at).toLocaleString()}
                </td>
                <td className="py-2 pr-4 font-medium">{nameFor(alert.strategy_id)}</td>
                <td className="py-2 pr-4">{alert.symbol}</td>
                <td
                  className={`py-2 pr-4 ${alert.side === 'buy' ? 'text-emerald-400' : 'text-red-400'}`}
                >
                  {SIDE_LABEL[alert.side]}
                </td>
                <td className="py-2 pr-4">{alert.price}</td>
                {/* The signal still counts when scoring the strategy, but a
                    failed row is one the owner never saw -- unmarked it would
                    read as a notification that arrived. */}
                <td className="py-2 text-slate-500">
                  {alert.status === 'sent' ? (
                    '已送出'
                  ) : (
                    <span className="text-red-400" title={alert.error ?? undefined}>
                      未送達
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
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

  const strategies = strategiesQuery.data ?? []

  return (
    <div className="space-y-6">
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
            <th className="pb-2 font-normal">模式</th>
            <th className="pb-2 font-normal">最新訊號</th>
            <th className="pb-2 font-normal">錯誤</th>
            <th className="pb-2 font-normal">操作</th>
          </tr>
        </thead>
        <tbody>
          {strategies.map((strategy) => (
            <StrategyRow key={strategy.id} strategy={strategy} />
          ))}
        </tbody>
      </table>

      <AlertHistory strategies={strategies} />
    </div>
  )
}
