import { readFileSync, readdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

/**
 * The layout rules that break on a phone and nowhere else.
 *
 * jsdom has no layout engine, so nothing in the normal test suite can tell
 * that a nine-column table runs off the side of a 390px screen with no way to
 * reach the columns past the fourth. It renders, every assertion passes, and
 * the page is unusable in the hand.
 *
 * These are source-level checks rather than rendering ones for exactly that
 * reason. They are narrow on purpose: each one pins a specific thing that was
 * actually wrong, and none of them pretends to be a substitute for looking at
 * the app on a phone.
 */

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), '..')

function sourceFiles(dir: string): string[] {
  return readdirSync(resolve(SRC, dir))
    .filter((name) => name.endsWith('.tsx') && !name.endsWith('.test.tsx'))
    .map((name) => `${dir}/${name}`)
}

const FILES = [...sourceFiles('pages'), ...sourceFiles('components')]

describe('寬表格要能橫向捲動', () => {
  // Positions, orders, backtest history and the trade list all carry seven to
  // nine columns. On a phone the ones past the fourth were simply gone --
  // clipped, with nothing to scroll and nothing to say so.
  it.each(FILES)('%s', (file) => {
    const lines = readFileSync(resolve(SRC, file), 'utf-8').split('\n')

    const unwrapped: number[] = []
    lines.forEach((line, index) => {
      if (!line.includes('<table')) return
      // The scroll container is always the element immediately around it, so
      // a short look back is enough and keeps this from matching some
      // unrelated overflow rule elsewhere in the file.
      const before = lines.slice(Math.max(0, index - 3), index).join('\n')
      if (!before.includes('overflow-x')) unwrapped.push(index + 1)
    })

    expect(unwrapped, `這些行的 <table> 外面沒有可橫向捲動的容器`).toEqual([])
  })
})

describe('固定欄數的格線在窄螢幕會擠爆', () => {
  it.each(FILES)('%s', (file) => {
    const source = readFileSync(resolve(SRC, file), 'utf-8')

    // `grid-cols-3` with no breakpoint prefix applies at every width,
    // including 390px, where three cards share 120px each and every number in
    // them wraps onto four lines. The fix is always the same shape: a small
    // count by default, more from `sm:` up.
    const bare = source.match(/(?<![:\w-])grid-cols-[34567]\b/g) ?? []

    expect(bare, '固定欄數要加上 sm:／md: 斷點').toEqual([])
  })
})

describe('手機上的安全區與可點面積', () => {
  const css = readFileSync(resolve(SRC, 'index.css'), 'utf-8')

  it('表單元件在小螢幕至少 16px，否則 iOS 對焦時會自動放大整頁', () => {
    // The usual "fix" is maximum-scale=1, which stops the zoom by taking
    // pinch-zoom away from everyone. 16px stops it without costing anything.
    expect(css).toMatch(/font-size:\s*16px/)
  })

  it('有處理瀏海與底部指示條的安全區', () => {
    // viewport-fit=cover lets the page reach under them; without padding back
    // out, the header sits under the notch and the last row under the home
    // indicator.
    expect(css).toContain('safe-area-inset')
  })
})

describe('導覽列在窄螢幕不能把項目推到螢幕外', () => {
  const layout = readFileSync(resolve(SRC, 'components/Layout.tsx'), 'utf-8')

  it('可以換行', () => {
    // Ten items in a non-wrapping row measure about 630px. A phone is 390px,
    // so the last few links -- and the 登出 button after them -- were simply
    // off the side of the screen with no way to reach them. Not "awkward on
    // mobile": unreachable.
    expect(layout).toMatch(/flex-wrap/)
  })

  it('沒有 whitespace-nowrap 之類把換行擋掉的東西', () => {
    expect(layout).not.toContain('flex-nowrap')
  })
})
