import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ActionError } from './ActionError'
import { api, downloadPost } from '../lib/api'
import type { BackupSchedule } from '../lib/types'

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
  return (
    <div className="space-y-4">
      <ManualBackup />
      <ScheduledBackup />
    </div>
  )
}

/** Send it on a timer, so a backup does not depend on remembering.
 *
 * Email because it is the destination that needs no new account -- the SMTP
 * channel already configured for alerts is reused. Drive and Dropbox look
 * easier since the owner has an account, but neither has an app-password
 * equivalent; programmatic upload means registering an OAuth application.
 */
function ScheduledBackup() {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: ['backup-schedule'],
    queryFn: () => api.get<BackupSchedule>('/api/backup/schedule'),
  })

  const [enabled, setEnabled] = useState<boolean | null>(null)
  // null means "not touched yet, show the stored value". Falling back on an
  // empty string instead made clearing the box impossible: it re-filled from
  // the stored value on the next render, so typing 30 into a cleared field
  // produced 730.
  const [days, setDays] = useState<string | null>(null)
  const [passphrase, setPassphrase] = useState('')
  const [toAddr, setToAddr] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: () =>
      api.put<BackupSchedule>('/api/backup/schedule', {
        is_enabled: enabled ?? data?.is_enabled ?? false,
        interval_days: Number(days ?? data?.interval_days ?? 7),
        to_addr: (toAddr ?? data?.to_addr) || null,
        // Omitted when untouched, so changing the interval does not force a
        // passphrase they may not have to hand.
        ...(passphrase ? { passphrase } : {}),
      }),
    onSuccess: () => {
      setPassphrase('')
      queryClient.invalidateQueries({ queryKey: ['backup-schedule'] })
    },
  })

  if (!data) return null
  const on = enabled ?? data.is_enabled

  return (
    <section aria-label="自動備份" className="space-y-3 rounded border border-slate-800 p-4">
      <h2 className="text-sm font-semibold text-slate-300">自動備份（寄到 Email）</h2>
      <p className="text-sm text-slate-400">
        定期把加密備份寄到你的信箱，用的是「通知」頁那個 Email 管道，不用另外註冊什麼。
      </p>
      <p className="rounded border border-amber-700 bg-amber-950/40 px-3 py-2 text-xs text-amber-200">
        自動加密表示密碼必須存在伺服器上（跟券商金鑰一樣加密存放）。
        <strong>如果整個伺服器沒了，那份也一起沒了</strong>——請自己另外抄一份下來。
      </p>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={on}
          onChange={(e) => setEnabled(e.target.checked)}
          aria-label="開啟自動備份"
        />
        開啟自動備份
      </label>

      {on && (
        <div className="flex flex-wrap gap-3">
          <div>
            <label htmlFor="backup-interval" className="text-sm text-slate-400">
              每隔幾天
            </label>
            <input
              id="backup-interval"
              inputMode="numeric"
              value={days ?? String(data.interval_days)}
              onChange={(e) => setDays(e.target.value)}
              className="block w-24 rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
          <div>
            <label htmlFor="backup-schedule-passphrase" className="text-sm text-slate-400">
              自動備份密碼
            </label>
            <input
              id="backup-schedule-passphrase"
              type="password"
              autoComplete="new-password"
              value={passphrase}
              onChange={(e) => setPassphrase(e.target.value)}
              placeholder={data.has_passphrase ? '已設定，留空就不變' : '尚未設定'}
              className="block w-56 rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
          <div>
            <label htmlFor="backup-to-addr" className="text-sm text-slate-400">
              寄到（選填）
            </label>
            <input
              id="backup-to-addr"
              value={toAddr ?? data.to_addr ?? ''}
              onChange={(e) => setToAddr(e.target.value)}
              placeholder="沿用通知管道的收件人"
              className="block w-56 rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={save.isPending}
          onClick={() => save.mutate()}
          className="rounded bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          儲存自動備份設定
        </button>
        {save.isSuccess && <span className="text-sm text-emerald-400">已儲存。</span>}
        <ActionError error={save.error} />
      </div>

      {data.last_sent_at && (
        <p className="text-xs text-slate-500">
          上次寄出：{new Date(data.last_sent_at).toLocaleString()}
        </p>
      )}
      {/* A backup silently not arriving is the failure this feature exists to
          prevent, so the reason has to be on the page that offered it. */}
      {data.last_error && (
        <p className="rounded border border-red-800 bg-red-950/40 px-2 py-1 text-xs text-red-300">
          上次沒寄成功：{data.last_error}
        </p>
      )}
    </section>
  )
}

function ManualBackup() {
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
      {/* 這裡本來寫著「通知管道要另一把金鑰，這個檔案救不回來」。**那是錯的**，而
          它的來源是 backup.py 裡一句過期的註解（說 config_encrypted 是原樣帶走、仍以
          部署金鑰加密）。實際跑一次就知道：那一欄的型別是 EncryptedJSON，SQLAlchemy
          讀取時就解密了，所以進到封套裡的是明文。

          錯在這一頁比錯在註解裡嚴重得多——它會讓一個備份做對了的人以為自己白做了，
          然後放棄備份。所以改成說真的那件事：這個檔案自己就夠，而那把金鑰要備份是為
          了**線上那個資料庫**，不是為了這個檔案。

          由 tests/test_backup.py 的兩條釘著：passphrase 打得開、沒有 passphrase 的人
          連一個位元組都讀不到。 */}
      <p className="rounded border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-400">
        <strong className="text-slate-300">這個檔案是自給自足的。</strong>
        還原只需要上面這個密碼，<strong>不需要</strong>你這份部署的
        <code className="font-mono"> SECRET_ENCRYPTION_KEY</code>——通知管道的設定在
        檔案裡是解開的，由這個密碼保護。
      </p>
      <p className="rounded border border-amber-700 bg-amber-950/40 px-3 py-2 text-xs text-amber-200">
        <strong>但那把金鑰還是要另外存一份。</strong>
        它保護的是<strong>線上資料庫</strong>裡那些設定：金鑰沒了，那些資料還在，卻永
        遠打不開。而它現在由你的部署平台自動產生，
        <strong>所以你大概沒看過它</strong>——去平台後台的環境變數那一頁複製出來，
        跟這個備份檔分開存進密碼管理器。
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
