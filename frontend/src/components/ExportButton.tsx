import { useState } from 'react'
import { ActionError } from './ActionError'
import { downloadFile } from '../lib/api'

/** Downloads one of the CSV exports.
 *
 * The file name carries today's date, because 「orders.csv」 in a downloads
 * folder alongside last year's 「orders.csv」 is not a file anyone can
 * identify later.
 */
export function ExportButton({
  resource,
  label,
}: {
  resource: 'orders' | 'positions' | 'alerts'
  label: string
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  async function handleClick() {
    setBusy(true)
    setError(null)
    try {
      const today = new Date().toISOString().slice(0, 10)
      await downloadFile(`/api/export/${resource}.csv`, `${resource}-${today}.csv`)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <button
        type="button"
        disabled={busy}
        onClick={handleClick}
        className="rounded bg-slate-700 px-3 py-1 text-sm font-medium text-white hover:bg-slate-600 disabled:opacity-50"
      >
        {busy ? '準備中…' : label}
      </button>
      <ActionError error={error} />
    </span>
  )
}
