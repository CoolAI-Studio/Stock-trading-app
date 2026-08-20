import { useState } from 'react'
import { currentPushAvailability } from '../lib/platform'

/**
 * The one thing an iPhone owner has to do before this app can do its job.
 *
 * On iOS, Web Push works only for a site added to the Home Screen. That is
 * Apple's rule, and it makes installing a PRECONDITION for the whole product
 * rather than a nicety -- without it there is no push, and push is the point.
 *
 * It used to be explained in exactly one place: 通知 → 新增管道 → 瀏覽器推播.
 * Three interactions deep, behind a radio button that is not selected by
 * default. Somebody who never went looking never found out, and had no reason
 * to look, because nothing anywhere told them their phone needed anything.
 *
 * Dismissible, because a banner that cannot be silenced stops being read. Only
 * for the session, because the app still is not doing its job until this is
 * done -- and localStorage would let one stray tap hide it forever, leaving
 * the owner with a phone that never makes a sound and no idea why.
 */

const DISMISSED_KEY = 'install-prompt-dismissed'

export function InstallPrompt() {
  // Read once on mount: the answer cannot change without a navigation, and
  // re-reading on every render would make the banner flicker.
  const [availability] = useState(currentPushAvailability)
  const [dismissed, setDismissed] = useState(
    () => sessionStorage.getItem(DISMISSED_KEY) === '1',
  )
  const [showSteps, setShowSteps] = useState(false)

  // 'unsupported' deliberately shows nothing. Sending somebody to look for a
  // Home Screen they do not have wastes their time and makes the app look
  // broken; that case is explained on the notifications page, where they are
  // actually trying to set a channel up.
  const relevant = availability.kind === 'needs-install' || availability.kind === 'in-app-browser'
  if (!relevant || dismissed) return null

  // Inside LINE's or Facebook's browser the install steps are worse than
  // useless -- that share sheet has no 「加入主畫面」 at all. The only thing that
  // helps is opening the page in Safari, so that is the whole message and
  // there are no steps to expand.
  const inAppBrowser = availability.kind === 'in-app-browser'

  function dismiss() {
    sessionStorage.setItem(DISMISSED_KEY, '1')
    setDismissed(true)
  }

  return (
    <div
      role="status"
      className="border-b border-amber-800/60 bg-amber-950/40 px-4 py-2 text-sm text-amber-100 sm:px-6"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {inAppBrowser ? (
          <span>
            你是從別的 app 內建的瀏覽器打開這一頁的，那裡收不到推播。請先
            <strong> 用 Safari 開啟這個網址</strong>（內建瀏覽器的選單裡通常有「用 Safari
            開啟」），再設定推播。
          </span>
        ) : (
          <span>
            這支手機還收不到提醒 —— 要先把這個 app <strong>加入主畫面</strong>。
          </span>
        )}
        {!inAppBrowser && (
          <button
            type="button"
            onClick={() => setShowSteps((open) => !open)}
            className="underline underline-offset-2"
          >
            怎麼做？
          </button>
        )}
        <button type="button" onClick={dismiss} className="ml-auto text-amber-300/80 underline">
          關閉
        </button>
      </div>

      {showSteps && !inAppBrowser && (
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-amber-100/90">
          <li>在 Safari 下方按「分享」按鈕（往上的箭頭）</li>
          <li>往下捲，找到「加入主畫面」</li>
          <li>按「新增」</li>
          <li>關掉 Safari，改從主畫面上新出現的圖示打開這個 app</li>
          <li>回到「通知」頁建立瀏覽器推播管道</li>
          {/* Said plainly, because the alternative is the owner concluding the
              app is broken on their phone and giving up on it. */}
          <li className="text-amber-300/80">
            這是 Apple 的限制：iPhone 只有從主畫面開啟的網頁 app 才能收推播，在 Safari
            分頁裡是連推播功能都沒有的。不是這個 app 的問題，但沒做這一步就真的收不到。
          </li>
        </ol>
      )}
    </div>
  )
}
