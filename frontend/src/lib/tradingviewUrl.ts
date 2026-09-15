/**
 * 每個帳號自己的 TradingView 網址（#115），畫面上用的遮罩。
 *
 * 帳號與版本留著、只遮 MAC——跟後端 access log 的遮法同一個形狀
 * （backend/app/logging_setup.py）：看得出是哪一條網址，拿不到能用的那一段。
 *
 * 放在 lib/ 而不是元件檔裡：元件檔只匯出元件，React 的 fast refresh 才能正常重載它
 * （oxlint 的 react/only-export-components）。
 */
const PERSONAL_URL_MAC = /(\/api\/webhooks\/tradingview\/\d+\.\d+\.)[A-Za-z0-9_-]+$/

export function maskPersonalUrl(url: string): string {
  return url.replace(PERSONAL_URL_MAC, '$1••••••••')
}
