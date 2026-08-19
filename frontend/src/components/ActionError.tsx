/** Shown when an action the owner *took* fails.
 *
 * Distinct from QueryError, which covers a page that could not load. This is
 * the other half: every mutation on this app was written with onSuccess and
 * nothing else, so a refused confirm, a refused flatten, a refused delete all
 * did precisely nothing on screen. The worst case is real -- when the broker
 * call fails the backend commits the order as FAILED and *then* returns 422,
 * so the row went on rendering as 待確認 while the database said otherwise,
 * and the owner's natural response is to press the button again.
 *
 * Inline next to the control rather than a toast: the message has to stay put
 * long enough to read, and it has to be obvious which row it belongs to.
 */
export function ActionError({ error, className = '' }: { error: unknown; className?: string }) {
  if (!error) return null
  // ApiError extends Error and carries the backend's `detail` as its message,
  // which is already written for a person -- pass it through rather than
  // replacing it with something vaguer.
  const detail = error instanceof Error ? error.message : '未知的錯誤'

  return (
    <p
      role="alert"
      className={`rounded border border-red-800 bg-red-950/40 px-2 py-1 text-xs text-red-300 ${className}`}
    >
      操作失敗：{detail}
    </p>
  )
}
