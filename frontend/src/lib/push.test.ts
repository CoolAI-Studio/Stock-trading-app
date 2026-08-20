import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  currentSubscriptionEndpoint,
  isPushSupported,
  requestPushPermission,
  subscribeToPush,
  unsubscribeFromPush,
} from './push'

/**
 * Getting an actual push subscription out of a browser.
 *
 * THE RULE THIS FILE IS BUILT AROUND: Notification.requestPermission() needs
 * transient user activation. The activation a click grants is spent by an
 * intervening await -- and the form used to fetch the VAPID public key over the
 * network first, THEN ask. On Safari (and so on every iPhone) the permission
 * sheet simply never appeared: the owner pressed 建立, nothing visible
 * happened, and they concluded push does not work on their phone.
 *
 * So permission is now asked for by the click handler itself, before anything
 * is awaited, and subscribeToPush() is not allowed to ask at all. The test that
 * matters most here is the one asserting it does NOT call requestPermission --
 * putting the call back would restore the bug while every other test still
 * passed.
 */

const VAPID = 'BCFiiE5pxNqJyHn6QEeewWKjMVfko4jbGnPX6kcmbZzyxnbdLjnQClrwCygjbO5f1zgjHx90FkiQKyaJE-hGYdI'
const OTHER_VAPID = 'BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U'

/** The bytes a browser stores alongside a subscription, so a test can build one
 * that either does or does not match the key being subscribed with. */
function keyBytes(base64: string): Uint8Array {
  const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4)
  const raw = atob(padded.replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from(raw, (c) => c.charCodeAt(0))
}

function mockSubscription(endpoint = 'https://push.example.com/x', vapid: string | null = VAPID) {
  return {
    endpoint,
    options: vapid === null ? {} : { applicationServerKey: keyBytes(vapid).buffer },
    toJSON: () => ({ endpoint, keys: { p256dh: 'p256dh-value', auth: 'auth-value' } }),
    unsubscribe: vi.fn().mockResolvedValue(true),
  }
}

describe('isPushSupported', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('is true when serviceWorker/PushManager/Notification all exist', () => {
    vi.stubGlobal('navigator', { serviceWorker: {} })
    vi.stubGlobal('window', { PushManager: {}, Notification: {} })
    expect(isPushSupported()).toBe(true)
  })

  it('is false when serviceWorker is missing', () => {
    vi.stubGlobal('navigator', {})
    vi.stubGlobal('window', { PushManager: {}, Notification: {} })
    expect(isPushSupported()).toBe(false)
  })
})

describe('requestPushPermission', () => {
  let requestPermissionMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    requestPermissionMock = vi.fn().mockResolvedValue('granted')
    vi.stubGlobal('navigator', { serviceWorker: {} })
    vi.stubGlobal('window', { PushManager: {}, Notification: {} })
    vi.stubGlobal('Notification', {
      permission: 'default',
      requestPermission: requestPermissionMock,
    })
  })

  afterEach(() => vi.unstubAllGlobals())

  it('asks the browser and hands back the answer', async () => {
    await expect(requestPushPermission()).resolves.toBe('granted')
    expect(requestPermissionMock).toHaveBeenCalled()
  })

  it('does not ask again once it has already been granted', async () => {
    // Safari shows nothing for a repeat request, but Chrome briefly flashes the
    // sheet; either way the answer is already known and asking is noise.
    vi.stubGlobal('Notification', {
      permission: 'granted',
      requestPermission: requestPermissionMock,
    })

    await expect(requestPushPermission()).resolves.toBe('granted')
    expect(requestPermissionMock).not.toHaveBeenCalled()
  })

  it('reports a previous refusal without re-prompting', async () => {
    // Once denied, browsers refuse to ask again and resolve instantly. Calling
    // it would look like a no-op to the owner, so the caller needs the state
    // itself in order to explain that it has to be changed in settings.
    vi.stubGlobal('Notification', {
      permission: 'denied',
      requestPermission: requestPermissionMock,
    })

    await expect(requestPushPermission()).resolves.toBe('denied')
    expect(requestPermissionMock).not.toHaveBeenCalled()
  })

  it('is safe to call where push does not exist at all', async () => {
    vi.stubGlobal('navigator', {})
    vi.stubGlobal('window', {})
    await expect(requestPushPermission()).resolves.toBe('denied')
  })
})

