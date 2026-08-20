import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../context/useAuth'
import { useWebSocket } from '../lib/useWebSocket'
import { InstallPrompt } from './InstallPrompt'
import { PushSelfHeal } from './PushSelfHeal'
import { WorkerHealthBanner } from './WorkerHealthBanner'

const NAV_ITEMS = [
  { to: '/', label: '儀表板', end: true },
  { to: '/strategies', label: '策略' },
  { to: '/backtest', label: '回測' },
  { to: '/orders', label: '訂單' },
  { to: '/positions', label: '部位' },
  { to: '/notifications', label: '通知' },
  { to: '/webhooks', label: 'TradingView' },
  { to: '/account', label: '帳號' },
  { to: '/risk-settings', label: '風險設定' },
  { to: '/broker-settings', label: '券商設定' },
]

export function Layout() {
  const { logout } = useAuth()
  // Mounted here rather than on the dashboard so one connection covers every
  // signed-in page. It used to live on the dashboard alone, which meant the
  // Orders page -- the screen you actually sit on waiting to confirm an order
  // -- received no live updates at all, and window-focus refetching is
  // globally disabled, so nothing else filled the gap either.
  useWebSocket(true)

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="flex items-start justify-between gap-4 border-b border-slate-800 px-4 py-3 sm:items-center sm:px-6">
        {/* Wraps rather than scrolls. Ten items measure about 630px in one
            row and a phone is 390px, so the last few links -- and the 登出
            button after them -- were off the side of the screen with no way
            to reach them at all. A scrolling row would fit in one line but
            hides that there is more; wrapping costs a second line and hides
            nothing. */}
        <nav className="flex flex-wrap gap-x-4 gap-y-2">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `text-sm font-medium ${isActive ? 'text-emerald-400' : 'text-slate-400 hover:text-slate-200'}`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        {/* shrink-0 so a wrapping nav never squeezes this to nothing -- being
            unable to sign out is the one nav failure with a security edge. */}
        <button
          onClick={logout}
          className="shrink-0 text-sm text-slate-400 hover:text-slate-200"
        >
          登出
        </button>
      </header>
      {/* Directly under the nav, on every page: the failure it reports makes
          every other page's contents untrustworthy, so it cannot live on the
          dashboard alone. */}
      {/* Above the worker banner on purpose: if this phone cannot receive a
          push at all, knowing the worker is healthy is not the useful fact. */}
      <InstallPrompt />
      {/* iOS silently rotates push subscriptions and fires no event, so the
          only place to notice is here, on load. Silent unless it could not
          repair itself. */}
      <PushSelfHeal />
      <WorkerHealthBanner />
      <main className="p-4 sm:p-6">
        <Outlet />
      </main>
    </div>
  )
}
