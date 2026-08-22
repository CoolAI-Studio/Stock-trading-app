import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { api } from '../lib/api'
import { hasSeenOnboarding } from '../lib/onboarding'
import type { NotificationChannel, Strategy, WatchlistItem } from '../lib/types'

/**
 * 剛建好帳號的人，第一個畫面應該是引導，不是一個空的儀表板。
 *
 * 判斷「還沒開始用」的方式跟 ONBOARDING.md 寫的一樣：沒有策略、沒有自選股、
 * 沒有通知管道。三個都空才算——只要他已經做過其中任何一件事，就不要再打擾他。
 *
 * TWO WAYS THIS COULD GO WRONG, and both are guarded:
 *
 *   把人關在迴圈裡。他按下「我知道自己在做什麼」離開引導，帳號還是空的，於是
 *   又被導回去。所以離開過就記下來（lib/onboarding.ts），記號存在就不再攔截。
 *
 *   在還不知道答案的時候就跳。查詢還沒回來、或後端根本沒回應的時候，一律先給
 *   儀表板：把一個正在用的人丟進引導，比晚一秒鐘看到引導糟糕得多。
 */
export function OnboardingGate({ children }: { children: ReactNode }) {
  const strategies = useQuery({
    queryKey: ['strategies'],
    queryFn: () => api.get<Strategy[]>('/api/strategies'),
    retry: false,
  })
  const watchlist = useQuery({
    queryKey: ['watchlist'],
    queryFn: () => api.get<WatchlistItem[]>('/api/watchlist'),
    retry: false,
  })
  const channels = useQuery({
    queryKey: ['notification-channels'],
    queryFn: () => api.get<NotificationChannel[]>('/api/notifications/channels'),
    retry: false,
  })

  const answered = strategies.isSuccess && watchlist.isSuccess && channels.isSuccess
  const empty =
    (strategies.data?.length ?? 0) === 0 &&
    (watchlist.data?.length ?? 0) === 0 &&
    (channels.data?.length ?? 0) === 0

  if (answered && empty && !hasSeenOnboarding()) {
    return <Navigate to="/welcome" replace />
  }
  return <>{children}</>
}
