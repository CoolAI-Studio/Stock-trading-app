/** The WebSocket address, derived from the API address when nobody set one.
 *
 * The deploy flow asked for both, and the README's own instruction for the
 * second was 「同一個網址，但開頭改成 wss://」 -- which is a transformation, not
 * a decision. A blank whose correct value is mechanically derivable from the
 * blank above it is a blank that should not be asked for: it is one more thing
 * to paste, one more place to typo, and getting it wrong produces a dashboard
 * whose prices never update with nothing on screen saying why.
 *
 * VITE_WS_URL still wins when it is set, for the deployment that genuinely
 * serves its socket somewhere else.
 */
function toSocketScheme(url: string): string | null {
  if (url.startsWith('https://')) return `wss://${url.slice('https://'.length)}`
  if (url.startsWith('http://')) return `ws://${url.slice('http://'.length)}`
  return null
}

export function websocketBaseUrl(
  apiBaseUrl: string,
  explicit?: string,
  pageOrigin?: string,
): string {
  const override = (explicit ?? '').trim()
  if (override) return override

  const api = apiBaseUrl.trim()
  const fromApi = toSocketScheme(api)
  if (fromApi) return fromApi

  // 空的 API 位址＝**同源**（#53 之後那是正式建置的預設值，見 apiBase.ts）。
  //
  // 這一段是線上量出來的缺口：useWebSocket 原本自己寫 `?? 'http://localhost:8000'`，
  // 沒走 resolveApiBase，所以每一份「只部署一次」的副本，socket 位址都是
  // `ws://localhost:8000`——瀏覽器去打**使用者自己電腦上的** 8000 埠。抓線上 bundle
  // grep localhost:8000 就命中。症狀完全靜默：頁面正常、REST 正常，只有即時報價永遠
  // 不更新，而 README 第一行就在宣傳它。
  //
  // 同源不能只回空字串了事：`new WebSocket('/ws')` 會丟 SyntaxError，WebSocket 一定
  // 要絕對網址。所以從當前頁面的位址推。
  if (!api && pageOrigin) {
    const fromPage = toSocketScheme(pageOrigin.trim())
    if (fromPage) return fromPage
  }
  // Not a URL this can transform. Returned unchanged rather than guessed at:
  // a wrong socket address fails silently, and the caller's own default is a
  // better answer than an invented one.
  return api
}
