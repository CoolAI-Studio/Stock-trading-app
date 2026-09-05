import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'

type CheckStatus = 'ok' | 'fail' | 'starting' | 'disabled'

interface HealthCheck {
  status: CheckStatus
  last_loop_age_sec?: number
  last_poll_age_sec?: number
}

export interface Health {
  status: CheckStatus
  checks: Record<string, HealthCheck>
}

/** Says out loud when the background engine has stopped.
 *
 * This product's job is to warn the owner, so the one failure it cannot
 * afford is warning nobody. And every way that happens is silent: the free
 * tier spins the service down, the data provider blocks the IP, a tick wedges.
 * On screen all three look identical to a quiet market -- the strategy list
 * still says 啟用中, the dashboard still counts three active strategies, and
 * the orders page is simply empty.
 *
 * /healthz has run three real checks and returned 503 since day one. Nothing
 * in the app had ever asked it. This is that one request, in the layout so it
 * covers every page rather than only the dashboard.
 */
export function WorkerHealthBanner() {
  const { data, isError } = useQuery({
    queryKey: ['healthz'],
    queryFn: () => api.get<Health>('/healthz'),
    // Roughly the market poll interval. Cheap by design: this URL carries no
    // ?deep=1, and without it the endpoint does not touch the database at all
    // -- just in-memory timestamps. That matters at 30s: the free-tier
    // database sleeps after 5 minutes idle, and a query here would pin it
    // awake for as long as any tab is open, which is how a month's compute
    // allowance disappears by mid-month (backend health._database_answer).
    refetchInterval: 30_000,
    retry: false,
  })

  // A failed request is itself the outage: the backend did not answer at all.
  if (isError) {
    return (
      <Banner tone="fail">
        連不上後端服務。策略沒有在跑，價格提醒也不會送出——請確認伺服器狀態。
      </Banner>
    )
  }
  if (!data) return null

  const worker = data.checks.worker
  const market = data.checks.market_data

  if (worker?.status === 'starting' || market?.status === 'starting') {
    return <Banner tone="warn">背景引擎剛啟動，正在暖機。這段期間還不會產生訊號。</Banner>
  }
  if (worker?.status === 'fail') {
    return (
      <Banner tone="fail">
        背景引擎已停止{ageNote(worker.last_loop_age_sec)}。策略不會執行、停損不會檢查、提醒不會送出。
      </Banner>
    )
  }
  if (market?.status === 'fail') {
    return (
      <Banner tone="fail">
        行情已經抓不到{ageNote(market.last_poll_age_sec)}。畫面上的價格是舊的，停損也是拿舊價在比對。
      </Banner>
    )
  }
  // Healthy is the normal case and says nothing: a banner that is always
  // there stops being read.
  return null
}

function ageNote(seconds: number | undefined): string {
  if (seconds === undefined) return ''
  if (seconds < 120) return `（已 ${Math.round(seconds)} 秒沒有動作）`
  return `（已 ${Math.round(seconds / 60)} 分鐘沒有動作）`
}

function Banner({ tone, children }: { tone: 'fail' | 'warn'; children: React.ReactNode }) {
  const skin =
    tone === 'fail'
      ? 'border-red-800 bg-red-950/60 text-red-200'
      : 'border-amber-700 bg-amber-950/50 text-amber-200'
  return (
    <div role="alert" className={`border-b px-6 py-2 text-sm ${skin}`}>
      {children}
    </div>
  )
}