describe('subscribeToPush', () => {
  let registerMock: ReturnType<typeof vi.fn>
  let subscribeMock: ReturnType<typeof vi.fn>
  let getSubscriptionMock: ReturnType<typeof vi.fn>
  let requestPermissionMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    subscribeMock = vi.fn().mockResolvedValue(mockSubscription())
    getSubscriptionMock = vi.fn().mockResolvedValue(null)
    registerMock = vi.fn().mockResolvedValue({
      pushManager: { subscribe: subscribeMock, getSubscription: getSubscriptionMock },
    })
    requestPermissionMock = vi.fn().mockResolvedValue('granted')

    vi.stubGlobal('navigator', {
      serviceWorker: { register: registerMock, ready: Promise.resolve() },
    })
    vi.stubGlobal('window', { PushManager: {}, Notification: {} })
    vi.stubGlobal('Notification', {
      permission: 'granted',
      requestPermission: requestPermissionMock,
    })
  })

  afterEach(() => vi.unstubAllGlobals())

  it('registers the service worker and returns the subscription config', async () => {
    const result = await subscribeToPush(VAPID)

    // The api= query string is how the worker learns where the backend is:
    // public/sw.js is copied verbatim by Vite, so import.meta.env does not
    // exist inside it and it could not post a delivery receipt without this.
    expect(registerMock).toHaveBeenCalledWith(expect.stringContaining('/sw.js?api='))
    expect(result).toEqual({
      endpoint: 'https://push.example.com/x',
      p256dh: 'p256dh-value',
      auth: 'auth-value',
    })
  })

  it('NEVER asks for permission itself', async () => {
    // The whole bug in one assertion. Anything awaited before
    // requestPermission() spends the click's activation, and Safari then
    // refuses to show the sheet at all -- silently. Permission belongs to the
    // click handler; by the time this runs the answer must already be in.
    await subscribeToPush(VAPID)

    expect(requestPermissionMock).not.toHaveBeenCalled()
  })

  it('refuses to run before permission has been granted', async () => {
    vi.stubGlobal('Notification', {
      permission: 'default',
      requestPermission: requestPermissionMock,
    })

    await expect(subscribeToPush(VAPID)).rejects.toThrow('未取得通知權限')
    expect(subscribeMock).not.toHaveBeenCalled()
  })

  it('says so when the owner has blocked notifications rather than just failing', async () => {
    vi.stubGlobal('Notification', {
      permission: 'denied',
      requestPermission: requestPermissionMock,
    })

    // A denied permission cannot be re-requested by any amount of retrying; the
    // message has to point at the place it can actually be changed.
    await expect(subscribeToPush(VAPID)).rejects.toThrow(/設定/)
  })

  it('throws when the browser does not support push', async () => {
    vi.stubGlobal('navigator', {})
    await expect(subscribeToPush('key')).rejects.toThrow('這個瀏覽器不支援推播通知')
  })

  it('reuses a subscription that already exists instead of making a second one', async () => {
    // Registering a second channel from the same device used to call
    // subscribe() blind. The browser hands back the existing subscription when
    // the key matches, so the two rows carry the same endpoint -- and every
    // alert then arrives twice.
    const existing = mockSubscription('https://push.example.com/already-here')
    getSubscriptionMock.mockResolvedValue(existing)

    const result = await subscribeToPush(VAPID)

    expect(subscribeMock).not.toHaveBeenCalled()
    expect(result.endpoint).toBe('https://push.example.com/already-here')
  })
})

describe('unsubscribeFromPush', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('unsubscribes the existing push subscription if one exists', async () => {
    const subscription = mockSubscription()
    const getRegistrationMock = vi.fn().mockResolvedValue({
      pushManager: { getSubscription: vi.fn().mockResolvedValue(subscription) },
    })
    vi.stubGlobal('navigator', { serviceWorker: { getRegistration: getRegistrationMock } })
    vi.stubGlobal('window', { PushManager: {}, Notification: {} })

    await unsubscribeFromPush()

    // Same URL as register(), or it looks up a different worker and finds
    // nothing to unsubscribe.
    expect(getRegistrationMock).toHaveBeenCalledWith(expect.stringContaining('/sw.js?api='))
    expect(subscription.unsubscribe).toHaveBeenCalled()
  })

  it('does nothing when push is unsupported', async () => {
    vi.stubGlobal('navigator', {})
    await expect(unsubscribeFromPush()).resolves.toBeUndefined()
  })
})


