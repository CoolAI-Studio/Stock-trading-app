/**
 * 陌生人看到的第一個地方（GitHub Pages），三頁。
 *
 * 為什麼它存在：這個 repo 是公開的，而一個只想在手機上收股票提醒的人打開它，看到
 * 的是五百多個檔案，其中一百八十幾個是測試。使用者：「使用者也看不懂到底哪些是要
 * 用到的。」
 *
 * 他提過的另一個做法是開第二個「乾淨骨架」repo。那會製造第二個事實來源——這一份
 * 修了策略沙箱逃逸和跨帳號隔離，骨架那一份要有人記得同步，而沒同步的那段時間，照
 * 骨架部署的人拿到的是有洞的版本，且他不會知道。同一個 repo 裡的幾頁 HTML 沒有這
 * 個問題。
 *
 * 順序是使用者定的：**先 AI、再資料庫、最後才裝**。第一版把三件事塞在同一頁，他
 * 的回應是「說了太多文字，造成視覺疲勞」，而且「看完整的安裝說明跟看原始碼都到同
 * 一個頁面，這樣是有問題的——安裝說明就是安裝說明，不會跳程式碼」。
 *
 * 這裡守的是承重規則，不是排版。
 */

import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const DOCS = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'docs')
const read = (name: string) => readFileSync(resolve(DOCS, name), 'utf-8')
const strip = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')

const PAGES = ['index.html', 'database.html', 'install.html'] as const

describe('引導：三頁，一頁一件事', () => {
  it('第一頁講 AI，而且說得出用跟不用的差別', () => {
    // 使用者：「不是一開始先教導如何設定 AI API 嗎？也要交代用不用 AI 的差別。」
    const page = read('index.html')

    // 比對卡片的標籤而不是內文：這一頁精簡之後，內文裡的「不用」多半屬於「不用
    // 註冊、不用付錢」。要驗的是**兩個選項並排存在**，不是某個詞出現過。
    expect(page).toMatch(/class="who">\s*不用\s*</)
    expect(page).toMatch(/class="who">\s*用 AI\s*</)
    // 而且差別要具體，不是「更好用」這種沒有資訊的句子。
    expect(strip(page)).toMatch(/照常|不影響/)
  })

  it('第一頁說得出金鑰去哪裡申請', () => {
    // CLAUDE.md：app 生得出來的就給按鈕，生不出來的老實說去哪裡拿。金鑰是後者。
    const page = read('index.html')

    expect(page).toContain('openrouter.ai/keys')
    expect(strip(page)).toMatch(/複製|Create Key/)
  })

  it('第二頁才講資料庫，而且本機和雲端是並排的兩條路', () => {
    const text = strip(read('database.html'))

    expect(text).toMatch(/自己的電腦/)
    expect(text).toMatch(/雲端/)
    // 差別要說在點子上：關機會怎樣，而不是「本機比較簡單」。
    expect(text).toMatch(/關機/)
  })

  it('第二頁不指定某一家 —— 它要的是三樣東西，不是三個品牌', () => {
    // 這一頁的前身是登入頁上一顆直接跳 render.com/deploy 的按鈕，那把選擇拿走了。
    const text = strip(read('database.html'))

    expect(text).toMatch(/不是三個品牌/)
    for (const alternative of ['Railway', 'Fly.io', 'Neon', 'Supabase']) {
      expect(text, `缺少 ${alternative}`).toContain(alternative)
    }
  })

  it('第二頁說得出資料庫是唯一要去別人家拿的東西', () => {
    expect(strip(read('database.html'))).toMatch(/連線字串/)
  })

  it('安裝說明跟原始碼是兩個不同的去處', () => {
    // 使用者：「看完整的安裝說明跟看原始碼都到同一個頁面，這樣是有問題的。」
    const page = read('install.html')
    const installLink = page.match(/href="([^"]*)"[^>]*>\s*完整的部署說明/)?.[1] ?? ''

    expect(installLink).toContain('DEPLOYMENT.md')
    expect(installLink).not.toMatch(/github\.com\/CoolAI-Studio\/Stock-trading-app\/?$/)
  })

  it('三頁互相走得通，而且知道自己是第幾步', () => {
    expect(read('index.html')).toContain('database.html')
    expect(read('database.html')).toContain('install.html')
    expect(read('database.html')).toContain('index.html')
    expect(read('install.html')).toContain('database.html')
    for (const name of PAGES) {
      expect(read(name), `${name} 沒有標出自己是哪一步`).toContain('class="now"')
    }
  })

  it('AI 是選配，第一頁就要說清楚', () => {
    // CLAUDE.md：設定流程不可以依賴 AI——AI 需要一把金鑰，那本身就是一格空白，
    // 依賴它就循環了。
    expect(strip(read('index.html'))).toMatch(/先跳過|選配|可以不用/)
  })

  it('每一頁都自己站得住，而且手機看得下去', () => {
    for (const name of PAGES) {
      const page = read(name)
      // Pages 上沒有建置步驟，抓不到 CDN 就散掉的頁面不能當第一個對外的畫面。
      expect(page, `${name} 有外部腳本`).not.toMatch(/<script[^>]+src=/i)
      expect(page, `${name} 抓了外部樣式表`).not.toMatch(/<link[^>]+stylesheet[^>]*https?:/i)
      // 目標使用者是「想在手機上收股票提醒的人」，他很可能就在手機上讀這一頁。
      expect(page, `${name} 沒有 viewport`).toMatch(/<meta[^>]+viewport/i)
    }
  })

  it('文字要精簡 —— 使用者說第一版讓人視覺疲勞', () => {
    // 不是排版品味，是一條會紅的線。使用者：「不是拆成三頁，而是要把贅字都去除，
    // 只留精簡的文字，你可以看看 apple 的網站。」
    //
    // 目前 248 / 368 / 259 字元（第一版單頁約 2700）。上限訂在 450：夠寫完一頁該
    // 講的事，寫不下第二段補述——而補述正是贅字的來源。真的需要細節的人往
    // DEPLOYMENT.md 走，那裡本來就是說明書。
    for (const name of PAGES) {
      const size = strip(read(name)).replace(/\s/g, '').length
      expect(size, `${name} 有 ${size} 個字元，太長了`).toBeLessThan(450)
    }
  })
})
