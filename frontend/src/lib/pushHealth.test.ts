import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  forgetPushChannel,
  healPushSubscription,
  rememberPushChannel,
  rememberedPushChannelId,
} from './pushHealth'
import { api } from './api'
import * as push from './push'
import type { NotificationChannel } from './types'

vi.mock('./api', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

vi.mock('./push', () => ({
  isPushSupported: vi.fn(() => true),
  currentSubscriptionEndpoint: vi.fn(),
  subscribeToPush: vi.fn(),
}))

/**
 * Noticing that this phone stopped being subscribed, and fixing it.
 *
 * THE GAP THIS CLOSES, and it is the largest one left in the product. iOS
 * never fires `pushsubscriptionchange` -- a WebKit engineer said so on
 * Bugzilla #273063: "We don't fire PushSubscriptionChangeEvent". So when iOS
 * rotates or drops a subscription (the icon was deleted and re-added,
 * notifications were switched off and on, the VAPID pair changed, or one of
 * Apple's own Web.app bugs), nothing tells the app. The stored endpoint keeps
 * looking healthy, the channel keeps looking enabled, and the next alert --
 * possibly days later -- gets a 410 and is dropped.
 *
 * There is no event to listen for, so the only thing that works is looking:
 * on every app start, compare what this browser actually holds against what
 * the server has recorded, and repair the difference.
 *
 * HOW THE DEVICE KNOWS WHICH ROW IS ITS OWN. The endpoint cannot be the link,
 * because the endpoint is exactly the thing that changed. So the channel id is
 * remembered in localStorage at the moment this device creates it. On iOS a
 * Home Screen web app is explicitly exempt from ITP's seven-day storage
 * eviction, so that survives.
 */

const CHANNEL: NotificationChannel = {
  id: 7,
  channel_type: 'web_push',
  label: 'iphone',
  is_enabled: true,
  subscribed_events: null,
  quiet_start_hour: null,
  quiet_end_hour: null,
  last_sent_at: null,
  last_error: null,
  config_preview: 'web_push: ...',
  push_endpoint: 'https://push.example.com/old',
}

const TELEGRAM: NotificationChannel = { ...CHANNEL, id: 1, channel_type: 'telegram', push_endpoint: null }

beforeEach(() => {
  vi.clearAllMocks()
  // clearAllMocks resets call history, NOT implementations -- so the `false`
  // one test installs below would leak into every test after it and make them
  // all silently pass through the unsupported branch.
  vi.mocked(push.isPushSupported).mockReturnValue(true)
  localStorage.clear()
  vi.mocked(api.get).mockResolvedValue({ public_key: 'vapid-key' } as never)
  vi.mocked(api.patch).mockResolvedValue(CHANNEL as never)
  // A sane default subscription, so a test that is not ABOUT the resubscribe
  // does not have to restate it -- and so an unmocked call cannot quietly
  // return undefined and be written into the channel config.
  vi.mocked(push.subscribeToPush).mockResolvedValue({
    endpoint: 'https://push.example.com/NEW',
    p256dh: 'p',
    auth: 'a',
  })
})

afterEach(() => localStorage.clear())

// --- remembering which row is ours ------------------------------------------

describe('記住這台裝置建立的是哪一個管道', () => {
  it('存得起來也讀得回來', () => {
    rememberPushChannel(7)
    expect(rememberedPushChannelId()).toBe(7)
  })

  it('沒有記錄時回 null，不要回 NaN 或 0', () => {
    expect(rememberedPushChannelId()).toBeNull()
  })

  it('壞掉的內容當成沒有記錄', () => {
    localStorage.setItem('push-channel-id', 'not-a-number')
    expect(rememberedPushChannelId()).toBeNull()
  })

  it('可以忘掉（刪除管道時要用）', () => {
    rememberPushChannel(7)
    forgetPushChannel()
    expect(rememberedPushChannelId()).toBeNull()
  })
})

// --- the healthy case does nothing ------------------------------------------

describe('一切正常時不要多做事', () => {
  it('端點相同就什麼都不做', async () => {
    rememberPushChannel(7)
    vi.mocked(push.currentSubscriptionEndpoint).mockResolvedValue('https://push.example.com/old')

    const result = await healPushSubscription([CHANNEL])

    expect(result.kind).toBe('healthy')
    expect(api.patch).not.toHaveBeenCalled()
    expect(push.subscribeToPush).not.toHaveBeenCalled()
  })

  it('這台裝置沒有建立過推播管道就不用檢查', async () => {
    const result = await healPushSubscription([TELEGRAM])

    expect(result.kind).toBe('not-configured')
    expect(push.currentSubscriptionEndpoint).not.toHaveBeenCalled()
  })

  it('瀏覽器根本不支援推播時安靜跳過', async () => {
    vi.mocked(push.isPushSupported).mockReturnValue(false)
    rememberPushChannel(7)

    const result = await healPushSubscription([CHANNEL])

    expect(result.kind).toBe('not-configured')
  })
})

// --- the endpoint rotated ---------------------------------------------------

describe('iOS 把訂閱換掉了', () => {
  it('端點不一樣時把伺服器上的更新成現在這個', async () => {
    rememberPushChannel(7)
    vi.mocked(push.currentSubscriptionEndpoint).mockResolvedValue('https://push.example.com/NEW')

    const result = await healPushSubscription([CHANNEL])

    expect(result.kind).toBe('repaired')
    expect(api.patch).toHaveBeenCalledWith(
      '/api/notifications/channels/7',
      expect.objectContaining({
        config: expect.objectContaining({ endpoint: 'https://push.example.com/NEW' }),
      }),
    )
  })

  it('修復時要一併帶上新的加密金鑰，不能只換 endpoint', async () => {
    // The p256dh/auth pair belongs to the subscription. Updating the endpoint
    // and keeping the old keys produces a channel that looks repaired and
    // cannot be decrypted by the device -- silent again.
    rememberPushChannel(7)
    vi.mocked(push.currentSubscriptionEndpoint).mockResolvedValue('https://push.example.com/NEW')
    vi.mocked(push.subscribeToPush).mockResolvedValue({
      endpoint: 'https://push.example.com/NEW',
      p256dh: 'new-p',
      auth: 'new-a',
    })

    await healPushSubscription([CHANNEL])

    expect(api.patch).toHaveBeenCalledWith(
      '/api/notifications/channels/7',
      expect.objectContaining({
        config: { endpoint: 'https://push.example.com/NEW', p256dh: 'new-p', auth: 'new-a' },
      }),
    )
  })

  it('修好之後順便把停用狀態解開 —— 之前的 410 會把它關掉', async () => {
    rememberPushChannel(7)
    vi.mocked(push.currentSubscriptionEndpoint).mockResolvedValue('https://push.example.com/NEW')

    await healPushSubscription([{ ...CHANNEL, is_enabled: false, last_error: 'HTTP 410' }])

    expect(api.patch).toHaveBeenCalledWith(
      '/api/notifications/channels/7',
      expect.objectContaining({ is_enabled: true }),
    )
  })
})

// --- the subscription is gone altogether ------------------------------------

describe('訂閱整個不見了', () => {
  it('沒有訂閱但權限還在時，重新訂閱並回寫', async () => {
    rememberPushChannel(7)
    vi.mocked(push.currentSubscriptionEndpoint).mockResolvedValue(null)
    vi.mocked(push.subscribeToPush).mockResolvedValue({
      endpoint: 'https://push.example.com/fresh',
      p256dh: 'p',
      auth: 'a',
    })

    const result = await healPushSubscription([CHANNEL])

    expect(push.subscribeToPush).toHaveBeenCalledWith('vapid-key')
    expect(result.kind).toBe('repaired')
    expect(api.patch).toHaveBeenCalled()
  })

  it('重新訂閱失敗時回報「要手動處理」，不要假裝修好了', async () => {
    // Permission revoked is the common cause, and no amount of retrying fixes
    // it. Reporting success here would leave the owner believing their alerts
    // work.
    rememberPushChannel(7)
    vi.mocked(push.currentSubscriptionEndpoint).mockResolvedValue(null)
    vi.mocked(push.subscribeToPush).mockRejectedValue(new Error('未取得通知權限'))

    const result = await healPushSubscription([CHANNEL])

    expect(result.kind).toBe('needs-attention')
    expect(api.patch).not.toHaveBeenCalled()
  })
})

// --- the remembered row is not there any more -------------------------------

describe('記住的那一列已經不在了', () => {
  it('管道被刪掉時忘掉它，不要一直想修一個不存在的東西', async () => {
    rememberPushChannel(7)

    const result = await healPushSubscription([TELEGRAM])

    expect(result.kind).toBe('not-configured')
    expect(rememberedPushChannelId()).toBeNull()
  })
})

// --- it must never make things worse ----------------------------------------

describe('修復本身不能造成傷害', () => {
  it('伺服器回錯誤時安靜放棄，不要讓整個 app 掛掉', async () => {
    // This runs on every app start. An exception escaping here would take the
    // page down on load -- far worse than a subscription that needs fixing.
    rememberPushChannel(7)
    vi.mocked(push.currentSubscriptionEndpoint).mockResolvedValue('https://push.example.com/NEW')
    vi.mocked(api.patch).mockRejectedValue(new Error('500'))

    const result = await healPushSubscription([CHANNEL])

    expect(result.kind).toBe('needs-attention')
  })

  it('只碰自己記住的那一列，絕不動別台裝置的', async () => {
    const other: NotificationChannel = {
      ...CHANNEL,
      id: 9,
      label: 'laptop',
      push_endpoint: 'https://push.example.com/laptop',
    }
    rememberPushChannel(7)
    vi.mocked(push.currentSubscriptionEndpoint).mockResolvedValue('https://push.example.com/NEW')

    await healPushSubscription([CHANNEL, other])

    expect(api.patch).toHaveBeenCalledTimes(1)
    expect(api.patch).toHaveBeenCalledWith('/api/notifications/channels/7', expect.anything())
  })
})
