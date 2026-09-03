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
  /** Only what the owner changed. Empty for a strategy that declares no
   * parameters, which is most of them. Storing the whole merged dict would pin
   * the strategy to whatever the defaults were the day it was saved. */
  params: Record<string, number | boolean | string>
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
  /** Why the symbol the code assigned to self.symbol can never produce a
   * price, or null. Separate from `error` because the PYTHON is fine -- the
   * editor printed 「偵測到：均線（2330）」 in green, which reads as approval, and
   * the refusal only arrived at save time from a different field with nothing
   * connecting it to the symbol the AI had chosen. */
  symbol_problem: string | null
  /** What the SOURCE declares in self.params, with the author's own defaults.
   * The form cannot render a field per parameter without being told what they
   * are -- and these are the DEFAULTS, not the values in force, so the page can
   * show 「預設 5，你設成 20」. */
  declared_params: Record<string, number | boolean | string>
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
  /** What `price` is denominated in. A bare number was safe only while the app
   * could show .TW/.TWO and US tickers alone; now that a US ADR and its
   * Taiwanese line can both answer 「台積電」, NT$2,375 and US$300 appear in the
   * same column. null on rows quoted before this existed. */
  currency: string | null
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
  /** web_push only. Not a secret on its own -- delivery also needs the
   * subscription's p256dh/auth keys and the server's VAPID signature -- and it
   * is the only way the browser can tell whether a row is THIS device. */
  push_endpoint: string | null
}

export interface NotificationLog {
  id: number
  /** null when the alert reached NO channel at all -- nobody was told. Before
   * that row existed, such an alert left no trace, so the ledger looked
   * exactly like an afternoon on which nothing had happened. */
  channel_id: number | null
  order_id: number | null
  event: string
  status: 'sent' | 'failed'
  error: string | null
  created_at: string
  /** When the device confirmed it displayed the notification, or null.
   * `status: 'sent'` only ever meant the push service ACCEPTED the message --
   * RFC 8030 §5 says a 2xx "does not indicate that the message was delivered
   * to the user agent" -- which is a far weaker claim than it reads as. */
  delivered_at: string | null
  /** 「so is it still coming or not?」 -- the only question the owner actually
   * asks about a failed alert. `status: 'failed'` was four situations wearing
   * one word: held for quiet hours, between attempts, out of attempts, and
   * 「the channel is gone, so nothing will happen」. Decided by the backend
   * model so the page and the retry sweep cannot drift apart. */
  delivery_state: 'sent' | 'deferred' | 'retrying' | 'given_up'
  attempts: number
  /** The ladder's length, from the server. 「第 3 次」 means nothing without
   * 「共 5 次」, and a 5 hard-coded here is a second copy waiting to disagree. */
  max_attempts: number
  /** When the next send is due, or null when nothing more is owed. */
  next_retry_at: string | null
}

/** POST /api/notifications/channels/{id}/test.
 *
 * `ok` means the channel's transport accepted the message, nothing more. For
 * web push, watch `log_id` for a delivery receipt from the device itself. */
export interface ChannelTestResult {
  ok: boolean
  error: string | null
  log_id: number | null
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
  /** The thresholds the replay enforced, as decimal fractions -- 0.05 is 5%.
   * '0' means it was not simulated. Echoed like any other assumption because
   * a run with a 5% stop and one without are different experiments. */
  stop_loss_pct: string
  take_profit_pct: string
}

/** Why a round trip ended. `signal` is the strategy's own SELL; the other two
 * are the position-level thresholds the live loop also enforces. */
export type ExitReason = 'signal' | 'stop_loss' | 'take_profit'

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
  exit_reason: ExitReason
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
  /** How many exits were forced by a threshold rather than chosen by the
   * strategy. A strategy whose every exit is the stop has been managed, not
   * tested. */
  stop_loss_exits: number
  take_profit_exits: number
  /** Candles that crossed BOTH thresholds, where nothing in the data says
   * which came first. The stop is assumed; these results are estimates. */
  ambiguous_exit_bars: number
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
  /** The scored source's fingerprint. Present on the list row so two runs can
   * be told apart as "same code, different costs" from "different code"
   * without fetching both programs. */
  code_hash: string
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
  /** The alert carried no `id`, so only the identical-body window protects
   * it. Shown so the owner learns it from the page rather than by being
   * replayed. */
  missing_id: boolean
}

/** What to paste into TradingView. Served rather than documented, because a
 * URL in a docs page is a URL nobody finds. */
export interface WebhookSetup {
  url: string
  example_message: string
  notes: string[]
}

/** A strategy's live scorecard.
 *
 * Deliberately on a different basis from the backtest's -- live fills charge
 * no commission or tax yet -- which is why `notes` travels with it. */
