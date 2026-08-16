import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../context/useAuth'

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/strategies', label: 'Strategies' },
  { to: '/orders', label: 'Orders' },
  { to: '/positions', label: 'Positions' },
  { to: '/notifications', label: 'Notifications' },
  { to: '/risk-settings', label: 'Risk Settings' },
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
          Log out
        </button>
      </header>
      <main className="p-6">
        <Outlet />
      </main>
    </div>
  )
}
