import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import type { NotificationChannel } from '../lib/types'

/**
 * 說出「你的提醒現在沒有地方可以送」。
 *
 * 一個沒有出口的提醒系統，跟一個沒有在跑的提醒系統，後果一模一樣——而它更容易
 * 發生，因為每一個畫面都看起來正常：策略是啟用中的、worker 是健康的、/healthz
 * 全綠、儀表板上有三支策略在跑。只是條件成立的時候沒有人會知道。
 *
 * 後端事後分得出這種情況（NotificationLog 的 channel_id 是 NULL 就代表這一則
 * 誰都沒送到，dispatcher._record_reaching_nobody），但事後才知道，對一個提醒
 * 產品來說已經太晚了。這是事前的那一句話。
 *
 * 跟 WorkerHealthBanner 一起掛在 Layout：同一種失效，就該在同一個位置、每一頁
 * 都看得見，而不是只在使用者剛好打開通知頁的時候。
 */
export function NoChannelBanner() {
  const { data, isSuccess } = useQuery({
    queryKey: ['notification-channels'],
    queryFn: () => api.get<NotificationChannel[]>('/api/notifications/channels'),
    retry: false,
  })

  // 只在「確定沒有」的時候講。載入中閃一下、或後端掛掉時多喊一次，都是把
  // 這一句話變成背景雜訊——而後端掛掉本來就有 WorkerHealthBanner 在講。
  if (!isSuccess) return null
  if ((data ?? []).some((channel) => channel.is_enabled)) return null

  return (
    <div
      role="alert"
      className="rounded border border-amber-700 bg-amber-950/40 px-3 py-2 text-sm text-amber-200"
    >
      你還沒有可以收通知的地方，
      <strong>所以就算條件成立，也不會有任何提醒送到你手上</strong>
      ——策略照樣在跑，只是沒有人會知道結果。
      <Link to="/notifications" className="ml-1 underline hover:text-amber-100">
        設定通知管道
      </Link>
      （Email、Telegram 或瀏覽器推播，選一個就夠了）。
    </div>
  )
}
