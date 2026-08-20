import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { healPushSubscription, type PushHealth } from '../lib/pushHealth'
import type { NotificationChannel } from '../lib/types'

/**
 * Checks, on app start, that this device is still actually subscribed.
 *
 * iOS never fires `pushsubscriptionchange`, so there is no event to hang this
 * off; "when the app opens" is the only moment available. See lib/pushHealth
 * for what it compares and repairs.
 *
 * SILENT WHEN IT WORKS, INCLUDING WHEN IT REPAIRS SOMETHING. A banner that
 * appears on every load stops being read, and then the one that matters gets
 * dismissed along with the rest. It speaks only when it could NOT fix things
 * -- which is exactly the state where alerts are already not arriving and the
 * owner has no other way to find out.
 */
export function PushSelfHeal() {
  const [health, setHealth] = useState<PushHealth | null>(null)
  // Runs once per mount. The channel query refetches on its own schedule and
  // re-running the repair on every one of those would mean re-subscribing over
  // and over.
  const done = useRef(false)

  const channelsQuery = useQuery({
    queryKey: ['notification-channels'],
    queryFn: () => api.get<NotificationChannel[]>('/api/notifications/channels'),
    // Offline, or signed out mid-flight: neither is worth a retry storm at
    // startup, and neither is something the owner can act on here.
    retry: false,
  })

  useEffect(() => {
    if (done.current || !channelsQuery.isSuccess) return
    done.current = true
    // Nothing here may throw into render. This runs on every page load, and an
    // exception escaping would take the app down at startup -- far worse than
    // a subscription that needs repairing.
    healPushSubscription(channelsQuery.data)
      .then(setHealth)
      .catch(() => setHealth(null))
  }, [channelsQuery.isSuccess, channelsQuery.data])

  if (health?.kind !== 'needs-attention') return null

  return (
    <p
      role="alert"
      className="border-b border-red-800/60 bg-red-950/40 px-4 py-2 text-sm text-red-100 sm:px-6"
    >
      {health.message}
    </p>
  )
}
