import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * 手機上真正把通知畫出來的那一段。
 *
 * `public/sw.js` 由 Vite 原樣複製，所以 `tsc -b` 不涵蓋它、oxlint 只看得到語法。
 * 在這條測試之前，**整個 repo 沒有任何東西載入過它**——它壞掉（回報排到
 * showNotification 前面、選項打錯、位址算錯）不會讓任何一關變紅。兩輪獨立稽查各自
 * 指出了這一點。
 *
 * 做法是把它當普通 JS 求值，餵一個假的 `self`。它沒有 import／export，所以這件事成
 * 立；而這正是它能被原樣送到瀏覽器的原因。
 */

const SOURCE = readFileSync(resolve(__dirname, '../../public/sw.js'), 'utf-8')

type Handlers = Record<string, (event: unknown) => void>

function loadWorker(registrationUrl: string) {
  const handlers: Handlers = {}
  const shown: Array<{ title: string; options: Record<string, unknown> }> = []
  // 宣告參數型別，否則 mock.calls[0][0] 在 tsc 眼中不存在（vi.fn() 推出的是空元組）。
  const fetchMock = vi.fn((_url: string, _init?: unknown) => Promise.resolve({ ok: true }))

  const self = {
    location: { href: registrationUrl, origin: new URL(registrationUrl).origin },
    addEventListener: (name: string, fn: (event: unknown) => void) => {
      handlers[name] = fn
    },
    skipWaiting: () => undefined,
    clients: { claim: () => Promise.resolve(), matchAll: () => Promise.resolve([]) },
    registration: {
      showNotification: (title: string, options: Record<string, unknown>) => {
        shown.push({ title, options })
        return Promise.resolve()
      },
    },
    fetch: fetchMock,
    AbortController,
    setTimeout,
    clearTimeout,
    URL,
    JSON,
  }

  // sw.js 用的是裸名（fetch、setTimeout…）而不是 self.fetch，所以那些要當參數傳進去。
  const run = new Function(
    'self',
    'fetch',
    'AbortController',
    'setTimeout',
    'clearTimeout',
    'URL',
    SOURCE,
  )
  run(self, fetchMock, AbortController, setTimeout, clearTimeout, URL)
  return { handlers, shown, fetchMock }
}

function pushEvent(payload: Record<string, unknown>) {
  const waits: Array<Promise<unknown>> = []
  return {
    event: {
      data: { json: () => payload, text: () => JSON.stringify(payload) },
      waitUntil: (p: Promise<unknown>) => waits.push(p),
    },
    settled: () => Promise.all(waits),
  }
}

describe('service worker（手機上真正畫出通知的那一段）', () => {
  beforeEach(() => vi.clearAllMocks())

  it('同源部署（api= 是空的）也要送出送達回報', async () => {
    /**
     * **這是出貨那一份的設定，而它原本整條跳過回報。**
     *
     * 後端直接供應前端之後，正式建置根本不設 VITE_API_BASE_URL，所以註冊網址是
     * `/sw.js?api=`——空字串。而 sw.js 寫的是 `if (!token || !API_BASE) return`，
     * 於是回報從來沒送出去過。
     *
     * 後果不是「少一個統計」：通知頁的「測試」按鈕會等 15 秒，然後告訴他「這台裝置
     * 沒有回報收到」，並建議他**把一個其實正常的推播管道刪掉重建**。那是他唯一用來
     * 確認通知路徑還活著的儀器，而它在每一份真實部署上都給錯的答案。
     *
     * 跟 useWebSocket 那個是同一個病：**空字串代表同源**，卻被當成「不知道後端在
     * 哪」。
     */
    const { handlers, fetchMock } = loadWorker('https://his-app.onrender.com/sw.js?api=')

    const { event, settled } = pushEvent({ title: '到價了', body: '2330 跌破 900', receipt: 'tok' })
    handlers.push(event)
    await settled()

    expect(fetchMock).toHaveBeenCalled()
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      'https://his-app.onrender.com/api/notifications/push/receipt',
    )
  })

  it('分開部署（api= 有值）時送到那個後端', async () => {
    const { handlers, fetchMock } = loadWorker(
      'https://ui.vercel.app/sw.js?api=' + encodeURIComponent('https://api.example.com'),
    )

    const { event, settled } = pushEvent({ title: 'x', receipt: 'tok' })
    handlers.push(event)
    await settled()

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      'https://api.example.com/api/notifications/push/receipt',
    )
  })

  it('先把通知畫出來，再回報 —— 順序不可以反', async () => {
    /**
     * sw.js 檔頭自己寫著理由：回報排在 showNotification 前面的話，三次慢網路就會讓
     * 這支手機的推播被瀏覽器永久拒絕——比回報要修的那個 bug 嚴重得多。
     */
    const order: string[] = []
    const { handlers } = loadWorker('https://his-app.onrender.com/sw.js?api=')
    const source = SOURCE.indexOf('showNotification')
    const receipt = SOURCE.indexOf('reportReceipt(data.receipt)')

    expect(source).toBeGreaterThan(-1)
    expect(receipt).toBeGreaterThan(source)
    expect(handlers.push).toBeTypeOf('function')
    expect(order).toEqual([])
  })

  it('沒有 receipt 的推播照樣顯示，不會因為回報而失敗', async () => {
    const { handlers, shown, fetchMock } = loadWorker('https://his-app.onrender.com/sw.js?api=')

    const { event, settled } = pushEvent({ title: '只是通知', body: '沒有回條' })
    handlers.push(event)
    await settled()

    expect(shown).toHaveLength(1)
    expect(shown[0].title).toBe('只是通知')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
