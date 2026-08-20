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

/**
 * Ask for notification permission. MUST BE CALLED DIRECTLY FROM A CLICK.
 *
 * Notification.requestPermission() requires transient user activation, and the
 * activation a click grants is spent by an intervening `await`. The form used
 * to fetch the VAPID public key over the network first and ask afterwards --
 * on Safari, and therefore on every iPhone, the permission sheet then never
 * appeared at all. The owner pressed 建立, saw nothing happen, and concluded
 * push does not work on their phone.
 *
 * So this is called by the click handler as its first statement, before
 * anything is awaited, and subscribeToPush() is not allowed to ask.
 *
 * Returns the resulting permission rather than throwing: 'denied' is a state
 * the caller has to explain, not an error to swallow.
 */
export async function requestPushPermission(): Promise<NotificationPermission> {
  if (!isPushSupported()) return 'denied'

  // Both terminal states are final. Browsers resolve a repeat request
  // instantly without showing anything, so asking again cannot change the
  // answer and only makes the caller believe it tried.
  if (Notification.permission === 'granted') return 'granted'
  if (Notification.permission === 'denied') return 'denied'

  return Notification.requestPermission()
}

/**
 * Register the service worker and subscribe. Permission must ALREADY be
 * granted -- see requestPushPermission() for why this cannot ask.
 */
export async function subscribeToPush(vapidPublicKey: string): Promise<PushSubscriptionConfig> {
  if (!isPushSupported()) {
    throw new Error('這個瀏覽器不支援推播通知')
  }

  if (Notification.permission === 'denied') {
    // No amount of retrying reopens this; the browser will not ask again.
    // Naming the only place it can be changed is the difference between an
    // error and a dead end.
    throw new Error(
      '通知權限已被封鎖，瀏覽器不會再詢問。請到裝置的「設定」→ 通知（或瀏覽器的網站設定）' +
        '把這個網站的通知打開，再回來重新建立一次。',
    )
  }
  if (Notification.permission !== 'granted') {
    throw new Error('未取得通知權限')
  }

  const registration = await navigator.serviceWorker.register(SW_URL)
  await navigator.serviceWorker.ready

  const wanted = urlBase64ToUint8Array(vapidPublicKey)

  // Reuse whatever this device already has -- but only if it was created with
  // the SAME server key.
  //
  // Reuse matters because subscribing blind hands back the existing endpoint
  // anyway, so a second channel made on one device would carry a duplicate
  // endpoint and every alert would arrive twice, which is how somebody starts
  // ignoring them.
  //
  // Checking the key matters more. The browser stores applicationServerKey
  // with the subscription and never changes it, so once the server's VAPID
  // pair is regenerated, an old subscription makes Apple answer 403
  // VapidPkHashMismatch to every push -- forever, silently, with no way to
  // recover from inside the app because pressing 建立 would just hand back the
  // same dead subscription again.
  let existing = await registration.pushManager.getSubscription()
  if (existing && !usesKey(existing, wanted)) {
    await existing.unsubscribe()
    existing = null
  }

  const subscription =
    existing ??
    (await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: wanted as BufferSource,
    }))

  const json = subscription.toJSON()
  return {
    endpoint: subscription.endpoint,
    p256dh: json.keys?.p256dh ?? '',
    auth: json.keys?.auth ?? '',
  }
}

/** Whether an existing subscription was created with this exact server key.
 *
 * Returns false when the browser does not expose `options` -- re-subscribing
 * costs one round trip, whereas guessing wrong costs every future alert. */
function usesKey(subscription: PushSubscription, wanted: Uint8Array): boolean {
  const actual = subscription.options?.applicationServerKey
  if (!actual) return false
  const bytes = new Uint8Array(actual as ArrayBuffer)
  if (bytes.length !== wanted.length) return false
  return bytes.every((byte, i) => byte === wanted[i])
}

/** The endpoint this browser is currently subscribed with, or null.
 *
 * Callers need it to tell "this device" from "some other device the account
 * also has": deleting a channel used to unsubscribe whatever browser happened
 * to be doing the deleting, which killed a working phone from a laptop. */
export async function currentSubscriptionEndpoint(): Promise<string | null> {
  if (!isPushSupported()) return null
  const registration = await navigator.serviceWorker.getRegistration(SW_URL)
  const subscription = await registration?.pushManager.getSubscription()
  return subscription?.endpoint ?? null
}

export async function unsubscribeFromPush(): Promise<void> {
  if (!isPushSupported()) return
  const registration = await navigator.serviceWorker.getRegistration(SW_URL)
  const subscription = await registration?.pushManager.getSubscription()
  await subscription?.unsubscribe()
}
