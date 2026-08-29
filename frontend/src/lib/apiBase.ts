/**
 * 後端在哪裡。
 *
 * 後端直接供應前端之後，預設值必須是**同一個網址**。原本預設
 * `http://localhost:8000`：那對「前後端分開跑」的開發環境是對的，但對「使用者打開
 * 他自己那一份部署」是致命的——瀏覽器會去打**他自己電腦上的** 8000 埠，那裡什麼都
 * 沒有，而錯誤訊息是連線被拒絕，跟真正的原因差了十萬八千里。
 *
 * 抽成純函式是為了讓那三種情況測得到：`import.meta.env` 在測試裡改不動。
 */
export function resolveApiBase({ base, dev }: { base: string | undefined; dev: boolean }): string {
  // 明確設了就聽他的——**包括設成空字串**。那是「我要同源」的意思，而把它當成沒設
  // 會讓他在開發模式下拿到 localhost:8000，一個他明明寫了設定卻沒被聽見的結果。
  if (base !== undefined) return base
  // 開發模式：前端 5173、後端 8000，兩邊分開跑。
  if (dev) return 'http://localhost:8000'
  // 正式版而且沒設：後端就是供應這個頁面的那一個，所以同源。
  return ''
}
