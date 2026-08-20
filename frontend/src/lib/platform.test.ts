import { describe, expect, it } from 'vitest'
import { pushAvailability, type PushEnvironment } from './platform'

/**
 * Why an iPhone says push is unsupported when it is not.
 *
 * On iOS, Web Push works ONLY for a site that has been added to the Home
 * Screen and opened from there. In Safari itself `PushManager` is simply
 * absent -- so the app's support check failed, and the owner was told "這個
 * 瀏覽器不支援推播通知".
 *
 * That sentence is wrong, and wrong in the most expensive direction this
 * product has: somebody reads it, concludes their iPhone cannot receive
 * alerts, and stops. The browser supports it perfectly well. They are two taps
 * away, and nothing on screen said which two.
 *
 * These distinguish the three states that were previously one:
 *   - it works
 *   - it will work, once you add it to the Home Screen (here are the steps)
 *   - it genuinely will not work here
 */

const IPHONE =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1'
const IPAD_DESKTOP_MODE =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15'
const DESKTOP_CHROME =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
const ANDROID_CHROME =
  'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36'

function env(overrides: Partial<PushEnvironment> = {}): PushEnvironment {
  return {
    userAgent: DESKTOP_CHROME,
    maxTouchPoints: 0,
    hasPushManager: true,
    hasServiceWorker: true,
    hasNotification: true,
    standalone: false,
    ...overrides,
  }
}

describe('可以用的時候', () => {
  it('該有的都有就是可以用', () => {
    expect(pushAvailability(env()).kind).toBe('ready')
  })

  it('iPhone 從主畫面打開、而且 API 都在，就是可以用', () => {
    const result = pushAvailability(
      env({ userAgent: IPHONE, maxTouchPoints: 5, standalone: true }),
    )
    expect(result.kind).toBe('ready')
  })
})

describe('iPhone 在 Safari 裡打開', () => {
  const inSafari = env({
    userAgent: IPHONE,
    maxTouchPoints: 5,
    standalone: false,
    hasPushManager: false,
  })

  it('不能說「不支援」—— 它支援，只是要先加到主畫面', () => {
    const result = pushAvailability(inSafari)

    expect(result.kind).toBe('needs-install')
  })

  it('要給實際的步驟，不是只說「請加入主畫面」', () => {
    const result = pushAvailability(inSafari)

    if (result.kind === 'ready') throw new Error('expected an explanation')
    expect(result.message).toContain('分享')
    expect(result.message).toContain('主畫面')
  })

  it('也要講「加完之後要從主畫面打開」', () => {
    // The step people miss: they add it, stay in Safari, try again, and it
    // still does not work -- which reads as the instructions being wrong.
    const result = pushAvailability(inSafari)

    if (result.kind === 'ready') throw new Error('expected an explanation')
    expect(result.message).toMatch(/從主畫面(打開|開啟)/)
  })

  it('iPad 用桌面版 UA 偽裝成 Mac 時也要認出來', () => {
    // iPadOS reports itself as Macintosh. Touch points are the only thing that
    // separates it from a real Mac, and a real Mac genuinely does support push
    // in Safari -- so getting this wrong sends Mac users chasing a Home Screen
    // that does not exist.
    const result = pushAvailability(
      env({ userAgent: IPAD_DESKTOP_MODE, maxTouchPoints: 5, hasPushManager: false }),
    )

    expect(result.kind).toBe('needs-install')
  })

  it('真正的 Mac 不會被當成 iPad', () => {
    const result = pushAvailability(
      env({ userAgent: IPAD_DESKTOP_MODE, maxTouchPoints: 0, hasPushManager: false }),
    )

    expect(result.kind).toBe('unsupported')
  })
})

