import { ActionError } from './ActionError'

/** One delete control, so every list behaves the same way.
 *
 * Always asks first. Deleting is the one action on these pages with no undo,
 * and on a phone the button sits close enough to 查看 that a mis-tap is a
 * real way to lose a run you wanted. The confirm text names the thing rather
 * than saying "are you sure?", because the answer to a generic prompt is
 * always yes.
 *
 * Failures show inline rather than vanishing: the backend refuses some
 * deletes on purpose -- a confirmed order moved a position and is counted by
 * the capital gate -- and the reason it gives is the whole point.
 */
export function DeleteButton({
  what,
  onConfirm,
  pending = false,
  error = null,
  label = '刪除',
  tone = 'quiet',
}: {
  /** Named in the prompt: 「這筆回測」, 「AAPL 的提醒紀錄」. */
  what: string
  onConfirm: () => void
  pending?: boolean
  error?: unknown
  label?: string
  tone?: 'quiet' | 'loud'
}) {
  const skin =
    tone === 'loud'
      ? 'bg-red-900 text-red-100 hover:bg-red-800'
      : 'bg-slate-700 text-white hover:bg-slate-600'

  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <button
        type="button"
        disabled={pending}
        onClick={() => {
          if (window.confirm(`確定要刪除${what}嗎？此操作無法復原。`)) onConfirm()
        }}
        className={`rounded px-3 py-1 text-sm font-medium disabled:opacity-50 ${skin}`}
      >
        {pending ? '刪除中…' : label}
      </button>
      <ActionError error={error} />
    </span>
  )
}
