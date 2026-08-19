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
  filled_quantity: string | null
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
  /** Why the last signal did not become an order -- a capital ceiling, a
   * cooldown, an identical order already pending. Distinct from last_error:
   * the strategy worked, the risk gate refused it. Cleared as soon as an
   * order gets through. */
  last_blocked_reason: string | null
  last_blocked_at: string | null
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
  /** What the position is worth now, from the same market_quotes rows the
   * stop-loss scan reads. All four are null together when no quote has
   * reached this symbol -- not zero, which would read as "flat". */
  current_price: string | null
  market_value: string | null
  unrealized_pnl: string | null
  unrealized_pnl_pct: string | null
  quote_time: string | null
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
  /** Hours in the owner's own timezone during which this channel stays
   * silent. Both null means always on. A notification raised inside the
   * window is held and delivered when it ends, never dropped. */
  quiet_start_hour: number | null
  quiet_end_hour: number | null
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

/** Which price the simulation assumed a signal was filled at.
 * `next_open` is the honest one -- you only learn a close once the candle is
 * over, so the earliest you could have acted on it is the next open. */
export type FillPriceBasis = 'next_open' | 'close'

/** What the simulation charged for. Every backtest response echoes this back,
 * because a return figure read without its costs is not a number anyone can
 * act on. Rates are decimal fractions, not percentages: 0.001425 is 0.1425%. */
export interface BacktestAssumptions {
  fill_price_basis: FillPriceBasis
  commission_rate: string
  slippage_rate: string
  sell_tax_rate: string
  quantity: string
  initial_capital: string
}

/** One realized round trip. The prices are the simulated fills, so they
 * already carry slippage, commission and tax -- pnl is net. */
export interface BacktestTrade {
  opened_at: string
  closed_at: string
  quantity: string
  entry_price: string
  exit_price: string
  pnl: string
  return_pct: string
}

/** One point of the equity chart: the account marked to that candle's close. */
export interface EquityPoint {
  timestamp: string
  close: string
  position_qty: string
  cash: string
  equity: string
}

export interface BacktestSummary {
  bars_total: number
  bars_tested: number
  signals: number
  skipped_signals: number
  unfilled_signals: number
  trade_count: number
  wins: number
  losses: number
  /** null, not 0, when nothing ever traded -- "0% 勝率" reads as a strategy
   * that lost every time rather than one that never opened a position. */
  win_rate_pct: string | null
  average_win: string | null
  average_loss: string | null
  net_pnl: string
  total_costs: string
  total_return_pct: string
  max_drawdown_pct: string
  final_equity: string
  open_quantity: string
  open_avg_entry_price: string
  /** What doing nothing would have returned over the same bars, and the
   * strategy's return minus that. null when there were no bars to hold. */
  buy_and_hold_return_pct: string | null
  excess_return_pct: string | null
  /** Gross profit over gross loss. null when nothing lost -- not a ratio. */
  profit_factor: string | null
  /** How much of the tested period the money was actually at risk. */
  exposure_pct: string | null
}

export interface BacktestResult {
  strategy_name: string
  symbol: string
  timeframe: string
  entry_point: string
  warmup_bars: number
  first_bar_at: string | null
  last_bar_at: string | null
  assumptions: BacktestAssumptions
  /** What the simulation assumed, already in Traditional Chinese. */
  assumption_notes: string[]
  /** What happened in this particular run that the owner should know about:
   * signals that could not be acted on, a position still open at the end, a
   * range too short to test at all. */
  notes: string[]
  trades: BacktestTrade[]
  equity_curve: EquityPoint[]
  summary: BacktestSummary
}

/** One row of GET /api/backtests. Deliberately carries no equity curve --
 * the list is for scanning. */
export interface BacktestRun {
  id: number
  strategy_id: number | null
  strategy_name: string
  symbol: string
  timeframe: string
  data_source: DataSource
  range_start: string
  range_end: string
  created_at: string
  assumptions: BacktestAssumptions
  summary: BacktestSummary
}

/** POST /api/backtests, and GET /api/backtests/{id}: one run in full,
 * including the source it actually scored. */
export interface BacktestRunDetail extends BacktestRun {
  source_code: string
  code_hash: string
  result: BacktestResult
}

/** One entry in the backtest form's 券商 dropdown.
 *
 * Served rather than hard-coded here so the rates live next to the tests that
 * pin them. `note` is not decoration: discount tiers vary per customer, and a
 * preset read as a promise is worse than no preset at all. */
export interface BrokerCostPreset {
  id: string
  label: string
  market: string
  commission_rate: string
  minimum_fee: string
  sell_tax_rate: string
  note: string
}

/** One symbol on the dashboard's quote table.
 *
 * In the database rather than the browser: it used to live in localStorage,
 * so it was empty on the phone, empty on a second computer, and gone after
 * clearing browsing data. */
export interface WatchlistItem {
  id: number
  symbol: string
  data_source: DataSource
  created_at: string
}

/** One recorded TradingView call. Failures are included on purpose -- a wrong
 * secret or malformed JSON is precisely the row somebody is looking for. */
export interface WebhookLog {
  id: number
  received_at: string
  remote_ip: string | null
  signature_valid: boolean
  parsed_ok: boolean
  raw_body: string
  order_id: number | null
  error: string | null
}

/** What to paste into TradingView. Served rather than documented, because a
 * URL in a docs page is a URL nobody finds. */
export interface WebhookSetup {
  url: string
  example_message: string
  notes: string[]
}
