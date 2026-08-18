export type OrderStatus = 'pending' | 'confirmed' | 'rejected' | 'expired' | 'failed'
export type OrderSide = 'buy' | 'sell'
export type OrderSource = 'strategy' | 'tradingview' | 'manual'
export type DataSource = 'yfinance' | 'binance'
export type ChannelType = 'line' | 'telegram' | 'email' | 'web_push'

export interface Order {
  id: number
  strategy_id: number | null
  source: OrderSource
  symbol: string
  side: OrderSide
  quantity: string
  signal_price: string | null
  status: OrderStatus
  risk_notes: Record<string, unknown> | null
  reject_reason: string | null
  fill_price: string | null
  filled_at: string | null
  decided_at: string | null
  broker_ref: string | null
  created_at: string
}

/** The eight global risk knobs a strategy may take over for itself.
 *
 * null means inherit the global value, which is what a strategy that never
 * opts in keeps holding. The backend owns that rule in
 * services/risk_resolver.py; this is the shape it reads and writes. */
export interface StrategyRiskOverrides {
  capital: string | null
  stop_loss_pct: string | null
  take_profit_pct: string | null
  max_position_qty: string | null
  max_order_notional: string | null
  max_pending_orders_per_symbol: number | null
  signal_cooldown_sec: number | null
  alert_interval_sec: number | null
}

export interface Strategy extends StrategyRiskOverrides {
  id: number
  name: string
  symbol: string
  data_source: DataSource
  is_active: boolean
  /** Watch-only: the strategy's BUY/SELL signals notify and are recorded as
   * alerts, but never become an order to confirm. */
  alert_only: boolean
  default_quantity: string
  warmup_bars: number
  last_signal: string | null
  last_signal_at: string | null
  last_run_at: string | null
  last_error: string | null
  consecutive_errors: number
}

/** GET /api/strategies/{id} -- unlike the list response, this carries the
 * source so the edit form can prefill it. */
export interface StrategyDetail extends Strategy {
  source_code: string
}

/** One recorded alert from a watch-only strategy. A `failed` row still means
 * the strategy fired -- it is only the delivery that did not arrive. */
export interface StrategyAlert {
  id: number
  strategy_id: number
  symbol: string
  side: OrderSide
  price: string
  status: 'sent' | 'failed'
  error: string | null
  created_at: string
}

export interface StrategyValidateResult {
  ok: boolean
  error: string | null
  detected_name: string | null
  detected_symbol: string | null
  /** "on_tick" (every quote) or "on_bar" (once per closed candle). The two
   * read almost alike in source, so the form says which one it turned out to
   * be rather than leaving it to be inferred. */
  entry_point: string | null
  /** The candle size an on_bar strategy declared; null for on_tick, which has
   * no candles to declare. */
  timeframe: string | null
  sample_signals: string[] | null
}

/** POST /api/strategies/generate -- everything /validate returns plus the
 * code it describes, so one round trip fills the whole form. source_code is
 * null only when generation never got as far as producing any. */
export interface StrategyGenerateResult extends StrategyValidateResult {
  source_code: string | null
  /** Set when the description was ambiguous and the model asked instead of
   * guessing. Never arrives together with source_code: a guess attached to a
   * question would look like a finished strategy. */
  question: string | null
}

export type IndicatorCategoryName = 'trend' | 'momentum' | 'volatility' | 'volume' | 'price'

export interface IndicatorParam {
  name: string
  type: string
  required: boolean
  default: number | string | boolean | null
}

/** One entry of GET /api/indicators -- what the runtime can already compute,
 * so a strategy description never has to guess at what exists. */
export interface Indicator {
  name: string
  category: IndicatorCategoryName
  title: string
  description: string
  signature: string
  result: 'series' | 'series_map' | 'value_map'
  keys: string[]
  params: IndicatorParam[]
}

export interface IndicatorCategoryInfo {
  name: IndicatorCategoryName
  label: string
  count: number
}

export interface IndicatorCatalogue {
  categories: IndicatorCategoryInfo[]
  indicators: Indicator[]
}

export interface SampleStrategy {
  filename: string
  source_code: string
}

export interface Position {
  symbol: string
  quantity: string
  avg_entry_price: string
  realized_pnl: string
  opened_at: string | null
  /** The strategy that opened this position, and therefore whose stop-loss /
   * take-profit thresholds it is scanned under. null means the global
   * settings apply -- a manual order or a TradingView fill. */
  strategy_id: number | null
}

export interface Quote {
  symbol: string
  data_source: DataSource
  price: string
  prev_close: string | null
  change_pct: string | null
  volume: string | null
  quote_time: string | null
}

export interface RiskSettings {
  capital: string
  stop_loss_pct: string
  take_profit_pct: string
  max_position_qty: string
  max_order_notional: string
  max_pending_orders_per_symbol: number
  signal_cooldown_sec: number
  alert_interval_sec: number
}

export interface NotificationChannel {
  id: number
  channel_type: ChannelType
  label: string
  is_enabled: boolean
  subscribed_events: string[] | null
  last_sent_at: string | null
  last_error: string | null
  config_preview: string
}

export interface NotificationLog {
  id: number
  channel_id: number
  order_id: number | null
  event: string
  status: 'sent' | 'failed'
  error: string | null
  created_at: string
}

export interface BrokerCredential {
  id: number
  label: string
  broker_name: string
  created_at: string
  config_preview: string
}

export interface AiAssistResult {
  ok: boolean
  reply: string | null
  error: string | null
}

export interface WsEvent {
  type: string
  ts: string
  v: number
  data: Record<string, unknown>
}
