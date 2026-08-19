import { useState } from 'react'
import { ActionError } from './ActionError'
import { downloadPost } from '../lib/api'

const MIN_LENGTH = 8

/** Download an encrypted copy of everything in this account.
 *
 * The deployment documents a manual pg_dump, which is a thing a person has to
 * remember, and the free-tier database keeps only a few hours of
 * point-in-time recovery -- so on the day it is needed the newest copy could
 * easily be months old.
 *
 * The passphrase is chosen here and stored nowhere. That is the point: the
 * archive carries broker keys and notification tokens, and a backup that can
 * only be opened with a secret held on the server it is backing up is not a
 * backup of anything. It is also why losing the passphrase means losing the
 * file, which the panel says before asking for one.
 */
export function BackupPanel() {
  const [passphrase, setPassphrase] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [done, setDone] = useState(false)

  const tooShort = passphrase.length > 0 && passphrase.length < MIN_LENGTH
  const mismatch = confirmation.length > 0 && confirmation !== passphrase
  const ready = passphrase.length >= MIN_LENGTH && confirmation === passphrase

  async function handleDownload() {
    setBusy(true)
    setError(null)
    setDone(false)
    try {
      const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, '')
      await downloadPost('/api/backup', { passphrase }, `trading-backup-${stamp}.bak`)
      setDone(true)
      setPassphrase('')
      setConfirmation('')
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-label="下載備份" className="space-y-3 rounded border border-slate-800 p-4">
      <h2 className="text-sm font-semibold text-slate-300">下載備份</h2>
      <p className="text-sm text-slate-400">
        把這個帳號的策略、訂單、部位、風險設定、通知管道與自選清單打包成一個檔案，
        用你自己設的密碼加密。存到隨身碟或雲端都可以——沒有密碼誰都打不開。
      </p>
      <p className="rounded border border-amber-700 bg-amber-950/40 px-3 py-2 text-xs text-amber-200">
        這個密碼<strong>不會存在系統裡</strong>。忘記了就再也打不開那個檔案，我們也救不回來——
        請跟備份檔分開保存。
      </p>

      <div className="flex flex-wrap gap-3">
        <div>
          <label htmlFor="backup-passphrase" className="text-sm text-slate-400">
            備份密碼
          </label>
          <input
            id="backup-passphrase"
            type="password"
            autoComplete="new-password"
            value={passphrase}
            onChange={(e) => setPassphrase(e.target.value)}
            className="block w-56 rounded border border-slate-700 bg-slate-950 px-2 py-1"
          />
          {tooShort && <p className="mt-1 text-xs text-amber-300">至少 {MIN_LENGTH} 個字</p>}
        </div>
        <div>
          <label htmlFor="backup-passphrase-again" className="text-sm text-slate-400">
            再輸入一次
          </label>
          <input
            id="backup-passphrase-again"
            type="password"
            autoComplete="new-password"
            value={confirmation}
            onChange={(e) => setConfirmation(e.target.value)}
            className="block w-56 rounded border border-slate-700 bg-slate-950 px-2 py-1"
          />
          {/* Typed twice on purpose: a typo in a passphrase that is stored
              nowhere produces a file nobody can ever open, and there would be
              no way to find that out until the day it mattered. */}
          {mismatch && <p className="mt-1 text-xs text-amber-300">兩次輸入不一樣</p>}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={!ready || busy}
          onClick={handleDownload}
          className="rounded bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          {busy ? '打包中…' : '下載備份'}
        </button>
        {done && <span className="text-sm text-emerald-400">已下載。</span>}
        <ActionError error={error} />
      </div>
    </section>
  )
}
