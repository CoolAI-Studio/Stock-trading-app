import { api } from './api'
import { currentSubscriptionEndpoint, isPushSupported, subscribeToPush } from './push'
import type { NotificationChannel } from './types'

/**
 * Noticing that this phone quietly stopped being subscribed, and repairing it.
 *
 * THE GAP THIS CLOSES is the largest one left in the product. iOS never fires
 * `pushsubscriptionchange` -- a WebKit engineer stated so directly on Bugzilla
 * #273063: "We don't fire PushSubscriptionChangeEvent". So when iOS rotates or
 * drops a subscription (the Home Screen icon deleted and re-added,
 * notifications toggled off and on in Settings, the server's VAPID pair
 * changed, or one of Apple's own Web.app bugs), nothing informs the app. The
 * stored endpoint still looks fine, the channel still shows as enabled, and
 * the next real alert -- possibly days later -- gets a 410 and is dropped.
 *
 * There is no event to subscribe to, so the only thing that works is looking.
 * This runs on app start: compare what this browser actually holds against
 * what the server recorded, and fix the difference.
 *
 * HOW THE DEVICE KNOWS WHICH ROW IS ITS OWN. Not by endpoint -- the endpoint is
 * precisely what changed. The channel id is remembered in localStorage when
 * this device creates the channel. On iOS a Home Screen web app is explicitly
 * exempt from ITP's seven-day storage eviction (WebKit's own note on the
 * policy), so it survives.
 *
 * IT MUST NEVER MAKE THINGS WORSE. Everything here is wrapped: this runs on
 * every page load, and an exception escaping would take the app down at
 * startup, which is a far worse failure than a subscription needing repair.
 * It also only ever touches the ONE row this device remembers creating --
 * repairing somebody else's device from here would be the same class of bug as
 * the delete that unsubscribed the wrong browser.
 */

const CHANNEL_KEY = 'push-channel-id'

export type PushHealth =
  /** Nothing to check, or nothing this device owns. */
  | { kind: 'not-configured' }
  /** The browser's subscription matches what the server has. */
  | { kind: 'healthy' }
  /** It had drifted and has been put right. */
  | { kind: 'repaired' }
  /** Drifted and could not be fixed from here -- the owner has to act. */
  | { kind: 'needs-attention'; message: string }

export function rememberPushChannel(channelId: number): void {
  try {
    localStorage.setItem(CHANNEL_KEY, String(channelId))
  } catch {
    // Private mode, quota, a locked-down browser. Losing the link only costs
    // the self-heal; it must not stop the channel being created.
  }
}

export function forgetPushChannel(): void {
  try {
    localStorage.removeItem(CHANNEL_KEY)
  } catch {
    /* see rememberPushChannel */
  }
}

export function rememberedPushChannelId(): number | null {
  try {
    const raw = localStorage.getItem(CHANNEL_KEY)
    if (!raw) return null
    const id = Number(raw)
    // Number('') is 0 and Number('x') is NaN; neither is a channel id, and
    // both would send a repair at /channels/0 or /channels/NaN.
    return Number.isInteger(id) && id > 0 ? id : null
  } catch {
    return null
  }
}

export async function healPushSubscription(
  channels: NotificationChannel[],
): Promise<PushHealth> {
  if (!isPushSupported()) return { kind: 'not-configured' }

  const channelId = rememberedPushChannelId()
  if (channelId === null) return { kind: 'not-configured' }

  const mine = channels.find((c) => c.id === channelId && c.channel_type === 'web_push')
  if (!mine) {
    // Deleted from another device, or from this one before the id was cleared.
    // Forget it rather than trying to repair a row that is not there.
    forgetPushChannel()
    return { kind: 'not-configured' }
  }

  let endpoint: string | null
  try {
    endpoint = await currentSubscriptionEndpoint()
  } catch {
    return { kind: 'not-configured' }
  }

  if (endpoint !== null && endpoint === mine.push_endpoint) {
    return { kind: 'healthy' }
  }

  // Either the endpoint rotated or the subscription is gone. Both are repaired
  // the same way -- take a fresh subscription and write the whole thing back.
  // Only replacing the endpoint would leave the old p256dh/auth in place, and a
  // push encrypted to the wrong keys is undeliverable in a way that looks
  // repaired.
  try {
    const { public_key } = await api.get<{ public_key: string }>(
      '/api/notifications/push/vapid-public-key',
    )
    const config = await subscribeToPush(public_key)

    await api.patch(`/api/notifications/channels/${mine.id}`, {
      label: mine.label,
      // A previous 410 will have switched it off. Repairing the subscription
      // without re-enabling the channel fixes the plumbing and leaves the tap
      // closed.
      is_enabled: true,
      subscribed_events: mine.subscribed_events,
      quiet_start_hour: mine.quiet_start_hour,
      quiet_end_hour: mine.quiet_end_hour,
      config,
    })
    return { kind: 'repaired' }
  } catch (error) {
    // Permission revoked is the usual cause and no retry fixes it. Reporting
    // success here would leave the owner believing their alerts work.
    return {
      kind: 'needs-attention',
      message:
        error instanceof Error && error.message.includes('權限')
          ? '這台裝置的推播訂閱失效了，而且通知權限已被關閉，沒辦法自動修好。' +
            '請到裝置的「設定」→ 通知把這個 app 的通知打開，再回「通知」頁重新建立一次。'
          : '這台裝置的推播訂閱失效了，自動修復沒有成功。請到「通知」頁把這個推播管道刪掉再重新建立。',
    }
  }
}
