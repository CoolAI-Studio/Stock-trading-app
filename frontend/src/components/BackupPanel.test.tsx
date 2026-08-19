import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BackupPanel } from './BackupPanel'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api, downloadPost } from '../lib/api'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  downloadPost: vi.fn(),
  api: { get: vi.fn(), put: vi.fn() },
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
