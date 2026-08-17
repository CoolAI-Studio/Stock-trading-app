import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { isPushSupported, subscribeToPush, unsubscribeFromPush } from './push'

function mockSubscription(endpoint = 'https://push.example.com/x') {
  return {
    endpoint,
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

describe('subscribeToPush', () => {
  let registerMock: ReturnType<typeof vi.fn>
  let subscribeMock: ReturnType<typeof vi.fn>
  let requestPermissionMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    subscribeMock = vi.fn().mockResolvedValue(mockSubscription())
    registerMock = vi.fn().mockResolvedValue({
      pushManager: { subscribe: subscribeMock },
    })
    requestPermissionMock = vi.fn().mockResolvedValue('granted')

    vi.stubGlobal('navigator', {
      serviceWorker: { register: registerMock, ready: Promise.resolve() },
    })
    vi.stubGlobal('window', { PushManager: {}, Notification: {} })
    vi.stubGlobal('Notification', { requestPermission: requestPermissionMock })
  })

  afterEach(() => vi.unstubAllGlobals())

  it('registers the service worker, requests permission, and returns the subscription config', async () => {
    const result = await subscribeToPush('BCFiiE5pxNqJyHn6QEeewWKjMVfko4jbGnPX6kcmbZzyxnbdLjnQClrwCygjbO5f1zgjHx90FkiQKyaJE-hGYdI')

    expect(registerMock).toHaveBeenCalledWith('/sw.js')
    expect(requestPermissionMock).toHaveBeenCalled()
    expect(result).toEqual({
      endpoint: 'https://push.example.com/x',
      p256dh: 'p256dh-value',
      auth: 'auth-value',
    })
  })

  it('throws when permission is denied', async () => {
    requestPermissionMock.mockResolvedValue('denied')
    await expect(subscribeToPush('key')).rejects.toThrow('未取得通知權限')
  })

  it('throws when the browser does not support push', async () => {
    vi.stubGlobal('navigator', {})
    await expect(subscribeToPush('key')).rejects.toThrow('這個瀏覽器不支援推播通知')
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

    expect(getRegistrationMock).toHaveBeenCalledWith('/sw.js')
    expect(subscription.unsubscribe).toHaveBeenCalled()
  })

  it('does nothing when push is unsupported', async () => {
    vi.stubGlobal('navigator', {})
    await expect(unsubscribeFromPush()).resolves.toBeUndefined()
  })
})
