import { useEffect } from 'react'
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { Layout } from './components/Layout'
import { ProtectedRoute } from './components/ProtectedRoute'
import { LoginPage } from './pages/LoginPage'
import { DashboardPage } from './pages/DashboardPage'
import { StrategiesPage } from './pages/StrategiesPage'
import { OrdersPage } from './pages/OrdersPage'
import { PositionsPage } from './pages/PositionsPage'
import { NotificationsPage } from './pages/NotificationsPage'
import { RiskSettingsPage } from './pages/RiskSettingsPage'
import { BrokerSettingsPage } from './pages/BrokerSettingsPage'
import { AccountPage } from './pages/AccountPage'
import { WebhooksPage } from './pages/WebhooksPage'
import { BacktestPage } from './pages/BacktestPage'
import { SetupPage } from './pages/SetupPage'
import { SystemStatusPage } from './pages/SystemStatusPage'
import { setSetupRequiredHandler } from './lib/api'

/** Sends somebody to the setup page the moment the backend says it has not
 * been configured.
 *
 * Registered here rather than checked on load: an unconfigured deployment
 * answers 503 on EVERY real route, so whichever page they happened to open is
 * the one that finds out, and each of them would otherwise render its own
 * generic 「載入失敗」 over a problem that is neither theirs nor mysterious.
 *
 * api.ts owns the recognition (a 503 carrying `setup_required`, never a bare
 * one -- Render answers those during a cold start); this owns the navigation,
 * which keeps the router out of the fetch layer. */
function SetupRedirect() {
  const navigate = useNavigate()
  useEffect(() => {
    setSetupRequiredHandler(() => navigate('/setup', { replace: true }))
  }, [navigate])
  return null
}

function App() {
  return (
    <>
      <SetupRedirect />
      <Routes>
        {/* Outside ProtectedRoute on purpose: there is no account yet, and
            there cannot be one until the deployment is configured. Behind the
            guard it would bounce the only person who needs it to a login page
            that cannot work. */}
        <Route path="/setup" element={<SetupPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route element={<ProtectedRoute />}>
          <Route element={<Layout />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/strategies" element={<StrategiesPage />} />
            <Route path="/orders" element={<OrdersPage />} />
            <Route path="/positions" element={<PositionsPage />} />
            <Route path="/notifications" element={<NotificationsPage />} />
            <Route path="/risk-settings" element={<RiskSettingsPage />} />
            <Route path="/broker-settings" element={<BrokerSettingsPage />} />
            <Route path="/backtest" element={<BacktestPage />} />
            <Route path="/webhooks" element={<WebhooksPage />} />
            <Route path="/account" element={<AccountPage />} />
            <Route path="/system" element={<SystemStatusPage />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  )
}

export default App
