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
  /** Genuinely not going to work here. */
  | { kind: 'unsupported'; message: string }

const INSTALL_STEPS =
  'iPhone／iPad 只有把網站「加入主畫面」之後才能收推播，這是 Apple 的限制，不是這個 app 的問題。' +
  '做法：在 Safari 下方按「分享」按鈕（往上的箭頭）→ 往下捲找到「加入主畫面」→ 按「新增」。' +
  '然後關掉 Safari，改從主畫面上新出現的圖示打開這個 app，再回到這一頁設定推播。' +
  '沒有從主畫面打開的話，設定會一樣失敗。'

const TOO_OLD =
  '這台裝置已經是從主畫面打開的，但系統仍然沒有推播功能 —— 代表 iOS 版本太舊。' +
  'Web 推播要 iOS 16.4 以上才有。先更新系統，或改用 Telegram／Email 通知管道。'

const NOT_SUPPORTED =
  '這個瀏覽器沒有 Web 推播功能，設定了也收不到。' +
  '可以改用 Telegram 或 Email 通知管道，兩者都不挑瀏覽器。'

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
    return env.standalone
      ? { kind: 'unsupported', message: TOO_OLD }
      : { kind: 'needs-install', message: INSTALL_STEPS }
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
