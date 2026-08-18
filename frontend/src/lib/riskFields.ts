import type { RiskSettings } from './types'

export interface RiskFieldSpec {
  key: keyof RiskSettings
  label: string
  help?: string
  /** 0 is a real, deliberate value here and means "no ceiling" -- the backend
   * reads `<= 0` as "not configured, allow" (services/risk.py). Flagged
   * rather than written into each `help` so the per-strategy form can warn
   * that 0 and a blank box mean opposite things. */
  zeroMeansUnlimited?: boolean
}

export const ZERO_MEANS_UNLIMITED_HELP = '填 0 表示不限制。'

/** The eight knobs risk settings are made of, in the order both the global
 * form and the per-strategy override form show them.
 *
 * One list rather than one per page: the same reason the backend keeps
 * risk_resolver.OVERRIDABLE_FIELDS in a single tuple -- a knob reworded in
 * one place and forgotten in the other goes on quietly saying something else.
 * The last two are both throttles and easy to mistake for each other, so each
 * says in its own help text which pipeline it gates. */
export const RISK_FIELDS: readonly RiskFieldSpec[] = [
  {
    key: 'capital',
    label: '本金',
    help: '買進後的持倉總成本不能超過這個金額，會超過的買進訊號會被擋下來。',
    zeroMeansUnlimited: true,
  },
  { key: 'stop_loss_pct', label: '停損百分比' },
  { key: 'take_profit_pct', label: '停利百分比' },
  { key: 'max_position_qty', label: '最大持倉數量', zeroMeansUnlimited: true },
  { key: 'max_order_notional', label: '單筆最大金額', zeroMeansUnlimited: true },
  { key: 'max_pending_orders_per_symbol', label: '單一代號最大待確認訂單數' },
  {
    key: 'signal_cooldown_sec',
    label: '下單訊號冷卻時間（秒）',
    help: '管「下單」：同一個策略的訊號在這段時間內，只會產生一張待確認訂單，避免同一波行情被重複下單。',
  },
  {
    key: 'alert_interval_sec',
    label: '提醒間隔（秒）',
    help: '管「通知」：只提醒策略最快每隔這麼久才會再通知你一次，跟上面的下單冷卻是兩回事，這個完全不影響訂單。價格在策略的門檻附近上下震盪時，同一個訊號會一直重複觸發，這個間隔就是用來避免手機被洗版。填 0 表示每次訊號都通知。',
  },
]

/** The field's own help plus, where it applies, the "0 means no ceiling"
 * note. Appended rather than spelled out in three `help` strings so the three
 * fields cannot drift into three wordings of one rule. */
export function riskFieldHelp(field: RiskFieldSpec): string | undefined {
  if (!field.zeroMeansUnlimited) return field.help
  return field.help ? `${field.help}${ZERO_MEANS_UNLIMITED_HELP}` : ZERO_MEANS_UNLIMITED_HELP
}
