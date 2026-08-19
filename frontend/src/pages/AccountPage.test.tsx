import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AccountPage } from './AccountPage'
import { api } from '../lib/api'
import type { Account } from '../lib/types'

const logout = vi.fn()

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn(), post: vi.fn() },
}))
vi.mock('../context/useAuth', () => ({ useAuth: () => ({ logout }) }))

const ACCOUNT: Account = {
  id: 1,
  email: 'me@example.com',
  is_active: true,
  timezone: 'Asia/Taipei',
  last_login_at: '2026-08-19T01:30:00Z',
  previous_login_at: '2026-08-18T01:30:00Z',
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <AccountPage />
    </QueryClientProvider>,
  )
}

describe('AccountPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.get).mockResolvedValue(ACCOUNT as never)
    vi.mocked(api.post).mockResolvedValue(undefined as never)
  })
  afterEach(() => vi.restoreAllMocks())

  it('shows the previous sign-in, which is the one worth reading', async () => {
    // "Last login" is the session you are sitting in. The one before it is
    // what tells you whether somebody else has been in.
    renderPage()
    const panel = await screen.findByLabelText('登入資訊')
    expect(panel).toHaveTextContent('上一次登入')
    expect(panel).toHaveTextContent(/如果.*沒在用電腦的時間/)
  })

  it('says the first login is the first login rather than showing a blank', async () => {
    vi.mocked(api.get).mockResolvedValue({ ...ACCOUNT, previous_login_at: null } as never)
    renderPage()
    expect(await screen.findByText('這是第一次登入')).toBeInTheDocument()
  })

  it('changes the password once both new fields agree', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.type(screen.getByLabelText('目前密碼'), 'old-password')
    await user.type(screen.getByLabelText('新密碼'), 'a-new-password')
    await user.type(screen.getByLabelText('再輸入一次'), 'a-new-password')
    await user.click(screen.getByRole('button', { name: '修改密碼' }))

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/auth/change-password', {
        current_password: 'old-password',
        new_password: 'a-new-password',
      }),
    )
  })

  it('signs the owner out afterwards, because their token just died', async () => {
    // Changing the password invalidates every token including this one, so
    // staying on the page would 401 every subsequent request with no
    // explanation.
    const user = userEvent.setup()
    renderPage()

    await user.type(screen.getByLabelText('目前密碼'), 'old-password')
    await user.type(screen.getByLabelText('新密碼'), 'a-new-password')
    await user.type(screen.getByLabelText('再輸入一次'), 'a-new-password')
    await user.click(screen.getByRole('button', { name: '修改密碼' }))

    await waitFor(() => expect(logout).toHaveBeenCalled())
  })

  it('will not submit mismatched new passwords', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.type(screen.getByLabelText('目前密碼'), 'old-password')
    await user.type(screen.getByLabelText('新密碼'), 'a-new-password')
    await user.type(screen.getByLabelText('再輸入一次'), 'different')

    expect(screen.getByText('兩次輸入不一樣')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '修改密碼' })).toBeDisabled()
  })

  it('says the wrong current password was rejected', async () => {
    const { ApiError } = await vi.importActual<typeof import('../lib/api')>('../lib/api')
    vi.mocked(api.post).mockRejectedValue(new ApiError(401, 'Current password is incorrect'))
    const user = userEvent.setup()
    renderPage()

    await user.type(screen.getByLabelText('目前密碼'), 'wrong')
    await user.type(screen.getByLabelText('新密碼'), 'a-new-password')
    await user.type(screen.getByLabelText('再輸入一次'), 'a-new-password')
    await user.click(screen.getByRole('button', { name: '修改密碼' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Current password is incorrect')
  })

  it('asks before signing every device out, including this one', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '全部登出' }))

    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('你現在用的這一台'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/auth/logout-everywhere'))
  })

  it('does nothing if that prompt is dismissed', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const user = userEvent.setup()
    renderPage()

    await user.click(screen.getByRole('button', { name: '全部登出' }))
    expect(api.post).not.toHaveBeenCalled()
  })
})
