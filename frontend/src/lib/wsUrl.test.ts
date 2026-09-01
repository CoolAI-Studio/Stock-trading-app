import { describe, expect, it } from 'vitest'
import { websocketBaseUrl } from './wsUrl'

/**
 * One fewer blank in the deploy form.
 *
 * The flow asked for VITE_API_BASE_URL and VITE_WS_URL, and the README's own
 * instruction for the second was 「同一個網址，但開頭改成 wss://」. That is a
 * transformation, not a decision -- and a blank whose correct value is
 * mechanically derivable from the one above it is one more thing to paste, one
 * more place to typo, and a dashboard whose prices silently never update.
 */

describe('WebSocket 網址', () => {
  it('https 的後端就是 wss', () => {
    expect(websocketBaseUrl('https://my-app.onrender.com')).toBe('wss://my-app.onrender.com')
  })

  it('本機 http 就是 ws', () => {
    expect(websocketBaseUrl('http://localhost:8000')).toBe('ws://localhost:8000')
  })

  it('明確設定的還是贏 —— 有人的 socket 真的在別的地方', () => {
    expect(websocketBaseUrl('https://api.example.com', 'wss://socket.example.com')).toBe(
      'wss://socket.example.com',
    )
  })

  it('空字串的覆寫不算覆寫', () => {
    // An env var declared and left blank is the commonest way this arrives.
    expect(websocketBaseUrl('https://my-app.onrender.com', '')).toBe('wss://my-app.onrender.com')
  })

  it('路徑和連接埠都要留著', () => {
    expect(websocketBaseUrl('https://host.example:8443/base')).toBe('wss://host.example:8443/base')
  })

  it('看不懂的就原樣傳回去，不要猜', () => {
    // A wrong socket address fails silently. The caller's own default is a
    // better answer than an invented one.
    expect(websocketBaseUrl('nonsense')).toBe('nonsense')
  })
})

describe('只部署一次的那一份（同源）', () => {
  /**
   * **線上實測到的缺口。**
   *
   * 後端直接供應前端之後（#53），`VITE_API_BASE_URL` 在正式建置裡是**沒設**的，而
   * `resolveApiBase` 對那種情況回空字串＝同源。REST 那一半因此是對的。
   *
   * 但 useWebSocket 沒走那條路，它自己寫了 `?? 'http://localhost:8000'`，所以線上那
   * 份 bundle 的 socket 位址是 `ws://localhost:8000`——使用者的瀏覽器去打**他自己電腦
   * 上的** 8000 埠。驗證方式是直接抓線上的 bundle：
   *
   *     curl https://…/assets/index-*.js | grep localhost:8000   → 命中
   *
   * 症狀完全靜默：頁面正常、REST 正常、只有即時報價永遠不更新，而 README 第一行就在
   * 宣傳那個功能。
   *
   * 同源不能只回空字串了事：`new WebSocket('/ws')` 會直接丟 SyntaxError，WebSocket 一
   * 定要絕對網址。所以同源時要從當前頁面的位址推出來。
   */
  it('同源（沒設 API 位址）時，從當前頁面推出 wss', () => {
    expect(websocketBaseUrl('', undefined, 'https://his-app.onrender.com')).toBe(
      'wss://his-app.onrender.com',
    )
  })

  it('同源而且頁面是 http（自架、區網）時是 ws', () => {
    expect(websocketBaseUrl('', undefined, 'http://192.168.1.10:8000')).toBe(
      'ws://192.168.1.10:8000',
    )
  })

  it('明確設了 API 位址的話，還是以它為準 —— 分開部署那條路沒有被動到', () => {
    expect(websocketBaseUrl('https://api.example.com', undefined, 'https://ui.example.com')).toBe(
      'wss://api.example.com',
    )
  })

  it('連頁面位址都沒有（測試、SSR）就不要猜', () => {
    // 猜一個錯的 socket 位址會靜默失敗，而回空字串會讓呼叫端自己決定要不要連。
    expect(websocketBaseUrl('', undefined, undefined)).toBe('')
  })
})
