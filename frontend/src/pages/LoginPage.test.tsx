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

  it('那段話裡不可以有沒被渲染的 markdown', async () => {
    // 實際開瀏覽器看到的第一句話是：
    //   「你現在建立的是**第一個也是唯一一個**帳號」
    // 兩坨星號原樣印在畫面上。這是全新使用者看到的**第一個畫面的第一句話**，
    // 而它看起來像壞掉的樣板。JSX 不是 markdown，要粗體就用 <strong>。
    route([['registration-open', () => jsonResponse({ open: true })]])

    renderLoginPage()

    await screen.findByRole('button', { name: '建立帳號' })
    expect(document.body.textContent).not.toMatch(/\*\*/)
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

  it('已經有擁有者的時候，註冊那條路還在 —— 只是通往他自己那一份', async () => {
    // 使用者：「還是沒有註冊按鈕，我認為相當重要。」
    //
    // 這一份部署只有一個擁有者，所以在**這裡**註冊是不可能的。但一個誤闖進來的
    // 陌生人，看到一個他永遠登不進去的表單而沒有任何說明，只會以為壞了。而他要
    // 的東西是存在的：部署他自己那一份，自己的網址、自己的資料庫、自己的流量。
    //
    // 連回同一個正典 repo，不是各自拷貝：拷貝出去的那一份會停在拷貝的那一天，
    // 而這個 app 修過沙箱逃逸和跨帳號隔離——層層轉發之後最末端的人拿到的是少了
    // 那些修補的版本，而他不會知道。
    route([['registration-open', () => jsonResponse({ open: false })]])

    renderLoginPage()

    const link = await screen.findByRole('link', { name: /自己部署|怎麼部署|部署你自己/ })
    // **不可以直接跳到某一家的部署按鈕。** 第一版指的是 render.com/deploy，那等於
    // 把「不要綁死廠商」那條規則推翻——使用者最早提的三個需求之一就是它。這個
    // app 要的是三樣東西（能跑 Docker 的地方、一個 Postgres、放前端的地方），
    // 不是三個品牌，而選哪一家是他的決定，不是這顆按鈕的。
    const href = link.getAttribute('href') ?? ''
    expect(href).not.toContain('render.com/deploy')
    expect(href).not.toContain('vercel.com/new')
    expect(href).toContain('github.com/CoolAI-Studio/Stock-trading-app')
    // getAllBy：那句話裡有一個 <span> 把「私人部署」框起來，所以外層段落和它
    // 自己都會命中。要驗的是「有沒有說」，不是「說在哪一個標籤裡」。
    expect(screen.getAllByText(/私人部署|只有擁有者/).length).toBeGreaterThan(0)
  })

  it('而且要說得出「跑在自己的電腦上」也是一條路', async () => {
    // 使用者：「不是說有本機端跟自選雲端可以選，你這樣是強迫別人只能選擇 render
    // 這單一方案不是嗎？」——對的。一顆直接跳到某一家的按鈕，就是把選擇拿走。
    route([['registration-open', () => jsonResponse({ open: false })]])

    renderLoginPage()

    await screen.findByRole('link', { name: /自己部署|怎麼部署|部署你自己/ })
    expect(document.body.textContent).toMatch(/自己的電腦|自己的機器/)
    expect(document.body.textContent).toMatch(/哪一家|任何一家|不是.*品牌/)
  })

  it('那條路不會假裝能在這一份上註冊', async () => {
    // 按了會走進死路的按鈕，比沒有按鈕更糟——這一頁自己已經有一條測試在講同一
    // 件事（「有人搶先註冊的話說清楚，並且切回登入」）。
    route([['registration-open', () => jsonResponse({ open: false })]])

    renderLoginPage()

    await screen.findByRole('link', { name: /自己部署|怎麼部署|部署你自己/ })
    expect(screen.queryByRole('button', { name: '建立帳號' })).not.toBeInTheDocument()
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
