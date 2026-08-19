/** Paging for the history lists.
 *
 * Every list here fetched a fixed first page and said nothing about it, so
 * after a few weeks the orders history simply stopped at the most recent
 * fifty with no hint that anything was missing -- and because pending rows
 * shared the same budget, the busier the account got the less history was
 * visible. The backend has supported limit/offset the whole time.
 *
 * No total count: the API does not return one, and inventing "第 2 頁，共 ?
 * 頁" would be worse than admitting we only know whether there is more.
 */
export function Pager({
  offset,
  pageSize,
  shown,
  onChange,
}: {
  offset: number
  pageSize: number
  /** How many rows this page actually returned. A full page means there is
   * probably another; a short one means this is the end. */
  shown: number
  onChange: (offset: number) => void
}) {
  const hasPrevious = offset > 0
  const hasNext = shown >= pageSize
  if (!hasPrevious && !hasNext) return null

  const from = offset + 1
  const to = offset + shown

  return (
    <div className="flex items-center gap-3 text-sm">
      <button
        type="button"
        disabled={!hasPrevious}
        onClick={() => onChange(Math.max(0, offset - pageSize))}
        className="rounded bg-slate-700 px-3 py-1 font-medium text-white hover:bg-slate-600 disabled:opacity-40"
      >
        上一頁
      </button>
      <span className="tabular-nums text-slate-500">
        {shown === 0 ? '沒有資料' : `第 ${from}–${to} 筆`}
      </span>
      <button
        type="button"
        disabled={!hasNext}
        onClick={() => onChange(offset + pageSize)}
        className="rounded bg-slate-700 px-3 py-1 font-medium text-white hover:bg-slate-600 disabled:opacity-40"
      >
        下一頁
      </button>
    </div>
  )
}
