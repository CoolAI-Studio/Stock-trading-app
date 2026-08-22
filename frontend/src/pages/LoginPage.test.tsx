import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LoginPage } from './LoginPage'
import { AuthProvider } from '../context/AuthContext'
import { setToken } from '../lib/api'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function renderLoginPage() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<div>home page</div>} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  )
}

describe('LoginPage', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    setToken(null)
  })

  it('navigates to / after a successful login', async () => {
    // A FRESH Response per call, not one instance reused: a body can only be
    // read once, and this page now asks whether the deployment has an owner
    // before anybody types anything. Sharing one Response made the login
    // request read a body the first request had already consumed.
    vi.mocked(fetch).mockImplementation(() =>
      Promise.resolve(jsonResponse({ access_token: 'tok' })),
    )
    const user = userEvent.setup()
    renderLoginPage()

    await user.type(screen.getByLabelText('電子信箱'), 'me@example.com')
    await user.type(screen.getByLabelText('密碼'), 'hunter2')
    await user.click(screen.getByRole('button', { name: '登入' }))

    await waitFor(() => expect(screen.getByText('home page')).toBeInTheDocument())
  })

  it('shows an error message on invalid credentials', async () => {
    vi.mocked(fetch).mockImplementation(() =>
      Promise.resolve(jsonResponse({ detail: 'Incorrect email or password' }, 401)),
    )
    const user = userEvent.setup()
    renderLoginPage()

    await user.type(screen.getByLabelText('電子信箱'), 'me@example.com')
    await user.type(screen.getByLabelText('密碼'), 'wrong')
    await user.click(screen.getByRole('button', { name: '登入' }))

    expect(await screen.findByText('Incorrect email or password')).toBeInTheDocument()
  })
})

/**
 * 安裝的最後一步，必須在這一頁做完。
 *
 * WHAT THIS FIXES. A person deploys their own copy, fills in every blank the
 * setup page asks for, presses the buttons that generate the keys -- and then
 * arrives at a login form for an account that does not exist. Nothing in this
 * frontend ever called /api/auth/register; the setup page pointed at
 * DEPLOYMENT.md, which said to flip an environment variable and use curl.
 * CLAUDE.md: 任何「請在你的電腦上跑這支腳本」的指示，對這個使用者等於流程到此結束。
 *
 * The backend has always allowed the first account from the web
 * (ALLOW_FIRST_ACCOUNT, on by default). Only the screen was missing.
 */
describe('LoginPage：這個部署還沒有擁有者的時候', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    setToken(null)
  })

  function route(handlers: Array<[string, () => Response]>) {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      for (const [fragment, make] of handlers) {
        if (url.includes(fragment)) return Promise.resolve(make())
      }
      return Promise.resolve(jsonResponse({ detail: 'unrouted' }, 404))
    })
  }

  it('請他建立帳號，而不是要他登入一個還不存在的帳號', async () => {
    route([['registration-open', () => jsonResponse({ open: true })]])

    renderLoginPage()

    expect(await screen.findByRole('button', { name: '建立帳號' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '登入' })).not.toBeInTheDocument()
  })

  it('建立完直接進去，不用再登入一次', async () => {
    route([
      ['registration-open', () => jsonResponse({ open: true })],
      ['auth/register', () => jsonResponse({ id: 1, email: 'me@example.com' }, 201)],
      ['auth/login', () => jsonResponse({ access_token: 'tok' })],
    ])
    const user = userEvent.setup()
    renderLoginPage()

    await user.type(await screen.findByLabelText('電子信箱'), 'me@example.com')
    await user.type(screen.getByLabelText('密碼'), 'correct-horse-battery')
    await user.type(screen.getByLabelText('再輸入一次密碼'), 'correct-horse-battery')
    await user.click(screen.getByRole('button', { name: '建立帳號' }))

    await waitFor(() => expect(screen.getByText('home page')).toBeInTheDocument())
  })

  it('兩次密碼不一樣就不送出，因為打錯的密碼會鎖住整個部署', async () => {
    route([['registration-open', () => jsonResponse({ open: true })]])
    const user = userEvent.setup()
    renderLoginPage()

    await user.type(await screen.findByLabelText('電子信箱'), 'me@example.com')
    await user.type(screen.getByLabelText('密碼'), 'correct-horse-battery')
    await user.type(screen.getByLabelText('再輸入一次密碼'), 'correct-horse-batteru')
    await user.click(screen.getByRole('button', { name: '建立帳號' }))

    expect(await screen.findByText(/兩次輸入的密碼不一樣/)).toBeInTheDocument()
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('register'))).toBe(
      false,
    )
  })

  it('有人搶先註冊的話說清楚，並且切回登入', async () => {
    route([
      ['registration-open', () => jsonResponse({ open: true })],
      ['auth/register', () => jsonResponse({ detail: '這個部署已經有擁有者了。' }, 403)],
    ])
    const user = userEvent.setup()
    renderLoginPage()

    await user.type(await screen.findByLabelText('電子信箱'), 'me@example.com')
    await user.type(screen.getByLabelText('密碼'), 'correct-horse-battery')
    await user.type(screen.getByLabelText('再輸入一次密碼'), 'correct-horse-battery')
    await user.click(screen.getByRole('button', { name: '建立帳號' }))

    expect(await screen.findByText(/這個部署已經有擁有者了。/)).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '登入' })).toBeInTheDocument()
  })

  it('已經有擁有者的時候，不給任何建立帳號的入口', async () => {
    route([['registration-open', () => jsonResponse({ open: false })]])

    renderLoginPage()

    expect(await screen.findByRole('button', { name: '登入' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '建立帳號' })).not.toBeInTheDocument()
  })

  it('問不到後端的時候還是給得出登入表單，不會卡住', async () => {
    vi.mocked(fetch).mockRejectedValue(new Error('network down'))

    renderLoginPage()

    expect(await screen.findByRole('button', { name: '登入' })).toBeInTheDocument()
  })
})