export interface StrategyPerformance {
  total_orders: number
  filled_orders: number
  /** null, not 0, when nothing has filled: "0 元" reads as "traded and broke
   * even", which is a different statement from "has not traded". */
  realized_pnl: string | null
  open_quantity: string
  open_cost: string
  bought_value: string
  sold_value: string
  notes: string[]
}

/** Automatic emailed backups.
 *
 * `has_passphrase` rather than the passphrase itself: the worker is the only
 * thing that needs the value, and reading it back would put it in every
 * browser cache for no gain. */
export interface BackupSchedule {
  is_enabled: boolean
  interval_days: number
  to_addr: string | null
  last_sent_at: string | null
  last_error: string | null
  has_passphrase: boolean
}

/** The signed-in account.
 *
 * Two login stamps rather than one: "last login" showing the session you are
 * sitting in tells you nothing -- the one before it is what you can recognise
 * or fail to. */
export interface Account {
  id: number
  email: string
  is_active: boolean
  timezone: string
  last_login_at: string | null
  previous_login_at: string | null
}

/** One candidate from GET /api/symbols/search.
 *
 * `verified` carries the honesty: a Taiwanese listing came out of the
 * exchanges' own registry, while a US ticker is inferred from the shape of
 * what was typed because there is no bundled table to check it against.
 * Presenting both with equal confidence is how somebody ends up watching a
 * symbol that will never price. */
export interface SymbolMatch {
  symbol: string
  name: string
  detail: string
  market: string
  data_source: DataSource
  verified: boolean
  /** What a price for this symbol is denominated in. Half of what separates
   * 2330.TW from TSM: both answer 「台積電」, both price, and the provider names
   * both "Taiwan Semiconductor Manufacturing" -- only 台股·TWD vs 美股·USD says
   * that 220 means two different things. */
  currency: string | null
}

export interface SymbolSearchResponse {
  query: string
  matches: SymbolMatch[]
  /** When the bundled Taiwanese listing table was built. A company that listed
   * after this date is legitimately absent -- a different problem from a typo,
   * and it deserves a different message. */
  listings_generated_at: string | null
  /** And when the bundled US directory was built. Both are reported
   * because an empty result looked in both, and a US company listed last
   * week produces exactly the same empty result as a typo. */
  us_listings_generated_at: string | null
}

/** GET /api/setup/status -- what a fresh deployment still needs.
 *
 * Only served while something IS missing; the endpoint 404s once there is
 * nothing left to configure, which is why a 404 reads as success on the setup
 * page rather than as an error. */
export interface SetupStatus {
  missing: {
    name: string
    /** What breaks if this stays empty, in the owner's language. A list of
     * variable names is what render.yaml already gave them; the reason is the
     * part that decides whether they bother. */
    why: string
    how: string
    /** Non-null means the app can produce this value itself -- the difference
     * between a two-minute setup and installing Python. Null means only the
     * deployer can supply it (DATABASE_URL points at somebody else's service),
     * and offering a button would be a lie. */
    generator: string | null
    /** False means the app boots and works, but something the owner expects
     * to work will not. Shown apart from the blocking ones: 「it will not
     * start」 and 「TradingView will send to the wrong address」 are not the
     * same urgency, and a page that mixes them teaches people to skim. */
    blocking: boolean
    /** Which step of the deploy flow this belongs to. Seven parallel blanks
     * is what render.yaml already gave them; the order is what was missing,
     * and two of these cannot be KNOWN until the step before has happened. */
    step: number
    /** 可以選的做法。空的代表這一格沒有「選哪一種」的問題（一把金鑰就是一把
     * 金鑰）。資料庫是唯一有選擇的那一格，而**雲端使用者能做那個選擇的地方
     * 只有這一頁**：資料庫還沒接上的時候整個 app 是鎖住的，他連帳號都還沒
     * 有，走不到登入之後的設定引導。 */
    options?: {
      kind: string
      label: string
      detail: string
      url: string | null
    }[]
    /** 這一格還要一起貼哪幾個環境變數。多數是空的——一個值就是一個值。
     * 推播那一對不是：只照標題貼一個，每一則推播都會失敗，而畫面上不會有東西
     * 說是因為少了另一半。 */
    also?: string[]
  }[]
  /** Where to paste the answers, in the words of the platform this deployment
   * is actually on -- the backend works that out from the environment
   * (services/hosting.py). The audience has just met their hosting platform
   * and does not know environment variables live behind a menu; naming the
   * WRONG platform's menu is worse than being vague, because they will go
   * looking for it. */
  where: string
}

/** 一格表單，連同它的中文說明。
 *
 * `help` 跟著欄位走，不是寫在前端某個地方：解釋「跌破多少通知我」那一句話，
 * 屬於讀那個數字的程式旁邊，否則兩邊會各說各話而只有一邊是對的。 */