describe('真的不能用的時候', () => {
  it('已經從主畫面打開卻還是沒有 API，就是 iOS 版本太舊', () => {
    // Web Push arrived in iOS 16.4. Telling this person to add it to the Home
    // Screen again would be an instruction they have already followed.
    const result = pushAvailability(
      env({ userAgent: IPHONE, maxTouchPoints: 5, standalone: true, hasPushManager: false }),
    )

    expect(result.kind).toBe('unsupported')
    if (result.kind === 'ready') throw new Error('expected an explanation')
    expect(result.message).toContain('16.4')
  })

  it('其他瀏覽器缺 API 就照實說，不要叫人去加主畫面', () => {
    const result = pushAvailability(env({ hasPushManager: false }))

    expect(result.kind).toBe('unsupported')
    if (result.kind === 'ready') throw new Error('expected an explanation')
    expect(result.message).not.toContain('主畫面')
  })

  it('缺 service worker 一樣不能用', () => {
    expect(pushAvailability(env({ hasServiceWorker: false })).kind).toBe('unsupported')
  })

  it('缺 Notification 一樣不能用', () => {
    expect(pushAvailability(env({ hasNotification: false })).kind).toBe('unsupported')
  })

  it('Android Chrome 該能用就能用，不要被 iOS 的判斷掃到', () => {
    expect(pushAvailability(env({ userAgent: ANDROID_CHROME, maxTouchPoints: 5 })).kind).toBe(
      'ready',
    )
  })
})

// --- opened from inside another app -----------------------------------------
//
// LINE, Facebook and Instagram open links in their own embedded browser
// (WKWebView / SFSafariViewController), which has no Web Push at all AND no
// 「加入主畫面」 in its share sheet. Telling somebody there to follow the
// install steps sends them looking for a menu item that does not exist, and
// the app looks broken. For a Taiwanese owner this is the common case: links
// arrive in LINE.

describe('從別的 app 內建瀏覽器打開', () => {
  const LINE_IOS =
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Line/14.9.0'
  const FB_IOS =
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) [FBAN/FBIOS;FBAV/470.0]'

  function env(userAgent: string) {
    return {
      userAgent,
      maxTouchPoints: 5,
      hasPushManager: false,
      hasServiceWorker: true,
      hasNotification: false,
      standalone: false,
    }
  }

  it('LINE 的內建瀏覽器要單獨認出來', () => {
    expect(pushAvailability(env(LINE_IOS)).kind).toBe('in-app-browser')
  })

  it('Facebook 的也是', () => {
    expect(pushAvailability(env(FB_IOS)).kind).toBe('in-app-browser')
  })

  it('訊息要叫人先用 Safari 開，而不是去找不存在的「加入主畫面」', () => {
    const result = pushAvailability(env(LINE_IOS))

    if (result.kind === 'ready') throw new Error('unreachable')
    expect(result.message).toContain('Safari')
    // Pinning the meaning, not a substring: the message may mention the share
    // sheet, but only to say it does NOT have the option. What it must never
    // do is hand out the ordinary install steps.
    expect(result.message).not.toContain('加入主畫面」→')
    expect(result.kind).toBe('in-app-browser')
  })

  it('真正的 Safari 不要被誤判', () => {
    const safari =
      'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'

    expect(pushAvailability(env(safari)).kind).toBe('needs-install')
  })

  it('已經從主畫面開啟的就不管 user agent 長怎樣', () => {
    // A Home Screen web app is not an in-app browser even if the UA is odd.
    const installed = { ...env(LINE_IOS), standalone: true, hasPushManager: true, hasNotification: true }

    expect(pushAvailability(installed).kind).toBe('ready')
  })
})

describe('安裝步驟的內容', () => {
  it('要提醒不要關掉「開啟為網頁 App」', () => {
    // iOS 26 defaults every Home Screen addition to opening as a web app, but
    // the user can switch that off at the moment they add it -- and then it is
    // a bookmark, with no push, looking exactly like a successful install.
    const result = pushAvailability({
      userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) Version/17.5 Safari/604.1',
      maxTouchPoints: 5,
      hasPushManager: false,
      hasServiceWorker: true,
      hasNotification: false,
      standalone: false,
    })

    if (result.kind === 'ready') throw new Error('unreachable')
    expect(result.message).toContain('網頁 App')
  })
})
