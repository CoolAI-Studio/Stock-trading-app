/**
 * 送到瀏覽器的東西裡不可以有任何秘密。
 *
 * ＊ 這個架構特有的洩漏管道。
 *
 * Vite 會把**每一個 `VITE_` 開頭的環境變數**打進 bundle。那個 bundle 是公開的：
 * 任何人打開開發者工具都讀得到，而且 CDN 會把它快取到世界各地。
 *
 * 所以 `VITE_AI_API_KEY` 這種名字一旦出現，那把金鑰就會出現在每一個訪客的原始碼
 * 裡——而且不會有任何東西變紅：app 照常運作，測試照常通過，只是金鑰公開了。
 *
 * 後端有 scripts/audit.py 守著資料外洩，但它看的是 HTTP 回應和資料表，看不到前端
 * 的建置產物。這一條補的是那個缺口。
 *
 * ＊ 判準：VITE_ 只能放「公開了也無所謂」的東西。
 *
 * 網址、功能開關、版本編號可以。任何名字裡有 KEY / SECRET / TOKEN / PASSWORD 的東
 * 西都不行——它們要嘛存在後端的資料庫裡（加密的），要嘛由使用者在設定頁貼進去。
 */

import { readFileSync, readdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')

/** 名字長得像秘密的。大小寫不拘——`VITE_apiKey` 一樣會被打進 bundle。 */
const LOOKS_LIKE_A_SECRET = /VITE_[A-Z0-9_]*(KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|PRIVATE)/i

/** 我們自己寫的、會被打包送出去的檔案。
 *
 * 用 readdirSync 走檔案系統，不用 `git ls-files`：child_process 的型別不在這個
 * tsconfig 的涵蓋範圍裡，而 `npm run build`（＝ tsc -b）會因此紅——那正是這個
 * repo 被咬過三次的那個陷阱（tsc --noEmit 不涵蓋測試檔）。
 */
function sourceFiles(): string[] {
  const wanted = /\.(ts|tsx|js|jsx|html)$/
  const found: string[] = []
  for (const entry of readdirSync(resolve(ROOT, 'src'), { recursive: true, encoding: 'utf-8' })) {
    if (wanted.test(entry)) found.push(resolve(ROOT, 'src', entry))
  }
  // 設定檔也會決定 bundle 裡有什麼，所以一起看。
  for (const name of ['vite.config.ts', 'index.html']) found.push(resolve(ROOT, name))
  return found
}

describe('bundle 裡不可以有秘密', () => {
  it('沒有任何一個 VITE_ 變數的名字長得像秘密', () => {
    const offenders: string[] = []
    for (const file of sourceFiles()) {
      const text = readFileSync(file, 'utf-8')
      // 跳過這個檔案自己——它就是為了寫出那個模式而存在的。
      if (file.endsWith('noSecretsInTheBundle.test.ts')) continue
      for (const [match] of text.matchAll(new RegExp(LOOKS_LIKE_A_SECRET, 'gi'))) {
        offenders.push(`${file}: ${match}`)
      }
    }

    expect(offenders, [
      '這幾個變數會被 Vite 打進送到瀏覽器的 bundle 裡，也就是公開。',
      '秘密要嘛存在後端的資料庫（加密的），要嘛由使用者在設定頁貼進去。',
      ...offenders,
    ].join('\n')).toEqual([])
  })

  it('注入的建置期常數只有版本編號', () => {
    // vite.config.ts 的 `define` 是另一條同樣公開的管道，而且它更容易被忽略：那裡
    // 塞進去的東西連 VITE_ 前綴都沒有，看不出它會出現在 bundle 裡。
    const config = readFileSync(resolve(ROOT, 'vite.config.ts'), 'utf-8')
    const defined = [...config.matchAll(/__([A-Z0-9_]+)__\s*:/g)].map((m) => m[1])

    expect(defined).toEqual(['APP_COMMIT'])
  })
})