export interface TemplateField {
  key: string
  label: string
  help: string
  kind: string
  default: number | string
  minimum: number | null
}

/** GET /api/strategies/templates -- 現成的提醒範本。
 *
 * 這份清單存在的理由是：在它之前，設定一則「跌到 900 叫我」的提醒需要打開一個
 * 程式碼編輯器。範本讓同一件事變成填表格。 */
export interface StrategyTemplate {
  key: string
  title: string
  summary: string
  good_for: string
  fields: TemplateField[]
}

/** GET /api/system/status -- 「is it still running」, for the page that answers it.
 *
 * Distinct from /healthz, which is unauthenticated and therefore deliberately
 * terse. This one is behind a login and can say how long, how many, and which. */
export interface SystemStatus {
  /** The one word the page leads with, so 「一切正常」 is readable without
   * decoding the four sections under it. */
  overall: 'ok' | 'warn' | 'fail'
  /** 他這一份是不是舊的。
   *
   * `behind` 是 `null` 代表**不知道**（問不到 GitHub、或這個平台沒告訴 app 它是
   * 哪一版），而畫面上不可以把它顯示成「已經是最新」——那會讓他錯過安全修補，
   * 而那正是他打開這一頁想確認的事。 */
  update?: {
    running: string | null
    latest: string | null
    behind: boolean | null
    why: string | null
    /** 這一份前端的 commit 在上游存在嗎。
     *
     * `false` 代表**這一份被改過**——自動同步只快轉、絕不覆蓋。那時候說「有新版可以
     * 更新」是錯的：他照著做（重新部署）拿到的還是自己那一版，因為同步不會直接蓋過
     * 去。它改成在他的 repo 上開一個 PR，所以正確的話是「有一個等你按的更新」。
     *
     * `null` 是「問不到」。誤判成分岔比誤判成落後更糟，所以那時候照舊講落後。 */
    frontend_from_upstream?: boolean | null
    /** 這一份是「一次部署」（前端和後端同一個映像檔）還是「兩次部署」。
     *
     * **畫面自己不知道這件事**——它在哪裡被送出來的，只有伺服器知道。而它決定
     * 「你看到的畫面是舊的」後面該接什麼：兩次部署要他去前端那個平台重新部署；
     * 一次部署的兩半依建構為真同版，所以唯一可能的原因是瀏覽器手上那份 bundle
     * 是舊的，而修法是重新整理。 */
    serves_its_own_frontend?: boolean
  }
  /** Whether asking the assistant would produce an answer rather than an
   * error. AI_API_KEY is one more blank in a deploy form and is optional by
   * design, so the box is left out entirely rather than offered and broken. */
  assistant_available: boolean
  /** 資料放在哪裡，以及上一次啟動時遷移有沒有跑完。
   *
   * `detail` 是給人看的一整句話，包含遷移失敗時的原因原文——那一串正是他要貼給別人
   * 看、或貼進「問 AI」的那一段。 */
  database: {
    kind: 'sqlite' | 'postgres' | 'other'
    ephemeral: boolean
    status: 'ok' | 'warn' | 'fail'
    detail: string
  }
  worker: {
    enabled: boolean
    uptime_sec: number
    last_loop_age_sec: number | null
    last_poll_age_sec: number | null
    /** 這個行程起來之前，有多久沒有**任何**行程在跑（秒）。null＝沒有這種空白。
     *
     * 上面三個都是這個行程自己的記憶，所以結構上不可能看到這件事：行程死掉，它們
     * 跟著歸零，醒來之後每一欄都是健康的。後端從 market_quotes.fetched_at 回頭算，
     * 因為那是唯一跨得過行程生死的牆上時鐘。 */
    slept_sec: number | null
  }
  market_data: {
    consecutive_empty_polls: number
    /** Named and aged rather than counted: the fix is to correct or delete
     * that one watchlist row, and nobody can do either from a number. */
    stale_symbols: { symbol: string; gap_sec: number }[]
    /** 抓不到 K 棒的那幾段（「代號 週期」）。
     *
     * 跟 stale_symbols 分開，因為報價和 K 棒走的是上游不同的端點：「報價正常、
     * K 棒抓不到」是一個真的組合，而那時候看 K 線的策略一則提醒都發不出來。 */
    stale_bars: { series: string; gap_sec: number }[]
  }
  notifications: {
    enabled: boolean
    sent: number
    retrying: number
    deferred: number
    given_up: number
    /** No channel was reachable at all. Counted apart from the rest because
     * it is the one failure the owner can fix themselves. */
    reached_nobody: number
    window_hours: number
  }
}

