import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DeleteButton } from './DeleteButton'
import { ApiError } from '../lib/api'

afterEach(() => vi.restoreAllMocks())

describe('DeleteButton', () => {
  it('asks before deleting, and names what it is about to delete', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const onConfirm = vi.fn()
    const user = userEvent.setup()
    render(<DeleteButton what="這筆回測" onConfirm={onConfirm} />)

    await user.click(screen.getByRole('button', { name: '刪除' }))

    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('這筆回測'))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('does nothing when the prompt is dismissed', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const onConfirm = vi.fn()
    const user = userEvent.setup()
    render(<DeleteButton what="這筆回測" onConfirm={onConfirm} />)

    await user.click(screen.getByRole('button', { name: '刪除' }))

    expect(onConfirm).not.toHaveBeenCalled()
  })

  it("shows the backend's reason when a delete is refused", () => {
    // Some refusals are deliberate -- a confirmed order moved a position --
    // and the explanation is the only thing that tells the owner what to do
    // instead.
    render(
      <DeleteButton
        what="這筆訂單"
        onConfirm={vi.fn()}
        error={new ApiError(409, '這筆訂單已經成交、動到了持倉')}
      />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('動到了持倉')
  })

  it('cannot be pressed twice while the first delete is in flight', () => {
    render(<DeleteButton what="這筆回測" onConfirm={vi.fn()} pending />)
    expect(screen.getByRole('button', { name: '刪除中…' })).toBeDisabled()
  })
})
