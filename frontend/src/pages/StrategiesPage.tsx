import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from '../lib/api'
import { DeleteButton } from '../components/DeleteButton'
import { SymbolInput } from '../components/SymbolInput'
import { StrategyParams, type ParamValue } from '../components/StrategyParams'
import { Pager } from '../components/Pager'
import { StrategyScorecard } from '../components/StrategyScorecard'
import { ExportButton } from '../components/ExportButton'
import { RISK_FIELDS, isSwitchedOff, offSwitchLabel } from '../lib/riskFields'
import type {
  DataSource,
  IndicatorCatalogue,
  OrderSide,
  RiskSettings,
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

// The runtime's own candle sizes, in the words the owner uses for them. Kept
// as a lookup rather than shown raw: "1wk" is the value yfinance wants, not
// something a non-programmer should have to recognise.
const TIMEFRAME_LABEL: Record<string, string> = {
  '1m': '1 分線',
  '5m': '5 分線',
  '15m': '15 分線',
  '30m': '30 分線',
  '1h': '1 小時線',
  '4h': '4 小時線',
  // Crypto only -- Yahoo refuses the interval outright. The strategy save
  // endpoint refuses the pair, so this label exists to name an existing
  // crypto strategy rather than to offer 12h on a stock.
  '12h': '12 小時線',
  '1d': '日線',
  '1wk': '週線',
  '1mo': '月線',
}

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

/** What the validator worked out about the code, in one line.
 *
 * The entry point is part of it because on_tick and on_bar read almost alike:
 * a strategy the owner asked for in 週線 that quietly came back reacting to
 * every quote looks exactly like one that did as it was told. */
function detectionSummary(result: StrategyValidateResult): string {
  const detected = `偵測到：${result.detected_name}（${result.detected_symbol}）`
  if (result.entry_point === 'on_bar') {
    const candle = TIMEFRAME_LABEL[result.timeframe ?? ''] ?? result.timeframe
    return `${detected}・每根${candle}收盤時判斷`
  }
  if (result.entry_point === 'on_tick') {
    return `${detected}・每次報價更新時判斷`
  }
  return detected
}

function ValidationSummary({
  validation,
  formSymbol,
}: {
  validation: StrategyValidateResult
  formSymbol: string
}) {
  // The code's own self.symbol is a LABEL -- what actually gets polled is the
  // 代號 field, which the form validates on its own. So when the two agree,
  // the field is already saying this and repeating it here is noise.
  //
  // When they DISAGREE the field says nothing, because the field's value is
  // fine. 「偵測到：均線（台積電）」 then sits there in green next to a perfectly
  // valid 2330.TW, and the only thing on screen that is wrong is the one line
  // nobody is being asked to look at.
  const mismatched =
    validation.symbol_problem && validation.detected_symbol !== formSymbol.trim()
  return (
    <>
      <p className={validation.ok ? 'text-emerald-400' : 'text-red-400'}>
        {validation.ok ? detectionSummary(validation) : validation.error}
      </p>
      {mismatched && (
        <p className="text-amber-400">
          程式碼裡的 self.symbol 是「{validation.detected_symbol}」，這個代號抓不到報價。
          實際輪詢的是上面的代號欄位，所以策略還是會跑，但程式碼裡的標的和它盯的
          標的不是同一個。{validation.symbol_problem}
        </p>
      )}
    </>
  )
}

/** The indicator catalogue, on demand.
 *
 * The runtime ships 40 verified indicators, and nothing in the UI said so:
 * the owner had to guess what was available, and a description naming
 * something that does not exist comes back hand-rolled -- which is the exact
 * failure the catalogue was built to remove. Fetched only when opened, since
 * most visits to this form never need it. */
function IndicatorCatalogueBrowser() {
  const [open, setOpen] = useState(false)
  const catalogueQuery = useQuery({
    queryKey: ['indicators'],
    queryFn: () => api.get<IndicatorCatalogue>('/api/indicators'),
    enabled: open,
  })
  const catalogue = catalogueQuery.data

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="rounded border border-slate-700 px-3 py-1 text-sm hover:bg-slate-800"
      >
        {open ? '收合指標清單' : '看看有哪些指標可以用'}
      </button>
      {open && (
        <div className="space-y-3 rounded border border-slate-800 bg-slate-950/60 p-3">
          <p className="text-xs text-slate-500">
            這些指標系統都算好了，描述策略時直接講名字（例如「RSI」「MACD 快慢線」）就可以，不用自己算，也不要請
            AI 自己寫一個——手寫的指標很容易差一點點，而且從結果看不出來。
          </p>
          {catalogueQuery.isError && (
            <p className="text-sm text-red-400">指標清單讀取失敗，請重新整理再試。</p>
          )}
          {catalogue?.categories.map((category) => (
            <div key={category.name} className="space-y-1">
              <p className="text-sm font-semibold text-slate-300">
                {category.label}（{category.count}）
              </p>
              <ul className="space-y-1">
                {catalogue.indicators
                  .filter((indicator) => indicator.category === category.name)
                  .map((indicator) => (
                    <li key={indicator.name} className="border-l border-slate-800 pl-2">
                      <p className="font-mono text-xs text-emerald-300">{indicator.signature}</p>
                      <p className="text-sm text-slate-300">{indicator.title}</p>
                      <p className="text-xs text-slate-500">{indicator.description}</p>
                    </li>
                  ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** Order size and data feed.
 *
 * Both existed as columns the backend honoured and neither had a field, so
 * every strategy ever created here traded one unit of a yfinance symbol --
 * `default_quantity` defaults to 1 and `data_source` to yfinance, and nothing
 * on screen could change either. */
function TradingFields({
  idPrefix,
  quantity,
  onQuantity,
  dataSource,
  onDataSource,
}: {
  idPrefix: string
  quantity: string
  onQuantity: (value: string) => void
  dataSource: DataSource
  onDataSource: (value: DataSource) => void
}) {
  return (
    <div className="flex flex-wrap gap-4">
      <div>
        <label htmlFor={`${idPrefix}-quantity`} className="text-sm text-slate-400">
          每次下單數量
        </label>
        <input
          id={`${idPrefix}-quantity`}
          value={quantity}
          onChange={(e) => onQuantity(e.target.value)}
          className="block w-40 rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
        <p className="mt-1 text-xs text-slate-500">
          這支策略每次發出訊號要買賣多少單位。台股整股請填 1000 的倍數。
        </p>
      </div>
      <div>
        <label htmlFor={`${idPrefix}-data-source`} className="text-sm text-slate-400">
          資料來源
        </label>
        <select
          id={`${idPrefix}-data-source`}
          value={dataSource}
          onChange={(e) => onDataSource(e.target.value as DataSource)}
          className="block w-40 rounded border border-slate-700 bg-slate-950 px-2 py-1"
        >
          <option value="yfinance">Yahoo（台股／美股）</option>
          <option value="binance">Binance（加密貨幣）</option>
        </select>
        <p className="mt-1 text-xs text-slate-500">
          代號要跟來源相符：Yahoo 用 2330.TW、AAPL，Binance 用 BTCUSDT。
        </p>
      </div>
    </div>
  )
}

function StrategyStatusNote({ strategy }: { strategy: Strategy }) {
  if (strategy.consecutive_errors > 0) {
    return <span className="text-xs text-red-400">{strategy.last_error}</span>
  }
  if (strategy.last_blocked_reason) {
    return (
      <span className="text-xs text-amber-300">
        訊號被風控擋下：{strategy.last_blocked_reason}
      </span>
    )
  }
  // Not an error, despite the column it travels in: the backend puts the
  // warm-up progress here because there was nowhere else to put it.
  if (strategy.last_error) {
    return <span className="text-xs text-sky-300">{strategy.last_error}</span>
  }
  return <span className="text-xs text-slate-600">—</span>
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

const RISK_OVERRIDE_LABEL = '使用個別風險設定'
const RISK_OVERRIDE_HELP =
  '不打開的話，這個策略沿用「風險設定」頁的全域數字，跟現在完全一樣。打開之後，只填你真的要改的欄位就好。'
/** Three states, and two of them look like an empty box unless the form says
 * otherwise. Inheriting and switched-off are expensive in opposite
 * directions -- a strategy meant to run without a stop-loss silently falling
 * back on the global one, or vice versa -- so neither may be left to be
 * inferred. */
const ALERT_PAGE_SIZE = 50

const RISK_THREE_STATES = [
  '留空＝沿用全域設定',
  '勾開關＝這個策略關掉它（不限制／不設停損）',
  '填數字＝只有這個策略用這個數字',
]

/** One override box per knob, as raw text: what the owner typed, before it is
 * read as a number or as "leave this one alone". */
type RiskOverrideValues = Record<keyof RiskSettings, string>

function emptyOverrides(): RiskOverrideValues {
  return Object.fromEntries(RISK_FIELDS.map((f) => [f.key, ''])) as RiskOverrideValues
}

function overridesOf(strategy: Strategy): RiskOverrideValues {
  return Object.fromEntries(
    RISK_FIELDS.map((f) => [f.key, strategy[f.key] === null ? '' : String(strategy[f.key])]),
  ) as RiskOverrideValues
}

function hasRiskOverrides(strategy: Strategy): boolean {
  return RISK_FIELDS.some((f) => strategy[f.key] !== null)
}

/** A blank box means "keep inheriting this one", which the API spells as
 * null. Never 0 -- for 本金 and 單筆最大金額 that would mean "no ceiling", and
 * for the rest a real limit of zero, so guessing wrong here is expensive in
 * both directions. */
function overridePayload(
  values: RiskOverrideValues,
  enabled: boolean,
): Record<string, string | null> {
  return Object.fromEntries(
    RISK_FIELDS.map((f) => {
      const typed = enabled ? values[f.key].trim() : ''
      return [f.key, typed === '' ? null : typed]
    }),
  )
}

function RiskOverrideFields({
  idPrefix,
  enabled,
  onToggle,
  values,
  onChange,
}: {
  idPrefix: string
  enabled: boolean
  onToggle: (value: boolean) => void
  values: RiskOverrideValues
  onChange: (values: RiskOverrideValues) => void
}) {
  // The same key the risk settings page uses, so the globals shown here are
  // whatever that page last saved rather than a second copy going stale.
  const globalsQuery = useQuery({
    queryKey: ['risk-settings'],
    queryFn: () => api.get<RiskSettings>('/api/risk-settings'),
  })
  const globals = globalsQuery.data

  return (
    <div className="space-y-2">
      <label htmlFor={`${idPrefix}-risk-override`} className="flex items-center gap-2 text-sm">
        <input
          id={`${idPrefix}-risk-override`}
          type="checkbox"
          checked={enabled}
          onChange={(e) => onToggle(e.target.checked)}
        />
        {RISK_OVERRIDE_LABEL}
      </label>
      <p className="text-xs text-slate-500">{RISK_OVERRIDE_HELP}</p>

      {enabled && (
        <div className="space-y-3 rounded border border-slate-800 p-3">
          <ul className="list-inside list-disc rounded border border-amber-700 bg-amber-950/40 px-3 py-2 text-xs text-amber-200">
            {RISK_THREE_STATES.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          {RISK_FIELDS.map((field) => {
            // Until the globals land there is no number to name; the box still
            // works and still means "inherit", it just cannot say what from.
            const inherited = globals ? String(globals[field.key]) : null
            const raw = values[field.key]
            const off = isSwitchedOff(raw)
            const protection = field.kind === 'protection'
            const badge = off ? '已關閉' : raw.trim() === '' ? '沿用全域' : `自訂：${raw.trim()}`
            return (
              <div key={field.key} data-risk-field={field.key}>
                <div className="flex items-center justify-between gap-2">
                  <label htmlFor={`${idPrefix}-${field.key}`} className="text-sm text-slate-400">
                    {field.label}
                  </label>
                  <span
                    className={`rounded border px-2 py-0.5 text-xs ${
                      off
                        ? 'border-amber-700 bg-amber-950/40 text-amber-300'
                        : raw.trim() === ''
                          ? 'border-slate-700 text-slate-500'
                          : 'border-sky-700 bg-sky-950/40 text-sky-300'
                    }`}
                  >
                    {badge}
                  </span>
                </div>
                <input
                  id={`${idPrefix}-${field.key}`}
                  // Switched off shows an empty disabled box, not a literal 0 --
                  // the same reason the global page does it, so the two forms
                  // cannot teach opposite readings of the same number.
                  value={off ? '' : raw}
                  disabled={off}
                  onChange={(e) => onChange({ ...values, [field.key]: e.target.value })}
                  placeholder={inherited === null ? '沿用全域' : `沿用全域：${inherited}`}
                  className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 disabled:opacity-50"
                />
                <label
                  className={`mt-1 flex items-center gap-2 text-xs ${
                    protection ? 'text-amber-300' : 'text-slate-400'
                  }`}
                >
                  <input
                    type="checkbox"
                    aria-label={offSwitchLabel(field)}
                    checked={off}
                    onChange={(e) =>
                      onChange({ ...values, [field.key]: e.target.checked ? '0' : '' })
                    }
                  />
                  {field.offLabel}
                </label>
                {off && field.offWarning && (
                  <p className="mt-1 rounded border border-amber-700 bg-amber-950/40 px-2 py-1 text-xs text-amber-200">
                    {field.offWarning}
                  </p>
                )}
                {(field.strategyHelp ?? field.help) && (
                  <p className="mt-1 text-xs text-slate-500">
                    {field.strategyHelp ?? field.help}
                  </p>
                )}
              </div>
            )
          })}
        </div>
      )}
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
  const [quantity, setQuantity] = useState(strategy.default_quantity)
  const [dataSource, setDataSource] = useState<DataSource>(strategy.data_source)
  // Same reasoning: the eight override columns ride on the list row, so the
  // toggle never renders off for a strategy that has its own settings.
  const [riskOverride, setRiskOverride] = useState(() => hasRiskOverrides(strategy))
  const [riskValues, setRiskValues] = useState(() => overridesOf(strategy))
  const [sourceCode, setSourceCode] = useState<string | null>(null)
  const [validation, setValidation] = useState<StrategyValidateResult | null>(null)
  const [params, setParams] = useState<Record<string, ParamValue>>(strategy.params ?? {})
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
      const payload: Record<string, unknown> = {
        name,
        symbol,
        alert_only: alertOnly,
        default_quantity: quantity,
        data_source: dataSource,
      }
      // Only send source_code once the real one has loaded, so a save that
      // races the fetch can't overwrite the stored code with an empty box.
      if (sourceCode !== null) payload.source_code = sourceCode
      // PATCH leaves absent fields alone, so a strategy that neither has
      // overrides nor wants any sends none. Switching the toggle off has to
      // send the nulls explicitly -- that is what empties the columns.
      if (riskOverride || hasRiskOverrides(strategy)) {
        Object.assign(payload, overridePayload(riskValues, riskOverride))
      }
      payload.params = params
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
      <SymbolInput
        id={`edit-strategy-symbol-${strategy.id}`}
        label="股票代號"
        value={symbol}
        onChange={setSymbol}
      />
      <TradingFields
        idPrefix={`edit-strategy-${strategy.id}`}
        quantity={quantity}
        onQuantity={setQuantity}
        dataSource={dataSource}
        onDataSource={setDataSource}
      />
      {/* In the edit panel rather than the row: it is the screen the owner
          opens when asking whether to keep this strategy, and a scorecard on
          every row of a list is noise. */}
      <StrategyScorecard strategyId={strategy.id} />
      <AlertOnlyField
        id={`edit-strategy-alert-only-${strategy.id}`}
        checked={alertOnly}
        onChange={setAlertOnly}
      />
      <RiskOverrideFields
        idPrefix={`edit-strategy-${strategy.id}`}
        enabled={riskOverride}
        onToggle={setRiskOverride}
        values={riskValues}
        onChange={setRiskValues}
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

      {/* Rendered off the VALIDATOR's answer: it is the only thing that knows
          what the current source declares. Validate, then tune. */}
      {validation?.declared_params && (
        <StrategyParams
          declared={validation.declared_params}
          value={params}
          onChange={setParams}
        />
      )}
      {validation && <ValidationSummary validation={validation} formSymbol={symbol} />}
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
        {/* Which risk numbers this strategy is actually running on. An
            override is invisible from the rest of the row, and a strategy
            silently on someone else's limits is the thing this column exists
            to stop. */}
        <td className="py-2 pr-4">
          <span
            className={
              hasRiskOverrides(strategy)
                ? 'rounded border border-sky-700 bg-sky-950/40 px-2 py-0.5 text-xs text-sky-300'
                : 'rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-400'
            }
          >
            {hasRiskOverrides(strategy) ? '自訂' : '全域'}
          </span>
        </td>
        <td className="py-2 pr-4" data-testid="order-size">
          {strategy.default_quantity}
        </td>
        <td className="py-2 pr-4 text-slate-400">{strategy.last_signal ?? '—'}</td>
        {/* Three different reasons a strategy can look idle, and only one of
            them used to show. `consecutive_errors > 0` hid the warm-up note
            the backend already writes into last_error, and a signal refused
            by the risk gate had nowhere to appear at all -- so "still warming
            up", "blocked by its own capital ceiling" and "nothing to say"
            were pixel-identical. */}
        <td className="py-2 pr-4">
          <StrategyStatusNote strategy={strategy} />
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
          <td colSpan={8} className="pb-4">
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
  const [quantity, setQuantity] = useState('1')
  const [dataSource, setDataSource] = useState<DataSource>('yfinance')
  const [riskOverride, setRiskOverride] = useState(false)
  const [riskValues, setRiskValues] = useState(emptyOverrides)
  const [description, setDescription] = useState('')
  const [validation, setValidation] = useState<StrategyValidateResult | null>(null)
  const [params, setParams] = useState<Record<string, ParamValue>>({})
  const [createError, setCreateError] = useState<string | null>(null)
  const [generateError, setGenerateError] = useState<string | null>(null)
  // What the AI asked instead of writing code, and what the owner replies.
  const [question, setQuestion] = useState<string | null>(null)
  const [answer, setAnswer] = useState('')
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
    mutationFn: (clarification: { question: string; answer: string } | null) => {
      const payload: Record<string, unknown> = {
        description: description.trim(),
        symbol: symbol.trim() || null,
      }
      // Only on a retry, and always as a pair: the model is single-turn, so
      // an answer arriving without the question it answers is unreadable.
      if (clarification) {
        payload.question = clarification.question
        payload.answer = clarification.answer
      }
      return api.post<StrategyGenerateResult>('/api/strategies/generate', payload)
    },
    onSuccess: (result) => {
      setQuestion(result.question)
      if (result.question) {
        // Nothing goes in the code box. The description had more than one
        // reading and the model refused to pick one -- filling the box now
        // would show the owner a finished-looking strategy built on a guess
        // they never made.
        setAnswer('')
        return
      }
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

  function startGeneration(clarification: { question: string; answer: string } | null) {
    if (!description.trim()) return
    if (sourceCode && !window.confirm(OVERWRITE_CONFIRM)) return
    setValidation(null)
    setCreateError(null)
    setGenerateError(null)
    generateMutation.mutate(clarification)
  }

  function handleAnswer() {
    if (!question || !answer.trim()) return
    startGeneration({ question, answer: answer.trim() })
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
        default_quantity: quantity,
        data_source: dataSource,
        params,
        // Left out entirely while the toggle is off: the columns default to
        // NULL, which is inherit, so an untouched form sends what it always
        // sent.
        ...(riskOverride ? overridePayload(riskValues, true) : {}),
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
            onClick={() => startGeneration(null)}
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

        {/* Deliberately not styled as an error: being asked something is the
            AI doing its job. The alternative -- letting it guess -- produces
            a strategy that looks finished and does something else, and the
            owner cannot read Python well enough to notice. */}
        {question && (
          <div className="space-y-2 rounded border border-amber-700 bg-amber-950/40 p-3">
            <p className="text-sm font-semibold text-amber-300">AI 需要你先確認一件事</p>
            <p className="text-xs text-amber-200/80">
              你的描述有一個地方可以有兩種以上的解讀，猜錯會變成完全不同的策略，而且從程式碼上你也看不出來它猜了。先回答，再讓它重新寫。
            </p>
            <p className="whitespace-pre-line text-sm text-slate-200">{question}</p>
            <label htmlFor="strategy-ai-answer" className="text-sm text-slate-400">
              你的回答
            </label>
            <textarea
              id="strategy-ai-answer"
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              rows={2}
              placeholder="用中文回答就可以，例如：選（A），兩線的距離還在擴大"
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-sm"
            />
            <button
              type="button"
              disabled={generateMutation.isPending || !answer.trim()}
              onClick={handleAnswer}
              className="rounded bg-amber-700 px-3 py-1 text-sm font-medium text-white hover:bg-amber-600 disabled:opacity-50"
            >
              {generateMutation.isPending ? '重新產生中…' : '回答並重新產生'}
            </button>
          </div>
        )}

        <IndicatorCatalogueBrowser />
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
      {/* A strategy is what actually produces alerts, so a symbol that cannot
          price here is a strategy that runs forever and never fires. */}
      <SymbolInput id="strategy-symbol" label="股票代號" value={symbol} onChange={setSymbol} />
      <TradingFields
        idPrefix="strategy"
        quantity={quantity}
        onQuantity={setQuantity}
        dataSource={dataSource}
        onDataSource={setDataSource}
      />
      <AlertOnlyField id="strategy-alert-only" checked={alertOnly} onChange={setAlertOnly} />
      <RiskOverrideFields
        idPrefix="strategy"
        enabled={riskOverride}
        onToggle={setRiskOverride}
        values={riskValues}
        onChange={setRiskValues}
      />
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

      {/* Rendered off the VALIDATOR's answer: it is the only thing that knows
          what the current source declares. Validate, then tune. */}
      {validation?.declared_params && (
        <StrategyParams
          declared={validation.declared_params}
          value={params}
          onChange={setParams}
        />
      )}
      {validation && <ValidationSummary validation={validation} formSymbol={symbol} />}
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
  // The whole point of 只提醒 mode is watching one strategy to see whether it
  // is any good, and every strategy's alerts were mixed into one table that
  // stopped at fifty. Two strategies in and they drown each other out.
  const [alertStrategyId, setAlertStrategyId] = useState('')
  const [alertOffset, setAlertOffset] = useState(0)
  const alertsQuery = useQuery({
    queryKey: ['strategy-alerts', alertStrategyId, alertOffset],
    queryFn: () => {
      const params = new URLSearchParams({
        limit: String(ALERT_PAGE_SIZE),
        offset: String(alertOffset),
      })
      if (alertStrategyId) params.set('strategy_id', alertStrategyId)
      return api.get<StrategyAlert[]>(`/api/alerts?${params.toString()}`)
    },
  })
  const alerts = alertsQuery.data ?? []
  const alertQueryClient = useQueryClient()

  const deleteAlert = useMutation({
    mutationFn: (id: number) => api.delete(`/api/alerts/${id}`),
    onSuccess: () => alertQueryClient.invalidateQueries({ queryKey: ['alerts'] }),
  })

  const clearAlerts = useMutation({
    mutationFn: () => api.delete<{ deleted: number }>('/api/alerts'),
    onSuccess: () => alertQueryClient.invalidateQueries({ queryKey: ['alerts'] }),
  })
  const nameFor = (strategyId: number) =>
    strategies.find((s) => s.id === strategyId)?.name ?? `#${strategyId}`

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-300">提醒紀錄</h2>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="alert-strategy-filter" className="text-sm text-slate-400">
            只看
          </label>
          <select
            id="alert-strategy-filter"
            value={alertStrategyId}
            onChange={(e) => {
              setAlertStrategyId(e.target.value)
              // Keeping the page would show an empty page 2 of a one-page
              // result.
              setAlertOffset(0)
            }}
            className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-sm"
          >
            <option value="">全部策略</option>
            {/* The symbol is on the option too: two strategies can share a
                name prefix, and this is the only place they appear side by
                side with nothing else to tell them apart. */}
            {strategies.map((s) => (
              <option key={s.id} value={String(s.id)}>
                {s.name}（{s.symbol}）
              </option>
            ))}
          </select>
          {alerts.length > 0 && <ExportButton resource="alerts" label="匯出 CSV" />}
        </div>
        {alerts.length > 0 && (
          <DeleteButton
            what="全部的提醒紀錄"
            label="清空全部"
            tone="loud"
            onConfirm={() => clearAlerts.mutate()}
            pending={clearAlerts.isPending}
            error={clearAlerts.error}
          />
        )}
      </div>
      <p className="text-xs text-slate-500">
        只提醒策略發出過的訊號都記在這裡。這些都沒有下單，可以拿來回頭檢視這個策略準不準，再決定要不要讓它真的下單。
      </p>
      {alerts.length === 0 && alertsQuery.isSuccess && (
        <p className="text-slate-500">目前沒有提醒紀錄。</p>
      )}
      {alerts.length > 0 && (
        <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0 [&_th]:whitespace-nowrap">
          <table aria-label="提醒紀錄" className="w-full text-left text-sm">
            <thead className="text-slate-500">
              <tr>
                <th className="pb-2 font-normal">時間</th>
                <th className="pb-2 font-normal">策略</th>
                <th className="pb-2 font-normal">股票代號</th>
                <th className="pb-2 font-normal">方向</th>
                <th className="pb-2 font-normal">價格</th>
                <th className="pb-2 font-normal">通知</th>
                <th className="pb-2 font-normal">操作</th>
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
                  <td className="py-2 pr-4 text-slate-500">
                    {alert.status === 'sent' ? (
                      '已送出'
                    ) : (
                      <span className="text-red-400" title={alert.error ?? undefined}>
                        未送達
                      </span>
                    )}
                  </td>
                  <td className="py-2">
                    <DeleteButton
                      what={`${alert.symbol} 這筆提醒`}
                      onConfirm={() => deleteAlert.mutate(alert.id)}
                      pending={deleteAlert.isPending && deleteAlert.variables === alert.id}
                      error={deleteAlert.variables === alert.id ? deleteAlert.error : null}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {alerts.length > 0 && (
        <Pager
          offset={alertOffset}
          pageSize={ALERT_PAGE_SIZE}
          shown={alerts.length}
          onChange={setAlertOffset}
        />
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

      <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0 [&_th]:whitespace-nowrap">
        <table className="w-full text-left text-sm">
          <thead className="text-slate-500">
            <tr>
              <th className="pb-2 font-normal">名稱</th>
              <th className="pb-2 font-normal">股票代號</th>
              <th className="pb-2 font-normal">狀態</th>
              <th className="pb-2 font-normal">模式</th>
              <th className="pb-2 font-normal">風險設定</th>
              <th className="pb-2 font-normal">下單量</th>
              <th className="pb-2 font-normal">最新訊號</th>
              {/* Not just errors any more: warm-up progress and risk-gate
                  refusals land here too, because all three answer the same
                  question -- why is this strategy not doing anything. */}
              <th className="pb-2 font-normal">狀況</th>
              <th className="pb-2 font-normal">操作</th>
            </tr>
          </thead>
          <tbody>
            {strategies.map((strategy) => (
              <StrategyRow key={strategy.id} strategy={strategy} />
            ))}
          </tbody>
        </table>
      </div>

      <AlertHistory strategies={strategies} />
    </div>
  )
}
