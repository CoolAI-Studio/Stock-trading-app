/** Shown when a query fails. Without this the pages fall back to `?? 0` and
 * `?? []`, so a backend outage renders as "0 待確認訂單 / 0 部位 / 0 策略" --
 * pixel-identical to a genuinely quiet account, which is exactly the moment
 * the owner would close the tab and miss an order waiting for confirmation. */
export function QueryError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  // ApiError extends Error, so this covers both it and a raw network failure.
  const detail = error instanceof Error ? error.message : ''

  return (
    <div
      role="alert"
      className="flex items-center justify-between gap-4 rounded border border-red-800 bg-red-950/40 px-4 py-3"
    >
      <p className="text-sm text-red-300">
        無法讀取資料，畫面上的數字可能不是最新的。{detail && `（${detail}）`}
      </p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="shrink-0 rounded bg-red-900 px-3 py-1 text-sm font-medium text-red-100 hover:bg-red-800"
        >
          重試
        </button>
      )}
    </div>
  )
}
