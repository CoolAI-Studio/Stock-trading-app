import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ApiError, SESSION_EXPIRED_KEY } from '../lib/api'
import { useAuth } from '../context/useAuth'
import { PROJECT_URL } from '../lib/project'

/**
 * 登入，或者——如果這個部署還沒有擁有者——建立那個帳號。
 *
 * WHY THIS PAGE HAS TWO SHAPES. Somebody deploys their own copy, fills in
 * every blank the setup page asks for, presses the buttons that generate the
 * keys, and then arrives here: a login form for an account that does not
 * exist. Nothing in this frontend ever called /api/auth/register. The setup
 * page pointed at DEPLOYMENT.md, which said to switch an environment variable
 * on, create the account with curl, and switch it back off -- and CLAUDE.md is
 * explicit that for this audience 「請在你的電腦上跑這支腳本」 is where the
 * process ends.
 *
 * The backend has always permitted the first account from a browser
 * (ALLOW_FIRST_ACCOUNT, on by default, and registration closes itself
 * afterwards as a fact about the database). Only the screen was missing.
 */
export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  // null while the answer is unknown. Rendering the login form during that
  // moment and swapping it for the other one is a flicker on the first screen
  // a new deployment ever shows, so nothing decisive is drawn until it lands.
  const [waitingForOwner, setWaitingForOwner] = useState<boolean | null>(null)
  // Read once, on first render: arriving here because a token expired is a
  // different situation from arriving here on purpose, and the difference is
  // the whole reason to say anything.
  const [expired] = useState(() => {
    const flag = sessionStorage.getItem(SESSION_EXPIRED_KEY) !== null
    sessionStorage.removeItem(SESSION_EXPIRED_KEY)
    return flag
  })

  useEffect(() => {
    let alive = true
    api
      .get<{ open: boolean }>('/api/auth/registration-open')
      .then((answer) => {
        if (alive) setWaitingForOwner(answer.open === true)
      })
      .catch(() => {
        // Unreachable backend, an older deployment that has no such endpoint,
        // anything. Fall back to the login form: it is the right screen for
        // every deployment that already has an owner, which is all of them
        // after the first five minutes.
        if (alive) setWaitingForOwner(false)
      })
    return () => {
      alive = false
    }
  }, [])

  async function handleLogin(): Promise<void> {
    await login(email, password)
    navigate('/', { replace: true })
  }

  async function handleCreate(): Promise<void> {
    if (password !== confirmation) {
      // Checked here rather than at the backend, because a typo in THIS
      // password is not a login failure -- it is a deployment nobody can get
      // into. There is no password reset: the account is the only one.
      setError('兩次輸入的密碼不一樣，請再確認一次。')
      return
    }
    try {
      await api.post('/api/auth/register', { email, password })
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        // Somebody claimed it first -- or this tab was open while the account
        // was made in another. Either way the login form is now the correct
        // screen, and leaving the create form up would loop them.
        setWaitingForOwner(false)
        setError(err.message)
        return
      }
      throw err
    }
    await handleLogin()
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      if (waitingForOwner) {
        await handleCreate()
      } else {
        await handleLogin()
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : waitingForOwner ? '建立失敗' : '登入失敗')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm space-y-4 rounded-lg border border-slate-800 bg-slate-900 p-8"
      >
        <h1 className="text-xl font-semibold text-slate-100">交易儀表板</h1>

        {waitingForOwner && (
          <p className="rounded border border-emerald-800 bg-emerald-950/40 px-3 py-2 text-sm text-emerald-200">
            這個部署還沒有擁有者。你現在建立的是
            <strong className="font-semibold">第一個也是唯一一個</strong>
            帳號——建立完成之後註冊就會自己關起來，別人打不進來。
            密碼請記牢，這裡沒有「忘記密碼」。
          </p>
        )}

        <div className="space-y-1">
          <label htmlFor="email" className="text-sm text-slate-400">
            電子信箱
          </label>
          <input
            id="email"
            type="email"
            required
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="password" className="text-sm text-slate-400">
            密碼
          </label>
          <input
            id="password"
            type="password"
            required
            autoComplete={waitingForOwner ? 'new-password' : 'current-password'}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
          />
        </div>

        {waitingForOwner && (
          <div className="space-y-1">
            <label htmlFor="confirmation" className="text-sm text-slate-400">
              再輸入一次密碼
            </label>
            <input
              id="confirmation"
              type="password"
              required
              autoComplete="new-password"
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
              className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
            />
          </div>
        )}

        {expired && !error && (
          <p className="rounded border border-amber-700 bg-amber-950/40 px-3 py-2 text-sm text-amber-200">
            登入時效到了，請重新登入。你的資料都還在，背景盯盤與通知也沒有停。
          </p>
        )}
        {error && <p className="text-sm text-red-400">{error}</p>}

        {waitingForOwner === null ? (
          <p className="text-sm text-slate-500">檢查這個部署的狀態…</p>
        ) : (
          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded bg-emerald-600 py-2 font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            {waitingForOwner
              ? submitting
                ? '建立中…'
                : '建立帳號'
              : submitting
                ? '登入中…'
                : '登入'}
          </button>
        )}
      </form>

      {/* 註冊那條路，在每一份部署上都在——只是這一份已經有擁有者了，所以它通往
          的是「部署你自己那一份」。

          不給任何入口，一個誤闖進來的陌生人只會看到一個他永遠登不進去的表單，
          然後以為壞了。而給一顆假裝能在這裡註冊的按鈕更糟：按了會走進死路的按
          鈕，比沒有按鈕還差——這一頁自己已經為了同一條理由做過一次決定。 */}
      {waitingForOwner === false && (
        <div className="mt-4 w-full max-w-sm rounded border border-slate-800 bg-slate-900/60 p-4 text-sm text-slate-400">
          <p>
            這是一份<span className="text-slate-200">私人部署</span>
            ，只有擁有者登得進來。註冊在這裡是關著的。
          </p>
          <p className="mt-2">
            想自己用一份的話，部署你自己的——
            <span className="text-slate-200">自己的網址、自己的資料庫、自己的通知</span>
            ，跟這一份完全無關，也不會用到別人的額度。
          </p>
          {/* 不指向某一家的部署按鈕。它要的是三樣東西，不是三個品牌——而選哪一
              家（或哪一家都不選）是他的決定。清單只有 README 那一份，這裡不抄。 */}
          <p className="mt-2">
            它要的是三樣東西，不是三個品牌：一個能跑 Docker 的地方、一個 Postgres、
            一個放前端的地方。
            <span className="text-slate-200">哪一家都可以，也可以整份跑在自己的電腦上。</span>
          </p>
          <a
            href={PROJECT_URL}
            target="_blank"
            rel="noreferrer"
            className="mt-3 block rounded bg-sky-700 px-3 py-2 text-center font-medium text-white hover:bg-sky-600"
          >
            看怎麼自己部署一份
          </a>
        </div>
      )}
    </div>
  )
}
