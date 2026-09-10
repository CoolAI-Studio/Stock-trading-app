import { useQuery } from '@tanstack/react-query'
import { Link, useLocation } from 'react-router-dom'
import { api } from '../lib/api'
import type { Position, Strategy } from '../lib/types'

// 這兩頁的全部工作就是帶他建第一支策略。OnboardingGate 會把剛建好帳號的人直接導去
// /welcome，所以在這裡還掛著這句話，等於他的第一個畫面上有一條黃色警告外加一個把他
// **帶離**引導的連結——一個跟引導搶人的第二個行動呼籲。
const PAGES_THAT_ARE_THE_FIX = new Set(['/welcome', '/guide'])

/**
 * 說出「這份部署現在沒有在盯任何東西」。
 *
 * 這是 NoChannelBanner 那一句話的另一半。那邊是「提醒沒有地方可以送」，這邊是「沒有
 * 東西會產生提醒」——後果一樣，而且都不會有任何東西變紅。
 *
 * 盯盤迴圈要看的清單是**啟用中的策略**加上**有部位的持股**（後端
 * `market_loop._watched_symbols`；自選清單不算，它只是給畫面看的）。兩者都空的時候，
 * 那條迴圈每一輪都無事可做。
 *
 * ＊ 為什麼需要一句話，而不是靠儀表板上那個「0」。
 *
 * 量出來的（維護者自己那一份，2026-09-06 到 09-08 連續 33.6 小時）：累計 SQL 12.2
 * 句/小時，也就是每 30 分鐘一輪、每輪 6 句——跨過整個台股盤中和美股夜盤，迴圈一次都
 * 沒走過「開盤」那條快路。而同一段時間裡 `/healthz` 每一格都是 ok、worker 心跳正常、
 * 資料庫漂亮地睡著。
 *
 * 一個數字 0 不會讓人停下來，一句「不會有任何提醒」會。這個 repo 已經在 NoChannelBanner
 * 上做過同一個判斷。
 *
 * ＊ 只在「確定沒有」的時候講。
 *
 * 載入中閃一下、或後端掛掉時多喊一次，都是把這句話變成背景雜訊——而後端掛掉本來就有
 * WorkerHealthBanner 在講。一句被學會忽略的警告，跟沒有那句話是同一件事。
 */
export function NothingWatchedBanner() {
  const { pathname } = useLocation()
  const strategies = useQuery({
    queryKey: ['strategies'],
    queryFn: () => api.get<Strategy[]>('/api/strategies'),
    retry: false,
  })
  const positions = useQuery({
    queryKey: ['positions'],
    queryFn: () => api.get<Position[]>('/api/positions'),
    retry: false,
  })

  if (PAGES_THAT_ARE_THE_FIX.has(pathname)) return null
  if (!strategies.isSuccess || !positions.isSuccess) return null
  if ((strategies.data ?? []).some((one) => one.is_active)) return null
  // 數量 0 是一筆平掉的紀錄，不是還在看的部位——停損掃描也是這樣分的。
  if ((positions.data ?? []).some((one) => Number(one.quantity) !== 0)) return null

  return (
    <div
      role="alert"
      className="rounded border border-amber-700 bg-amber-950/40 px-3 py-2 text-sm text-amber-200"
    >
      你沒有任何啟用中的策略，也沒有持股，
      <strong>所以盯盤現在沒有東西要看，不會有任何提醒送出</strong>
      ——服務是正常的，只是還沒有東西讓它盯。
      <Link to="/strategies" className="ml-1 underline hover:text-amber-100">
        去建立或啟用一支策略
      </Link>
    </div>
  )
}
