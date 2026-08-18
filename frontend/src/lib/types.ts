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

export interface Strategy {
  id: number
  name: string
  symbol: string
  data_source: DataSource
  is_active: boolean
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

export interface StrategyValidateResult {
  ok: boolean
  error: string | null
  detected_name: string | null
  detected_symbol: string | null
  sample_signals: string[] | null
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
