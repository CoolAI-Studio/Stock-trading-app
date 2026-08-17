const SW_URL = '/sw.js'

export interface PushSubscriptionConfig {
  endpoint: string
  p256dh: string
  auth: string
}

export function isPushSupported(): boolean {
  return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
}

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4)
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/')
  const rawData = atob(base64)
  const output = new Uint8Array(rawData.length)
  for (let i = 0; i < rawData.length; i++) output[i] = rawData.charCodeAt(i)
  return output
}

/** Registers the service worker, requests Notification permission, and
 * subscribes via PushManager. Throws if permission is denied or the
 * environment doesn't support Web Push -- callers show that as an error. */
export async function subscribeToPush(vapidPublicKey: string): Promise<PushSubscriptionConfig> {
  if (!isPushSupported()) {
    throw new Error('這個瀏覽器不支援推播通知')
  }

  const permission = await Notification.requestPermission()
  if (permission !== 'granted') {
    throw new Error('未取得通知權限')
  }

  const registration = await navigator.serviceWorker.register(SW_URL)
  await navigator.serviceWorker.ready

  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(vapidPublicKey) as BufferSource,
  })

  const json = subscription.toJSON()
  return {
    endpoint: subscription.endpoint,
    p256dh: json.keys?.p256dh ?? '',
    auth: json.keys?.auth ?? '',
  }
}

export async function unsubscribeFromPush(): Promise<void> {
  if (!isPushSupported()) return
  const registration = await navigator.serviceWorker.getRegistration(SW_URL)
  const subscription = await registration?.pushManager.getSubscription()
  await subscription?.unsubscribe()
}
