import type { RiskSettings } from './types'

/** What switching a knob off actually costs you.
 *
 * The three read alike in a form and mean opposite things: relaxing a cap is
 * benign, switching off a protection leaves a position with nothing to close
 * it, and switching off a throttle only makes things noisier. Labelling all
 * three "不限制" is what made 0 a trap in the first place. */
export type RiskFieldKind = 'cap' | 'protection' | 'throttle'

export interface RiskFieldSpec {
  key: keyof RiskSettings
  label: string
  kind: RiskFieldKind
  /** Wording for the switch that sets this field to 0. Deliberately not one
   * shared string -- see RiskFieldKind. */
  offLabel: string
  help?: string
  /** Shown only while the switch is on, where the consequence is worth saying
   * out loud rather than leaving the owner to work out. */
  offWarning?: string
}

/** The backend reads `<= 0` as "off / no limit" for every one of these
 * (services/risk.py, signals.py, alerts.py). That rule is uniform, which is
 * what lets one switch express it eight times. */
export function isSwitchedOff(raw: string): boolean {
  const trimmed = raw.trim()
  return trimmed !== '' && Number(trimmed) === 0
}

/** The accessible name of a field's off-switch. One helper so the form and
 * its tests cannot drift into two spellings of the same control. */
export function offSwitchLabel(field: RiskFieldSpec): string {
  return `${field.label}：${field.offLabel}`
}

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
    kind: 'cap',
    offLabel: '不限制',
    help: '買進後的持倉總成本不能超過這個金額，會超過的買進訊號會被擋下來。',
  },
  {
    key: 'stop_loss_pct',
    label: '停損百分比',
    kind: 'protection',
    offLabel: '不設停損',
    help: '跌破成本價這個百分比時自動送出賣單。例如 0.05 就是跌 5%。',
    offWarning:
      '已關閉停損：這個部位不管跌多少都不會自動賣出，虧損沒有底線，只能靠你自己盯盤手動處理。',
  },
  {
    key: 'take_profit_pct',
    label: '停利百分比',
    kind: 'protection',
    offLabel: '不設停利',
    help: '漲超過成本價這個百分比時自動送出賣單。例如 0.1 就是漲 10%。',
    offWarning: '已關閉停利：漲再多都不會自動獲利了結，要自己決定何時出場。',
  },
  { key: 'max_position_qty', label: '最大持倉數量', kind: 'cap', offLabel: '不限制' },
  { key: 'max_order_notional', label: '單筆最大金額', kind: 'cap', offLabel: '不限制' },
  {
    key: 'max_pending_orders_per_symbol',
    label: '單一代號最大待確認訂單數',
    kind: 'cap',
    offLabel: '不限制',
    help: '同一支股票最多能同時有幾張還沒確認的訂單。',
  },
  {
    key: 'signal_cooldown_sec',
    label: '下單訊號冷卻時間（秒）',
    kind: 'throttle',
    offLabel: '不冷卻',
    help: '管「下單」：同一個策略的訊號在這段時間內，只會產生一張待確認訂單，避免同一波行情被重複下單。',
    offWarning: '已關閉冷卻：同一波行情可能連續產生好幾張待確認訂單。',
  },
  {
    key: 'alert_interval_sec',
    label: '提醒間隔（秒）',
    kind: 'throttle',
    offLabel: '每次都通知',
    help: '管「通知」：只提醒策略最快每隔這麼久才會再通知你一次，跟上面的下單冷卻是兩回事，這個完全不影響訂單。價格在策略的門檻附近上下震盪時，同一個訊號會一直重複觸發，這個間隔就是用來避免手機被洗版。',
    offWarning: '已設成每次訊號都通知你：價格在門檻附近震盪時，手機會一直響。',
  },
]
