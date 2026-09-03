/**
 * 右下角那一格：你這一份骨架是哪一版。
 *
 * ＊ 為什麼要一直在畫面上。
 *
 * 版本資訊本來只在系統狀態頁，而那是他不會主動打開的地方——他打開它的時候，通常是
 * 因為已經出事了。
 *
 * 而這個更新模型（骨架由上游修、使用者自己管他加的東西）成立的前提，是他**隨時知道
 * 自己在哪一版**。那件事只有一直看得到才算數。
 *
 * ＊ 但它不可以吵。
 *
 * 沒事的時候就是角落一行灰字。一個平常就在閃的東西，會讓他在真的該看的那一次也不
 * 看——跟系統狀態頁「已經是最新就什麼都不說」是同一條規則的另一面。
 *
 * ＊ 「不知道」不可以畫成「已經是最新」。
 *
 * 這是它在這個 repo 裡第四次出現（build_info、update_check、系統狀態頁、這裡），因
 * 為每一次被違反的後果都一樣：他錯過安全修補。
 */

import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { FRONTEND_COMMIT } from '../lib/buildInfo'
import type { SystemStatus } from '../lib/types'

/** 一個掃過去就看得到的記號。
 *
 * 使用者：「應該跟其他程式一樣，會有一個驚嘆號提醒就好，要不要裝隨便使用者。」
 *
 * 原本這一格全部都是一行字，而字要讀才知道有沒有事——他不會每次都讀角落。別的軟體用
 * 的是一個記號，而那個記號的意思只有「有件事等你」，不是「你現在得處理」。
 *
 * **只掛在他按得下去的兩種狀態上**：落後了、被改過。「查不到有沒有新版」不掛——那多半
 * 是 GitHub 抖一下，而一個會因為別人抖一下就亮起來的記號，兩天之後就沒有人看了。
 */
function Bang() {
  return (
    <span
      aria-hidden="true"
      className="mr-1.5 inline-flex h-4 w-4 items-center justify-center rounded-full bg-amber-500/90 text-[10px] font-bold leading-none text-slate-950"
    >
      !
    </span>
  )
}

