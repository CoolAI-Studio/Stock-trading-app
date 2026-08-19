import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, SESSION_EXPIRED_KEY } from '../lib/api'
import { useAuth } from '../context/useAuth'

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  // Read once, on first render: arriving here because a token expired is a
  // different situation from arriving here on purpose, and the difference is
  // the whole reason to say anything.
  const [expired] = useState(() => {
    const flag = sessionStorage.getItem(SESSION_EXPIRED_KEY) !== null
    sessionStorage.removeItem(SESSION_EXPIRED_KEY)
    return flag
  })

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(email, password)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '登入失敗')
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
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
          />
        </div>

        {expired && !error && (
          <p className="rounded border border-amber-700 bg-amber-950/40 px-3 py-2 text-sm text-amber-200">
            登入時效到了，請重新登入。你的資料都還在，背景盯盤與通知也沒有停。
          </p>
        )}
        {error && <p className="text-sm text-red-400">{error}</p>}

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded bg-emerald-600 py-2 font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          {submitting ? '登入中…' : '登入'}
        </button>
      </form>
    </div>
  )
}
