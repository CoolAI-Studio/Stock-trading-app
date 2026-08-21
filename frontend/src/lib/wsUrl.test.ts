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