export function VersionBadge({ signedIn }: { signedIn: boolean }) {
  // 還沒登入的時候只問 /healthz。
  //
  // 兩個理由。一、/api/system/status 需要登入，在登入頁本來就拿不到。二、更重要
  // 的：那個端點會去打 GitHub，把它開給沒登入的人等於讓任何人都能用我們的 IP 消
  // 耗 GitHub 的額度——而真的需要知道有沒有新版的時候就問不到了。
  //
  // /healthz 本來就是公開的（外部看門狗每五分鐘打一次），而且已經帶著版本。它不
  // 會對外連線，只讀環境變數。
  const health = useQuery({
    queryKey: ['healthz-version'],
    enabled: !signedIn,
    queryFn: () => api.get<{ version?: { commit?: string | null } }>('/healthz'),
    staleTime: 5 * 60 * 1000,
  })

  const status = useQuery({
    queryKey: ['system-status', FRONTEND_COMMIT],
    // 前端自己的版本是建置期常數，後端不知道——所以「我這一份是不是從上游來的」
    // 這個問題只有前端問得出來。
    enabled: signedIn,
    queryFn: () =>
      api.get<SystemStatus>(
        FRONTEND_COMMIT
          ? `/api/system/status?frontend_commit=${FRONTEND_COMMIT}`
          : '/api/system/status',
      ),
    // 版本不會在一分鐘內變。這一格跟著系統狀態頁共用同一份查詢，所以打開那一頁的
    // 時候不會多打一次。
    staleTime: 5 * 60 * 1000,
  })

  if (!signedIn) {
    // 登入頁只說版本，不說有沒有新版——上游那個問題要登入才問得到（見上面）。
    const commit = health.data?.version?.commit
    if (!commit) return null
    return (
      <div
        role="status"
        className="fixed bottom-3 right-3 z-40 rounded border border-slate-800 bg-slate-900/80 px-3 py-1.5 text-xs text-slate-500 shadow"
      >
        版本 <code>{commit}</code>
      </div>
    )
  }

  const update = status.data?.update
  if (!update) return null

  const running = update.running ?? '不知道'

  // **分岔跟落後是兩件事。**
  //
  // 他（或他的 AI）改過骨架的原始碼之後，自動同步就停了（它只快轉、絕不覆蓋）。
  // 那時候說「有新版可以更新」是錯的：他照著做拿到的還是自己那一版，重試幾次之後
  // 會放棄——而真正該告訴他的那件事從頭到尾沒有說出口。
  //
  // `null` 是「問不到」，那時候照舊講落後：誤判成分岔比誤判成落後更糟，因為那句話
  // 會讓他從此不再期待更新，包括安全修補。
  if (update.frontend_from_upstream === false) {
    return (
      <div
        role="status"
        className="fixed bottom-3 right-3 z-40 rounded border border-slate-600 bg-slate-900/90 px-3 py-1.5 text-xs text-slate-300 shadow"
      >
        <Bang />
        這一份被改過（<code>{running}</code>），所以不會自動更新——你的 GitHub 上會有一個
        <strong>等你按的更新</strong>
        <Link to="/system" className="ml-2 underline">
          說明
        </Link>
      </div>
    )
  }

  if (update.behind === true && update.latest) {
    return (
      <div
        role="status"
        className="fixed bottom-3 right-3 z-40 rounded border border-amber-700 bg-amber-950/90 px-3 py-1.5 text-xs text-amber-200 shadow"
      >
        <Bang />
        有新版 <code>{update.latest}</code>（你是 <code>{running}</code>）
        <Link to="/system" className="ml-2 underline">
          怎麼更新
        </Link>
      </div>
    )
  }

  // **這一格問的是「伺服器在哪一版」**，所以瀏覽器手上那份 bundle 是舊的時候，它會
  // 一路顯示灰色的「沒事」——伺服器確實沒事。但他看到的東西是舊的，而這是這個檔案開
  // 頭那條規則更糟的一種違反：不是把「不知道」畫成「已經是最新」，是把「舊的」畫成
  // 「已經是最新」。
  //
  // 只有一次部署判得出來（前端後端同一個映像檔）：兩半依建構為真是同一個 commit，所
  // 以畫面的 commit 跟伺服器的不一樣，只可能是快取（#92）。兩次部署不能這樣比——前端
  // 那一份本來就可能比後端舊，而那是另一句話、另一個辦法（系統狀態頁在講）。
  //
  // 排在「有新版」後面：兩件事同時成立的時候，落後要他做的事比較大，而且重新整理之
  // 後那句話還是會在。排在「查不到」前面：這個有一個一按就好的辦法，那個沒有。
  if (
    update.serves_its_own_frontend &&
    update.running &&
    FRONTEND_COMMIT &&
    FRONTEND_COMMIT !== update.running
  ) {
    return (
      <div
        role="status"
        className="fixed bottom-3 right-3 z-40 rounded border border-amber-700 bg-amber-950/90 px-3 py-1.5 text-xs text-amber-200 shadow"
      >
        <Bang />
        這個畫面是舊的（<code>{FRONTEND_COMMIT}</code>），伺服器已經是{' '}
        <code>{update.running}</code>——<strong>重新整理一次</strong>
      </div>
    )
  }

  if (update.behind === null) {
    // **不是「已經是最新」。** 查不到就說查不到——說成最新會讓他錯過安全修補，而那
    // 正是這一格存在的理由。
    return (
      <div
        role="status"
        className="fixed bottom-3 right-3 z-40 rounded border border-slate-700 bg-slate-900/90 px-3 py-1.5 text-xs text-slate-400 shadow"
      >
        版本 <code>{running}</code>・查不到有沒有新版
      </div>
    )
  }

  // 沒事。一行灰字，不用顏色吵他。
  return (
    <div
      role="status"
      className="fixed bottom-3 right-3 z-40 rounded border border-slate-800 bg-slate-900/80 px-3 py-1.5 text-xs text-slate-500 shadow"
    >
      版本 <code>{running}</code>
    </div>
  )
}
