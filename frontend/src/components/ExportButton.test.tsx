import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ExportButton } from './ExportButton'
import { downloadFile } from '../lib/api'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  downloadFile: vi.fn(),
}))

describe('ExportButton', () => {
  beforeEach(() => vi.clearAllMocks())

  it('downloads the requested export', async () => {
    const user = userEvent.setup()
    render(<ExportButton resource="orders" label="匯出 CSV" />)

    await user.click(screen.getByRole('button', { name: '匯出 CSV' }))

    await waitFor(() =>
      expect(downloadFile).toHaveBeenCalledWith('/api/export/orders.csv', expect.any(String)),
    )
  })

  it('dates the file, so last year’s download is still identifiable', async () => {
    const user = userEvent.setup()
    render(<ExportButton resource="positions" label="匯出" />)

    await user.click(screen.getByRole('button', { name: '匯出' }))

    await waitFor(() => expect(downloadFile).toHaveBeenCalled())
    const filename = vi.mocked(downloadFile).mock.calls[0][1]
    expect(filename).toMatch(/^positions-\d{4}-\d{2}-\d{2}\.csv$/)
  })

  it('says so when the download fails instead of doing nothing visible', async () => {
    vi.mocked(downloadFile).mockRejectedValue(new Error('Service Unavailable'))
    const user = userEvent.setup()
    render(<ExportButton resource="orders" label="匯出" />)

    await user.click(screen.getByRole('button', { name: '匯出' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Service Unavailable')
  })
})
