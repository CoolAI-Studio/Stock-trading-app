// Minimal service worker for Web Push notifications only -- no offline
// caching/asset interception, so it never fights the app's own network
// requests or goes stale against a new deploy.

// Take over immediately instead of waiting for every tab to close. Without
// this a freshly installed worker sits in 'waiting' and the page that just
// subscribed is controlled by nobody, which is also why notificationclick
// could not find a window to focus.
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))

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
    self.registration.showNotification(data.title || '交易提醒', {
      body: data.body || '',
      // Every alert used to replace the previous one on iOS because they all
      // shared the default tag. A per-symbol tag collapses repeats of the SAME
      // instrument (which is what you want -- the newest price is the useful
      // one) while letting two different stocks both stay on screen.
      tag: data.tag || undefined,
      // Carried through to the click handler so the notification can open the
      // thing it is about rather than always the dashboard.
      data: { url: data.url || '/' },
    }),
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
