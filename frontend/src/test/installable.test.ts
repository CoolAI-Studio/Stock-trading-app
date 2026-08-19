import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

/**
 * The app has to be installable, and these check the parts that fail silently.
 *
 * On iOS, Web Push works only for a site added to the Home Screen and opened
 * from there. For an iPhone owner that makes the Home Screen icon the
 * difference between receiving alerts and not receiving them -- which puts a
 * missing manifest link or a missing apple-touch-icon squarely in the
 * "警告不能停擺" category rather than the cosmetic one.
 *
 * None of it is visible from inside the running app. Delete the manifest link
 * and every test still passes, every page still renders, and the only symptom
 * is that an iPhone quietly stops being able to receive anything -- discovered
 * weeks later by somebody who assumes they set it up wrong.
 */

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

function read(relative: string): string {
  return readFileSync(resolve(ROOT, relative), 'utf-8')
}

describe('index.html', () => {
  const html = read('index.html')

  it('連到 manifest', () => {
    expect(html).toMatch(/rel="manifest"\s+href="\/manifest\.webmanifest"/)
  })

  it('有 apple-touch-icon —— 沒有的話 iOS 會自己截一張網頁縮圖當圖示', () => {
    expect(html).toMatch(/rel="apple-touch-icon"/)
  })

  it('有 apple-mobile-web-app-capable，加到主畫面才會是全螢幕而不是開 Safari', () => {
    expect(html).toContain('apple-mobile-web-app-capable')
  })

  /** The viewport meta's content attribute, not the whole file: the comment
   * above it names the settings it deliberately does NOT use, and a
   * whole-file search matches the explanation as readily as the mistake. */
  const viewport = /<meta name="viewport" content="([^"]*)"/.exec(html)?.[1] ?? ''

  it('viewport 有 viewport-fit=cover，瀏海與底部指示條才不會蓋住內容', () => {
    expect(viewport).toContain('viewport-fit=cover')
  })

  it('沒有關掉縮放 —— 那是無障礙功能，不該為了排版犧牲', () => {
    expect(viewport).not.toBe('')
    expect(viewport).not.toContain('user-scalable=no')
    expect(viewport).not.toContain('maximum-scale')
  })

  it('有 theme-color，狀態列才不會是一塊白的', () => {
    expect(html).toMatch(/name="theme-color"/)
  })
})

describe('manifest', () => {
  const manifest = JSON.parse(read('public/manifest.webmanifest'))

  it('display 是 standalone，開起來才不是一個瀏覽器分頁', () => {
    expect(manifest.display).toBe('standalone')
  })

  it('short_name 短到能放在圖示下面不被截斷', () => {
    // iOS shows about 12 characters under a Home Screen icon.
    expect(manifest.short_name.length).toBeLessThanOrEqual(12)
  })

  it('有 192 和 512 兩種尺寸', () => {
    const sizes = manifest.icons.map((i: { sizes: string }) => i.sizes)
    expect(sizes).toContain('192x192')
    expect(sizes).toContain('512x512')
  })

  it('有一個 maskable 圖示，Android 裁成圓形時才不會把線切掉', () => {
    expect(manifest.icons.some((i: { purpose: string }) => i.purpose === 'maskable')).toBe(true)
  })

  it('start_url 從根目錄開始，不會停在使用者當初安裝的那一頁', () => {
    expect(manifest.start_url).toBe('/')
  })

  it('背景色跟頁面一樣，啟動時才不會閃一下白色', () => {
    expect(manifest.background_color).toBe('#0f172a')
  })
})

describe('圖示檔案真的存在', () => {
  // A manifest that points at a 404 installs an app with no icon, and nothing
  // anywhere reports it.
  it.each([
    'public/icons/icon-192.png',
    'public/icons/icon-512.png',
    'public/icons/icon-maskable-512.png',
    'public/icons/apple-touch-icon.png',
  ])('%s', (path) => {
    const bytes = readFileSync(resolve(ROOT, path))
    expect(bytes.length).toBeGreaterThan(0)
    // PNG magic. A JPEG or an SVG renamed to .png is rejected by iOS without
    // saying why.
    expect(bytes.subarray(0, 8)).toEqual(
      Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    )
  })
})
