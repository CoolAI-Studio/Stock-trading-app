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
      <RestoreBackup />
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

type RestoreReport = {
  strategies: number
  channels: number
  orders: number
  alerts: number
  positions: number
  positions_skipped: number
  watchlist: number
  watchlist_skipped: number
  risk_settings_created: boolean
  expired_pending: number
}

/** 把備份檔倒回來。
 *
 * ＊ 為什麼這顆按鈕要存在。
 *
 * 備份檔一直都做得出來（上面那一段，或每天自動寄到他信箱），但**倒回去的路只存在於
 * 文件裡**——而那條路的第一句話是「在你的電腦上跑 psql」。CLAUDE.md 寫得很清楚：任何
 * 「請在你的電腦上跑這支腳本」的指示，對這個使用者等於流程到此結束。做得出備份卻還
 * 不回去，等於沒有備份。
 *
 * ＊ 為什麼沒有「確定要覆蓋嗎」那種確認。
 *
 * 因為沒有東西會被覆蓋。還原一律**新增**，從不覆寫、從不刪除（backup.py 的 restore
 * 裡有整段理由）——他按錯了也只是多了一些停用的東西，而多出來的東西刪得掉。
 *
 * ＊ 為什麼結果要逐項報數字。
 *
 * 「還原完成」四個字說不出他真正需要知道的那件事：**策略和通知管道是停用的，等他自
 * 己打開**。沒有說出口的話，他會以為提醒已經在跑了——而那正是這個產品唯一不能失效的
 * 東西。
 */
function RestoreBackup() {
  const [file, setFile] = useState<File | null>(null)
  const [passphrase, setPassphrase] = useState('')
  const [report, setReport] = useState<RestoreReport | null>(null)

  const run = useMutation({
    mutationFn: async () => {
      const form = new FormData()
      form.append('file', file as File)
      form.append('passphrase', passphrase)
      return api.upload<RestoreReport>('/api/backup/restore', form)
    },
    onSuccess: (result) => {
      setReport(result)
      setPassphrase('')
    },
  })

  const ready = file !== null && passphrase.length >= MIN_LENGTH

  return (
    <section aria-label="還原備份" className="space-y-3 rounded border border-slate-800 p-4">
      <h2 className="text-sm font-semibold text-slate-300">從備份還原</h2>
      <p className="text-sm text-slate-400">
        選一個備份檔、輸入當初設的那個密碼，裡面的東西就會加回這個帳號底下。
      </p>
      <p className="rounded border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-400">
        <strong className="text-slate-300">它只會加，不會刪也不會蓋掉你現在的東西。</strong>
        你現在的策略、持股、風控設定都會原樣留著；備份裡的東西是<strong>額外</strong>加進來的，
        而且<strong>策略和通知管道會是停用的</strong>——你自己打開要用的那幾個就好。
        同一檔已經持有的部位、已經在自選清單裡的代號會被跳過，不會變成兩筆。
      </p>

      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="restore-file" className="block text-sm text-slate-400">
            備份檔（.bak）
          </label>
          <input
            id="restore-file"
            type="file"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null)
              setReport(null)
            }}
            className="mt-1 block text-sm text-slate-300 file:mr-3 file:rounded file:border-0 file:bg-slate-700 file:px-3 file:py-1.5 file:text-sm file:text-slate-200"
          />
        </div>
        <div>
          <label htmlFor="restore-passphrase" className="block text-sm text-slate-400">
            還原密碼
          </label>
          <input
            id="restore-passphrase"
            type="password"
            autoComplete="off"
            value={passphrase}
            onChange={(e) => setPassphrase(e.target.value)}
            className="mt-1 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100"
          />
        </div>
        <button
          type="button"
          disabled={!ready || run.isPending}
          onClick={() => run.mutate()}
          className="rounded bg-slate-700 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-600 disabled:opacity-50"
        >
          {run.isPending ? '還原中…' : '還原'}
        </button>
        <ActionError error={run.error} />
      </div>

      {report && (
        <div className="space-y-2 rounded border border-emerald-800 bg-emerald-950/30 px-3 py-2 text-sm text-emerald-200">
          <p>
            <strong>還原好了。</strong>加回來的有：策略 {report.strategies} 支、通知管道{' '}
            {report.channels} 個、持股 {report.positions} 筆、自選 {report.watchlist} 檔、
            訊號紀錄 {report.orders} 筆、提醒紀錄 {report.alerts} 筆
            {report.risk_settings_created ? '，以及風控設定' : ''}。
          </p>
          {/* 這一句是整段裡最重要的。沒有它，他會以為提醒已經在跑了。 */}
          <p className="text-amber-200">
            <strong>加回來的策略和通知管道都是停用的。</strong>
            去「策略」和「通知」那兩頁，把你要用的打開——沒有打開的不會發出任何提醒。
          </p>
          {(report.positions_skipped > 0 || report.watchlist_skipped > 0) && (
            <p className="text-slate-300">
              跳過了 {report.positions_skipped} 筆持股和 {report.watchlist_skipped} 檔自選
              ——那幾檔你現在已經有了，重複加進來會讓部位和停損算錯。
            </p>
          )}
          {report.expired_pending > 0 && (
            <p className="text-slate-300">
              有 {report.expired_pending} 筆當時還在「待確認」的訊號，加回來的時候標成已過期
              ——那是好久以前的價格，不該現在再問你一次要不要動作。
            </p>
          )}
        </div>
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
