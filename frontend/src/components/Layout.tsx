import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../context/useAuth'

const NAV_ITEMS = [
  { to: '/', label: '儀表板', end: true },
  { to: '/strategies', label: '策略' },
  { to: '/orders', label: '訂單' },
  { to: '/positions', label: '部位' },
  { to: '/notifications', label: '通知' },
  { to: '/risk-settings', label: '風險設定' },
  { to: '/broker-settings', label: '券商設定' },
]

export function Layout() {
  const { logout } = useAuth()

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="flex items-center justify-between border-b border-slate-800 px-6 py-3">
        <nav className="flex gap-4">
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
        <button onClick={logout} className="text-sm text-slate-400 hover:text-slate-200">
          登出
        </button>
      </header>
      <main className="p-6">
        <Outlet />
      </main>
    </div>
  )
}