/** GET /api/market/bars -- candles for the chart, from this app's own feed.
 *
 * Exists because TradingView's free embedded widget answers 「此商品僅在
 * TradingView 上可用」 for Taiwanese symbols: a data licensing restriction, not
 * a symbol-format problem. The backend already had these candles -- every
 * price, alert and backtest runs on them. */
export interface BarsResponse {
  symbol: string
  /** Echoed, because a chart drawing weekly candles under a 「日」 label is a
   * wrong chart that looks right. */
  timeframe: string
  /** The provider could not be reached, as opposed to answering with nothing.
   * Two different sentences on screen: one clears on its own and one never
   * will, and showing the permanent one for the transient case is how a stock
   * with fifty years of candles reads as delisted. */
  fetch_failed?: boolean
  bars: {
    /** ISO string. lightweight-charts wants UNIX SECONDS, so the component
     * converts -- handing it milliseconds plots every candle around the year
     * 57000 and the chart comes back empty with no error anywhere. */
    time: string
    open: number
    high: number
    low: number
    close: number
    volume: number
  }[]
  /** 「live」＝剛剛抓到的；「stored」＝上游不通，畫的是資料庫裡的存量。
   *
   * 圖上要說出來。一張永遠畫得出來的圖會掩蓋一個已經死掉一週的資料源，而畫面看
   * 起來正常、實際上停在上週，對提醒類產品比畫不出來更糟。
   *
   * 選填：比這個欄位舊的後端不會回它，那時候當成 live 是對的——它本來就沒有存量。 */
  served_from?: 'live' | 'stored'
}

/** GET /api/ai-settings -- which model to ask, and enough about the key to
 * recognise it.
 *
 * Write-only over the API: the key itself never comes back, like every other
 * secret on every other settings page. */
export interface AiSettings {
  configured: boolean
  /** Where the setting in force came from. 「It works and I never set it here」
   * sends somebody hunting through their hosting platform's settings for a
   * value they do not remember typing. */
  source: 'database' | 'env' | 'none'
  provider: string
  base_url: string
  model: string
  /** The last four characters, which is what tells 「the key I meant」 from
   * 「one I pasted wrong six months ago」 without revealing it. */
  key_preview: string | null
}

/** A value a user can set on a strategy or an indicator. Lives here rather
 * than in a component because both the strategy form and the chart's
 * indicator picker hand these to the same kind of endpoint. */
export type ParamValue = number | boolean | string

/** One tuning knob on an indicator, as its author declared it. */
export interface IndicatorParamSpec {
  name: string
  /** 'int' | 'float' | 'bool' | 'str'. Whole numbers must stay whole: the
   * server refuses a float where an int was declared rather than letting
   * range() raise deep inside the library. */
  type: string
  default: ParamValue
}

export interface IndicatorSpec {
  name: string
  title: string
  category: string
  /** The Chinese label for the category, for grouping in a menu. The raw
   * `category` is an enum value and 「trend」 as a heading is the enum leaking
   * onto the screen. */
  category_label: string
  /** One entry per output. macd yields three; sma yields one with an empty
   * key. `pane` is decided on the SERVER -- see services/indicator_panes.py --
   * because it cannot be derived from category, result type or value range,
   * and a second answer computed here would silently squash the chart. */
  outputs: { key: string; pane: string; scale: string }[]
  /** Only the tuning knobs. The bar columns are bound from the candles the
   * server already fetched. */
  params: IndicatorParamSpec[]
}

export interface AvailableIndicators {
  indicators: IndicatorSpec[]
}

export interface IndicatorSeriesResponse {
  name: string
  /** Empty for a single-output indicator. */
  key: string
  pane: string
  /** Two series in one pane sharing this string share an axis. Decided on the
   * server: everything an indicator returns shares it by default -- macd
   * against its own signal line is the point of macd -- except where the
   * scales genuinely differ, which is measured and declared there. */
  scale: string
  /** Warm-up positions are absent, not null: every indicator returns a list as
   * long as its input with leading Nones, and a null is not a point a line
   * renderer can draw. Each point carries its own time, so nothing has to be
   * zipped by index against the candles. */
  points: { time: string; value: number }[]
}

export interface IndicatorsResponse {
  symbol: string
  timeframe: string
  series: IndicatorSeriesResponse[]
}

export interface TimeframeOption {
  /** What the provider is asked for: 「4h」. */
  value: string
  /** What the owner reads: 四小時線. Served by the API so the chart, the
   * strategy form and the backtest form cannot drift into three names for one
   * candle. */
  label: string
  /** How many candles this source will actually part with. Yahoo caps intraday
   * history hard and hands back an empty frame past the cap rather than a
   * shorter one. */
  max_bars: number
}

export interface TimeframesResponse {
  sources: { data_source: DataSource; timeframes: TimeframeOption[] }[]
}
