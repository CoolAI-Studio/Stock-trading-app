// Minimal service worker for Web Push notifications only -- no offline
// caching/asset interception, so it never fights the app's own network
// requests or goes stale against a new deploy.

self.addEventListener('push', (event) => {
  let data = { title: 'Trading App', body: '' }
  if (event.data) {
    try {
      data = event.data.json()
    } catch {
      data.body = event.data.text()
    }
  }
  event.waitUntil(self.registration.showNotification(data.title || 'Trading App', { body: data.body || '' }))
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  event.waitUntil(
    self.clients.matchAll({ type: 'window' }).then((clientList) => {
      for (const client of clientList) {
        if ('focus' in client) return client.focus()
      }
      if (self.clients.openWindow) return self.clients.openWindow('/')
      return undefined
    }),
  )
})