// --- a rotated or mismatched VAPID key ---------------------------------------
//
// The browser stores the applicationServerKey with the subscription and will
// not change it. Reusing whatever subscription exists -- without checking that
// key -- means that after the server's VAPID pair is ever regenerated, Apple
// answers 403 VapidPkHashMismatch to every push, forever, and no amount of
// pressing 建立 in the app produces a working one. Silent and permanent.

describe('subscribeToPush 與 VAPID 金鑰', () => {
  let registerMock: ReturnType<typeof vi.fn>
  let subscribeMock: ReturnType<typeof vi.fn>
  let getSubscriptionMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    subscribeMock = vi.fn().mockResolvedValue(mockSubscription('https://push.example.com/fresh'))
    getSubscriptionMock = vi.fn().mockResolvedValue(null)
    registerMock = vi.fn().mockResolvedValue({
      pushManager: { subscribe: subscribeMock, getSubscription: getSubscriptionMock },
    })
    vi.stubGlobal('navigator', {
      serviceWorker: { register: registerMock, ready: Promise.resolve() },
    })
    vi.stubGlobal('window', { PushManager: {}, Notification: {} })
    vi.stubGlobal('Notification', { permission: 'granted', requestPermission: vi.fn() })
  })

  afterEach(() => vi.unstubAllGlobals())

  it('沿用金鑰相同的既有訂閱', async () => {
    getSubscriptionMock.mockResolvedValue(mockSubscription('https://push.example.com/kept', VAPID))

    const result = await subscribeToPush(VAPID)

    expect(subscribeMock).not.toHaveBeenCalled()
    expect(result.endpoint).toBe('https://push.example.com/kept')
  })

  it('金鑰換過了就丟掉舊訂閱重新申請，否則永遠是 403', async () => {
    const stale = mockSubscription('https://push.example.com/stale', OTHER_VAPID)
    getSubscriptionMock.mockResolvedValue(stale)

    const result = await subscribeToPush(VAPID)

    expect(stale.unsubscribe).toHaveBeenCalled()
    expect(subscribeMock).toHaveBeenCalled()
    expect(result.endpoint).toBe('https://push.example.com/fresh')
  })

  it('看不到既有訂閱用的是哪把金鑰時，重新申請而不是賭它相同', async () => {
    // Cannot confirm it matches. Re-subscribing costs one round trip; guessing
    // wrong costs every future alert.
    const unknown = mockSubscription('https://push.example.com/unknown', null)
    getSubscriptionMock.mockResolvedValue(unknown)

    await subscribeToPush(VAPID)

    expect(subscribeMock).toHaveBeenCalled()
  })
})

// --- which device am I? -------------------------------------------------------

describe('currentSubscriptionEndpoint', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('回報這台瀏覽器目前的訂閱位址', async () => {
    vi.stubGlobal('navigator', {
      serviceWorker: {
        getRegistration: vi.fn().mockResolvedValue({
          pushManager: {
            getSubscription: vi.fn().mockResolvedValue(mockSubscription('https://push.example.com/me')),
          },
        }),
      },
    })
    vi.stubGlobal('window', { PushManager: {}, Notification: {} })

    await expect(currentSubscriptionEndpoint()).resolves.toBe('https://push.example.com/me')
  })

  it('沒有訂閱就回 null，不要拋例外', async () => {
    vi.stubGlobal('navigator', {
      serviceWorker: {
        getRegistration: vi.fn().mockResolvedValue({
          pushManager: { getSubscription: vi.fn().mockResolvedValue(null) },
        }),
      },
    })
    vi.stubGlobal('window', { PushManager: {}, Notification: {} })

    await expect(currentSubscriptionEndpoint()).resolves.toBeNull()
  })

  it('不支援推播的瀏覽器回 null', async () => {
    vi.stubGlobal('navigator', {})
    vi.stubGlobal('window', {})
    await expect(currentSubscriptionEndpoint()).resolves.toBeNull()
  })
})
