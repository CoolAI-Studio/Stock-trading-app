import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BackupPanel } from './BackupPanel'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api, downloadPost } from '../lib/api'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  downloadPost: vi.fn(),
  api: { get: vi.fn(), put: vi.fn(), upload: vi.fn() },
}))

const SCHEDULE = {
  is_enabled: false,
  interval_days: 7,
  to_addr: null,
  last_sent_at: null,
  last_error: null,
  has_passphrase: false,
}

const GOOD = 'a-long-enough-passphrase'

async function fill(passphrase: string, confirmation = passphrase) {
  const user = userEvent.setup()
  renderPanel()
  await user.type(screen.getByLabelText('備份密碼'), passphrase)
  if (confirmation) await user.type(screen.getByLabelText('再輸入一次'), confirmation)
  return user
}

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <BackupPanel />
    </QueryClientProvider>,
  )
}

describe('BackupPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.get).mockResolvedValue(SCHEDULE as never)
    vi.mocked(api.put).mockResolvedValue(SCHEDULE as never)
  })

  it('說得出這個檔案自己就夠，以及那把金鑰為什麼還是要存', () => {
    // 這一條原本斷言的是相反的事：「通知管道救不回來，還原需要部署那把金鑰」。
    // 那是錯的，而錯的來源是 backup.py 裡一句過期的註解——實測（見
    // tests/test_backup.py）只用 passphrase 就讀得回那個 token。
    //
    // 錯在這一頁比錯在註解裡嚴重：它會讓一個備份做對了的人以為自己白做了。
    //
    // 兩件事都要說，而且不可以混在一起：這個**檔案**自給自足；那把金鑰要備份是為了
    // **線上那個資料庫**——金鑰沒了，資料還在卻永遠打不開，而它現在是平台自動產生
    // 的，所以他沒看過。
    renderPanel()

    const panel = screen.getByLabelText('下載備份')
    expect(panel).toHaveTextContent(/自給自足/)
    expect(panel).toHaveTextContent(/不需要/)
    expect(panel).toHaveTextContent(/SECRET_ENCRYPTION_KEY/)
    expect(panel).toHaveTextContent(/線上資料庫/)
  })

  it('warns that the passphrase is not recoverable before asking for one', () => {
    renderPanel()
    expect(screen.getByText(/不會存在系統裡/)).toBeInTheDocument()
  })

  it('will not download until the passphrase is confirmed', async () => {
    // A typo in a passphrase stored nowhere produces a file nobody can ever
    // open, and there is no way to find that out until it matters.
    await fill(GOOD, '')
    expect(screen.getByRole('button', { name: '下載備份' })).toBeDisabled()
  })

  it('says so when the two do not match', async () => {
    await fill(GOOD, 'something-else')
    expect(screen.getByText('兩次輸入不一樣')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '下載備份' })).toBeDisabled()
  })

  it('refuses a passphrase too short to be worth encrypting with', async () => {
    await fill('short', 'short')
    expect(screen.getByText(/至少 8 個字/)).toBeInTheDocument()
  })

  it('downloads once both boxes agree', async () => {
    const user = await fill(GOOD)
    await user.click(screen.getByRole('button', { name: '下載備份' }))

    await waitFor(() =>
      expect(downloadPost).toHaveBeenCalledWith(
        '/api/backup',
        { passphrase: GOOD },
        expect.stringContaining('trading-backup-'),
      ),
    )
  })

  it('clears the passphrase from the form once it has been used', async () => {
    const user = await fill(GOOD)
    await user.click(screen.getByRole('button', { name: '下載備份' }))

    await waitFor(() => expect(screen.getByLabelText('備份密碼')).toHaveValue(''))
  })

  it('says so when the download fails rather than looking like it worked', async () => {
    vi.mocked(downloadPost).mockRejectedValue(new Error('Service Unavailable'))
    const user = await fill(GOOD)
    await user.click(screen.getByRole('button', { name: '下載備份' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Service Unavailable')
  })
})

describe('automatic emailed backups', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.get).mockResolvedValue(SCHEDULE as never)
    vi.mocked(api.put).mockResolvedValue({ ...SCHEDULE, is_enabled: true } as never)
  })

  it('says the stored passphrase dies with the server', async () => {
    // Automating the encryption means the passphrase has to live on the
    // server; the owner has to know that before relying on it.
    renderPanel()
    expect(await screen.findByText(/整個伺服器沒了/)).toBeInTheDocument()
  })

  it('saves the schedule', async () => {
    const user = userEvent.setup()
    renderPanel()

    await user.click(await screen.findByLabelText('開啟自動備份'))
    await user.type(screen.getByLabelText('自動備份密碼'), GOOD)
    await user.click(screen.getByRole('button', { name: '儲存自動備份設定' }))

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        '/api/backup/schedule',
        expect.objectContaining({ is_enabled: true, passphrase: GOOD }),
      ),
    )
  })

  it('leaves the stored passphrase alone when it is not retyped', async () => {
    // Changing the interval must not force retyping a passphrase the owner
    // may not have to hand.
    vi.mocked(api.get).mockResolvedValue({
      ...SCHEDULE,
      is_enabled: true,
      has_passphrase: true,
    } as never)
    const user = userEvent.setup()
    renderPanel()

    const interval = await screen.findByLabelText('每隔幾天')
    await user.clear(interval)
    await user.type(interval, '30')
    await user.click(screen.getByRole('button', { name: '儲存自動備份設定' }))

    await waitFor(() => expect(api.put).toHaveBeenCalled())
    const payload = vi.mocked(api.put).mock.calls[0][1] as Record<string, unknown>
    expect('passphrase' in payload).toBe(false)
    expect(payload.interval_days).toBe(30)
  })

  it('shows why the last one did not arrive', async () => {
    // A backup silently not arriving is the failure this exists to prevent.
    vi.mocked(api.get).mockResolvedValue({
      ...SCHEDULE,
      is_enabled: true,
      has_passphrase: true,
      last_error: '找不到可用的 Email 通知管道',
    } as never)
    renderPanel()

    expect(await screen.findByText(/找不到可用的 Email 通知管道/)).toBeInTheDocument()
  })
})

