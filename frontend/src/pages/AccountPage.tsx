import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { ActionError } from '../components/ActionError'
import { api } from '../lib/api'
import { useAuth } from '../context/useAuth'
import type { Account } from '../lib/types'

const MIN_LENGTH = 8

/** The account's own page.
 *
 * There was none. The password guards the broker API keys, the notification
 * tokens and the ability to place orders, and changing it meant running a
 * script on the server -- which this owner is not going to do. There was also
 * no way to tell whether anyone else had ever signed in.
 */
export function AccountPage() {
  const { logout } = useAuth()
  const { data } = useQuery({
    queryKey: ['account'],
    queryFn: () => api.get<Account>('/api/auth/me'),
  })

  return (
    <div className="max-w-lg space-y-6">
      <h1 className="text-lg font-semibold">帳號</h1>
      {data && <Identity account={data} />}
      <ChangePassword onDone={logout} />
      <SignOutEverywhere onDone={logout} />
    </div>
  )
}

function Identity({ account }: { account: Account }) {
  return (
    <section aria-label="登入資訊" className="space-y-1 rounded border border-slate-800 p-4">
      <p className="text-sm">
        <span className="text-slate-500">帳號：</span>
        {account.email}
      </p>
      <p className="text-sm">
        <span className="text-slate-500">這次登入：</span>
        {account.last_login_at ? new Date(account.last_login_at).toLocaleString() : '—'}
      </p>
      {/* The one worth reading. If this is a time you were not at a computer,
          somebody else has the password. */}
      <p className="text-sm">
        <span className="text-slate-500">上一次登入：</span>
        {account.previous_login_at
          ? new Date(account.previous_login_at).toLocaleString()
          : '這是第一次登入'}
      </p>
      <p className="text-xs text-slate-500">
        如果「上一次登入」是你沒在用電腦的時間，代表有別人拿到了密碼——請馬上改密碼，
        改完所有已登入的裝置都會被踢出去。
      </p>
    </section>
  )
}

function ChangePassword({ onDone }: { onDone: () => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')

  const change = useMutation({
    mutationFn: () =>
      api.post('/api/auth/change-password', { current_password: current, new_password: next }),
    // Signed out on purpose: changing the password invalidates every token
    // including this one, so staying on the page would mean every subsequent
    // request 401ing with no explanation.
    onSuccess: onDone,
  })

  const tooShort = next.length > 0 && next.length < MIN_LENGTH
  const mismatch = again.length > 0 && again !== next
  const ready = current.length > 0 && next.length >= MIN_LENGTH && again === next

  return (
    <section aria-label="修改密碼" className="space-y-3 rounded border border-slate-800 p-4">
      <h2 className="text-sm font-semibold text-slate-300">修改密碼</h2>
      <p className="text-xs text-slate-500">
        改完之後，所有裝置上已經登入的狀態都會失效，包含這一台——你需要重新登入一次。
        這正是密碼外洩時該做的事。
      </p>

      <Field id="current-password" label="目前密碼" value={current} onChange={setCurrent} />
      <Field id="new-password" label="新密碼" value={next} onChange={setNext} />
      {tooShort && <p className="text-xs text-amber-300">至少 {MIN_LENGTH} 個字</p>}
      <Field id="new-password-again" label="再輸入一次" value={again} onChange={setAgain} />
      {mismatch && <p className="text-xs text-amber-300">兩次輸入不一樣</p>}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={!ready || change.isPending}
          onClick={() => change.mutate()}
          className="rounded bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          修改密碼
        </button>
        <ActionError error={change.error} />
      </div>
    </section>
  )
}

function SignOutEverywhere({ onDone }: { onDone: () => void }) {
  const signOut = useMutation({
    mutationFn: () => api.post('/api/auth/logout-everywhere'),
    onSuccess: onDone,
  })

  return (
    <section aria-label="登出所有裝置" className="space-y-2 rounded border border-slate-800 p-4">
      <h2 className="text-sm font-semibold text-slate-300">登出所有裝置</h2>
      <p className="text-xs text-slate-500">
        不改密碼，只讓目前所有已登入的裝置失效。在別人的電腦上登入過、忘記登出時用這個。
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={signOut.isPending}
          onClick={() => {
            if (window.confirm('確定要登出所有裝置嗎？包含你現在用的這一台。')) signOut.mutate()
          }}
          className="rounded bg-red-900 px-4 py-1.5 text-sm font-medium text-red-100 hover:bg-red-800 disabled:opacity-50"
        >
          全部登出
        </button>
        <ActionError error={signOut.error} />
      </div>
    </section>
  )
}

function Field({
  id,
  label,
  value,
  onChange,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <div>
      <label htmlFor={id} className="text-sm text-slate-400">
        {label}
      </label>
      <input
        id={id}
        type="password"
        autoComplete={id === 'current-password' ? 'current-password' : 'new-password'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
      />
    </div>
  )
}
