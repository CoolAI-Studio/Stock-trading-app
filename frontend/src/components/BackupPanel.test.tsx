import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BackupPanel } from './BackupPanel'
import { downloadPost } from '../lib/api'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  downloadPost: vi.fn(),
}))

const GOOD = 'a-long-enough-passphrase'

async function fill(passphrase: string, confirmation = passphrase) {
  const user = userEvent.setup()
  render(<BackupPanel />)
  await user.type(screen.getByLabelText('備份密碼'), passphrase)
  if (confirmation) await user.type(screen.getByLabelText('再輸入一次'), confirmation)
  return user
}

describe('BackupPanel', () => {
  beforeEach(() => vi.clearAllMocks())

  it('warns that the passphrase is not recoverable before asking for one', () => {
    render(<BackupPanel />)
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