// --- 還原 --------------------------------------------------------------------

/**
 * 備份檔一直都做得出來，但**倒回去的路只存在於文件裡**，而那條路的第一句話是「在你的
 * 電腦上跑 psql」——對這個產品的使用者等於流程到此結束。
 *
 * 這一段守的是三件事：那顆按鈕在、它說得出剛剛發生了什麼、而且它說得出**下一步**。
 * 最後一件最重要：還原進來的策略和通知管道是**停用**的（不然兩份一樣的策略同時在跑，
 * 同一件事通知兩次），而這件事沒有說出口的話，他會以為提醒已經在跑了。
 */
describe('BackupPanel：還原', () => {
  const REPORT = {
    strategies: 3,
    channels: 2,
    orders: 12,
    alerts: 4,
    positions: 1,
    positions_skipped: 2,
    watchlist: 0,
    watchlist_skipped: 5,
    risk_settings_created: false,
    expired_pending: 1,
  }

  async function restore(file = new File([new Uint8Array([1, 2, 3])], 'backup.bak')) {
    const user = userEvent.setup()
    renderPanel()
    await user.upload(screen.getByLabelText(/備份檔/), file)
    await user.type(screen.getByLabelText(/還原密碼/), GOOD)
    await user.click(screen.getByRole('button', { name: /還原/ }))
    return user
  }

  it('有一顆按得到的還原按鈕 —— 不是叫他去跑 psql', () => {
    renderPanel()

    expect(screen.getByRole('button', { name: /還原/ })).toBeInTheDocument()
  })

  it('先說清楚它只加不蓋，因為那是他按下去之前唯一想知道的事', () => {
    renderPanel()

    expect(screen.getByText(/不會刪|不會蓋|只會加/)).toBeInTheDocument()
  })

  it('把檔案和密碼一起送出去', async () => {
    vi.mocked(api.upload).mockResolvedValue(REPORT as never)

    await restore()

    await waitFor(() => expect(api.upload).toHaveBeenCalled())
    const [path, form] = vi.mocked(api.upload).mock.calls[0]
    expect(path).toBe('/api/backup/restore')
    expect((form as FormData).get('passphrase')).toBe(GOOD)
    expect((form as FormData).get('file')).toBeInstanceOf(File)
  })

  it('做完之後說出每一項的數字，不是只說「還原完成」', async () => {
    vi.mocked(api.upload).mockResolvedValue(REPORT as never)

    await restore()

    expect(await screen.findByText(/還原好了/)).toBeInTheDocument()
    expect(screen.getByText(/跳過了 2 筆持股/)).toBeInTheDocument()
  })

  it('而且說得出「它們是停用的，等你打開」', async () => {
    // 這是整段裡最重要的一句。沒有它，他會以為提醒已經在跑了——而那正是這個產品唯一
    // 不能失效的東西。
    vi.mocked(api.upload).mockResolvedValue(REPORT as never)

    await restore()

    expect(await screen.findByText(/加回來的策略和通知管道都是停用的/)).toBeInTheDocument()
    expect(screen.getByText(/把你要用的打開/)).toBeInTheDocument()
  })

  it('密碼太短就不要讓他按下去', async () => {
    const user = userEvent.setup()
    renderPanel()
    await user.upload(
      screen.getByLabelText(/備份檔/),
      new File([new Uint8Array([1])], 'backup.bak'),
    )
    await user.type(screen.getByLabelText(/還原密碼/), 'short')

    expect(screen.getByRole('button', { name: /還原/ })).toBeDisabled()
  })

  it('沒選檔案也不要讓他按下去', async () => {
    const user = userEvent.setup()
    renderPanel()
    await user.type(screen.getByLabelText(/還原密碼/), GOOD)

    expect(screen.getByRole('button', { name: /還原/ })).toBeDisabled()
  })

  it('失敗的時候把後端那句話原封不動印出來', async () => {
    // 「密碼不對」和「這個檔案不是備份」是兩件他改得動的事，而「還原失敗」對哪一件都
    // 沒有幫助。
    const { ApiError } = await import('../lib/api')
    vi.mocked(api.upload).mockRejectedValue(new ApiError(422, '密碼不對，或檔案已經損毀。'))

    await restore()

    expect(await screen.findByText(/密碼不對/)).toBeInTheDocument()
  })
})
