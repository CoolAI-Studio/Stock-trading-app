// Minimal service worker for Web Push notifications only -- no offline
// caching/asset interception, so it never fights the app's own network
// requests or goes stale against a new deploy.
//
// THE ONE RULE THIS FILE IS BUILT AROUND. WebKit gives a push handler about 30
// seconds to call showNotification(); miss it and the push is recorded as
// "silent", and THREE silent pushes remove every subscription for the origin
// (WebKit source, NotificationData.h: silentPushTimeoutForProduction { 30_s }).
// So showNotification() is awaited FIRST, before anything else, and nothing
// after it is allowed to reject. Putting the delivery receipt ahead of it
// would mean three slow networks permanently kill this phone's push -- a far
// worse bug than the one the receipt exists to fix.

// This file is served verbatim from Vite's public/ directory, so it is never
// compiled and import.meta.env does not exist in it. The API origin therefore
// arrives as a query string on the registration URL -- see lib/push.ts.
//
// **空的代表同源，不是「不知道」。** 後端直接供應前端之後（#53），正式建置根本不設
// VITE_API_BASE_URL，所以註冊網址是 `/sw.js?api=`。這裡原本是 `|| ''`，配上底下
// `if (!token || !API_BASE) return`，等於**每一份真實部署都整條跳過送達回報**。
//
// 後果不是少一個統計：通知頁的「測試」按鈕會等 15 秒，然後告訴他「這台裝置沒有回報
// 收到」，並建議他把一個其實正常的推播管道刪掉重建——那是他唯一用來確認通知路徑還活
// 著的儀器，而它在每一份真實部署上都給錯的答案。
//
// 跟 lib/useWebSocket.ts 那個是同一個病，而 lib/apiBase.ts 早就把規則寫下來了：空字
// 串是「我要同源」。service worker 一定跟它服務的頁面同源，所以那個答案就在手邊。
const API_BASE = new URL(self.location.href).searchParams.get('api') || self.location.origin

// Well under the 30-second silent-push budget, and long enough for a phone
// waking on a slow connection.
const RECEIPT_TIMEOUT_MS = 5000

self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))

/** Tell the server this device actually displayed the notification.
 *
 * A 2xx from the push service means only that it accepted the message for
 * later delivery (RFC 8030 §5), so without this the app cannot tell a
 * delivered alert from one the phone never saw -- and the 測試 button was
 * reporting the former when it was the latter.
 *
 * Never throws. The caller is inside waitUntil, and a rejection there is what
 * turns a delivered push into a silent one.
 */
async function reportReceipt(token) {
  if (!token || !API_BASE) return
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), RECEIPT_TIMEOUT_MS)
  try {
    await fetch(`${API_BASE}/api/notifications/push/receipt`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
      signal: controller.signal,
      // No cookies or credentials: the token in the body is the whole proof,
      // and it could only have come from decrypting this subscription's
      // payload.
      credentials: 'omit',
    })
  } catch {
    // Offline, aborted, server down. The alert was still shown, which is what
    // matters; the owner just sees 未確認 instead of 已送達.
  } finally {
    clearTimeout(timer)
  }
}

self.addEventListener('push', (event) => {
  let data = { title: '交易提醒', body: '' }
  if (event.data) {
    try {
      data = event.data.json()
    } catch {
      data.body = event.data.text()
    }
  }

  event.waitUntil(
    (async () => {
      // FIRST. See the note at the top of this file.
      await self.registration.showNotification(data.title || '交易提醒', {
        body: data.body || '',
        // Every alert used to replace the previous one because they all shared
        // the default tag. A per-symbol tag collapses repeats of the SAME
        // instrument (the newest price is the useful one) while letting two
        // different stocks both stay on screen.
        tag: data.tag || undefined,
        // Carried through to the click handler so the notification can open
        // the thing it is about rather than always the dashboard.
        data: { url: data.url || '/' },
      })

      await reportReceipt(data.receipt)
    })(),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const target = (event.notification.data && event.notification.data.url) || '/'
  event.waitUntil(
    self.clients
      // includeUncontrolled matters: a tab loaded before this worker activated
      // is not controlled by it, and without the flag matchAll returns nothing
      // -- so the click opened a second window on top of the app the owner
      // already had open.
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then((clientList) => {
        for (const client of clientList) {
          if ('focus' in client) {
            if ('navigate' in client && target !== '/') client.navigate(target)
            return client.focus()
          }
        }
        if (self.clients.openWindow) return self.clients.openWindow(target)
        return undefined
      }),
  )
})
