import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { setToken } from './lib/api'
import { AuthProvider } from './context/AuthContext'

/**
 * The routing decisions that decide whether a brand-new deployment is usable.
 *
 * Somebody who has just clicked 「Deploy to Render」 and left a blank empty
 * lands on the frontend with a backend that answers 503 on every real route.
 * Without somewhere to send them, every page renders its own generic
 * 「載入失敗」 and nothing on screen says what is actually wrong.
 *
 * `fetch` is stubbed rather than `api`, deliberately: the redirect is produced
 * by api.ts recognising the 503 and calling the handler App registered, and
 * mocking `api` away would leave that whole chain untested while the test
 * still passed.
 */

const SETUP_STATUS = {
  missing: [
    {
      name: 'SECRET_ENCRYPTION_KEY',
      why: '沒有它，通知設定存不了。',
      how: '按產生。',
      generator: 'fernet',
    },
  ],
  where: 'Render → Environment',
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** A backend in setup mode: 503 with the flag on everything real, and the
 * setup endpoint answering normally. */
function unconfiguredBackend() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) =>
      String(url).includes('/api/setup/status')
        ? jsonResponse(SETUP_STATUS)
        : jsonResponse({ detail: '還沒設定完成', setup_required: true }, 503),
    ),
  )
}

function show(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <MemoryRouter initialEntries={[path]}>
          <App />
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  localStorage.clear()
  setToken(null)
})

afterEach(() => vi.unstubAllGlobals())

describe('還沒設定完的部署', () => {
  it('/setup 不需要登入就打得開', async () => {
    // There is no account yet and there cannot be one until the deployment is
    // configured. Putting this behind ProtectedRoute would bounce the only
    // person who needs it to a login page that cannot work.
    unconfiguredBackend()
    show('/setup')

    expect(await screen.findByText(/完成你的部署設定/)).toBeInTheDocument()
  })

  it('後端回「還沒設定完成」就自動把人帶到設定頁', async () => {
    unconfiguredBackend()
    setToken('a-token')

    show('/positions')

    expect(await screen.findByText(/完成你的部署設定/)).toBeInTheDocument()
  })

  it('連登入頁也會被帶去設定頁 —— 那是還沒有帳號的人唯一會停在的地方', async () => {
    // It used to be that this could only assert 「the route is reachable」:
    // the login page made no request until somebody pressed a button, so an
    // unconfigured deployment showed a login form for an account that could
    // not exist, and nothing pointed at the setup page.
    //
    // It now asks whether the deployment has an owner yet (it has to, to know
    // whether to offer 「建立帳號」), and that request carries the same
    // setup_required flag as every other one -- so the redirect happens on
    // the one page a brand-new deployment actually lands on.
    unconfiguredBackend()

    show('/login')

    expect(await screen.findByText(/完成你的部署設定/)).toBeInTheDocument()
  })
})

describe('設定完成的部署', () => {
  it('沒登入就去登入頁，不會被設定頁攔截', async () => {
    // Setup mode must not swallow the ordinary case.
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'unauthorized' }, 401)))

    show('/positions')

    await waitFor(() => expect(screen.queryByText(/完成你的部署設定/)).not.toBeInTheDocument())
  })

  it('一般的 503 不會被當成「還沒設定」', async () => {
    // Render answers a plain 503 during a cold start on the free tier. Sending
    // somebody to a setup page over that would tell them their working
    // deployment is unconfigured.
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'upstream busy' }, 503)))
    setToken('a-token')

    show('/positions')

    await waitFor(() => expect(screen.queryByText(/完成你的部署設定/)).not.toBeInTheDocument())
  })
})
