/**
 * Whether this device can actually deliver a push notification, and if not,
 * whether that is fixable by the person holding it.
 *
 * On iOS, Web Push works only for a site added to the Home Screen and opened
 * from there; inside Safari, `PushManager` is simply absent. The app's support
 * check saw that and said "這個瀏覽器不支援推播通知".
 *
 * That is wrong, and wrong in the direction this product can least afford:
 * somebody reads it, concludes their iPhone cannot receive alerts, and stops
 * setting them up. The browser supports it fine. They are two taps away, and
 * nothing on the screen said which two.
 *
 * So the answer here has three states rather than two, because "no" was
 * hiding a "not yet".
 */

export interface PushEnvironment {
  userAgent: string
  /** iPadOS reports itself as a Mac; touch points are what separate them. */
  maxTouchPoints: number
  hasPushManager: boolean
  hasServiceWorker: boolean
  hasNotification: boolean
  /** Opened from the Home Screen rather than inside the browser. */
  standalone: boolean
}

export type PushAvailability =
  | { kind: 'ready' }
  /** Supported, but only once the site lives on the Home Screen. */
  | { kind: 'needs-install'; message: string }
  /** Opened inside another app's embedded browser, where there is no push AND
   * no way to install from here. A different instruction entirely. */
  | { kind: 'in-app-browser'; message: string }
  /** Genuinely not going to work here. */
  | { kind: 'unsupported'; message: string }

const INSTALL_STEPS =
  'iPhone／iPad 只有把網站「加入主畫面」之後才能收推播，這是 Apple 的限制，不是這個 app 的問題。' +
  '做法：在 Safari 下方按「分享」按鈕（往上的箭頭）→ 往下捲找到「加入主畫面」→ 按「新增」。' +
  // iOS 26 defaults every Home Screen addition to opening as a web app, but the
  // user can switch that off at the moment they add it. Then it is a bookmark:
  // no push, and indistinguishable from a successful install.
  '加入時如果看到「開啟為網頁 App」的開關，不要把它關掉 —— 關掉就只是書籤，一樣收不到。' +
  '然後關掉 Safari，改從主畫面上新出現的圖示打開這個 app，再回到這一頁設定推播。' +
  '沒有從主畫面打開的話，設定會一樣失敗。' +
  // Two icons for the same site are two separate apps on iOS, with separate
  // storage, separate permission and separate subscriptions -- which produces
  // "I definitely set this up and it still does not work".
  '主畫面上只留一個圖示就好；同一個網站裝兩份，iOS 會當成兩個各自獨立的 app。'

const TOO_OLD =
  '這台裝置已經是從主畫面打開的，但系統仍然沒有推播功能 —— 代表 iOS 版本太舊。' +
  'Web 推播要 iOS 16.4 以上才有。先更新系統，或改用 Telegram／Email 通知管道。'

const IN_APP_BROWSER =
  '你現在是從別的 app（LINE、Facebook 之類）內建的瀏覽器打開這一頁的，那裡沒有推播功能，' +
  '而且分享選單裡也沒有「加入主畫面」可以按。' +
  '請先用 Safari 開啟這個網址（在內建瀏覽器右下角的選單裡通常有「用 Safari 開啟」），' +
  '再照設定推播的步驟做一次。'

const NOT_SUPPORTED =
  '這個瀏覽器沒有 Web 推播功能，設定了也收不到。' +
  '可以改用 Telegram 或 Email 通知管道，兩者都不挑瀏覽器。'

// Embedded browsers that report themselves. A heuristic by nature -- there is
// no standard signal -- so it is only ever used to give BETTER advice, never to
// refuse anything: a false negative just shows the ordinary install steps.
const IN_APP_MARKERS = [
  / Line\//, // LINE, the one that matters most for a Taiwanese owner
  /FBAN|FBAV|FB_IAB/, // Facebook
  /Instagram/,
  /MicroMessenger/, // WeChat
  /; wv\)/, // generic Android WebView, harmless to include
]

function isInAppBrowser(userAgent: string): boolean {
  return IN_APP_MARKERS.some((marker) => marker.test(userAgent))
}

function isApplePortable(env: PushEnvironment): boolean {
  if (/iPad|iPhone|iPod/.test(env.userAgent)) return true
  // iPadOS in desktop mode is indistinguishable from macOS by user-agent
  // alone. Getting this wrong in the other direction matters: Safari on a real
  // Mac does support push, so a Mac user must never be sent looking for a Home
  // Screen they do not have.
  return /Macintosh/.test(env.userAgent) && env.maxTouchPoints > 1
}

export function pushAvailability(env: PushEnvironment): PushAvailability {
  if (env.hasPushManager && env.hasServiceWorker && env.hasNotification) {
    return { kind: 'ready' }
  }

  if (isApplePortable(env)) {
    // Already installed and still missing the API means the OS is too old.
    // Repeating the Home Screen instructions to someone who has followed them
    // reads as the app being broken.
    if (env.standalone) return { kind: 'unsupported', message: TOO_OLD }
    // Inside another app's browser the install steps are worse than useless:
    // that share sheet has no 「加入主畫面」 at all, so following them fails and
    // the app looks broken. Sending them to Safari is the only thing that
    // helps.
    if (isInAppBrowser(env.userAgent)) {
      return { kind: 'in-app-browser', message: IN_APP_BROWSER }
    }
    return { kind: 'needs-install', message: INSTALL_STEPS }
  }

  return { kind: 'unsupported', message: NOT_SUPPORTED }
}

export function readEnvironment(): PushEnvironment {
  return {
    userAgent: navigator.userAgent,
    maxTouchPoints: navigator.maxTouchPoints ?? 0,
    hasPushManager: 'PushManager' in window,
    hasServiceWorker: 'serviceWorker' in navigator,
    hasNotification: 'Notification' in window,
    standalone: isStandalone(),
  }
}

/** Running from the Home Screen / installed, rather than in a browser tab. */
export function isStandalone(): boolean {
  // navigator.standalone is iOS-only and is the only signal that works there;
  // display-mode covers every other platform's installed state.
  const iosStandalone = (navigator as Navigator & { standalone?: boolean }).standalone
  if (iosStandalone === true) return true
  return window.matchMedia?.('(display-mode: standalone)').matches ?? false
}

/** The current device's answer. Convenience over readEnvironment() for the
 * many call sites that only want the verdict. */
export function currentPushAvailability(): PushAvailability {
  return pushAvailability(readEnvironment())
}
