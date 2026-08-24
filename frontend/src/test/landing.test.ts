/**
 * 陌生人看到的第一個地方（GitHub Pages），三頁。
 *
 * 為什麼它存在：這個 repo 是公開的，而一個只想在手機上收股票提醒的人打開它，看到
 * 的是五百多個檔案，其中一百八十幾個是測試。開第二個「乾淨骨架」repo 會製造第二個
 * 事實來源——這一份修了策略沙箱逃逸，骨架那一份要有人記得同步，而沒同步的那段時
 * 間，照骨架部署的人拿到的是有洞的版本，且他不會知道。
 *
 * 這一頁被使用者退回三次，每一次都是同一類的錯：
 *
 *   一、「說了太多文字，造成視覺疲勞。」我的反應是拆成三頁——他直接指出那沒有解
 *       決問題：「不是拆成三頁，而是要把贅字都去除。」分頁只是把同樣多的字分散開。
 *
 *   二、「看完整的安裝說明跟看原始碼都到同一個頁面」——安裝說明就是安裝說明。
 *
 *   三、「你這個設定是專家級的工程師才有辦法設定。」精簡過頭之後，頁面變成一份漂
 *       亮的目錄：講了要去哪幾家，沒講怎麼申請、連結在哪、拿到的東西要貼去哪。
 *       「要他去 github 看那一堆冷冰冰的檔案，根本無從做起，我就直接放棄。」
 *
 * 所以精簡和完整不是二選一：**預設看到的要短，展開之後要真的做得完。** 下面的字數
 * 上限只算收起來時看得到的部分，而每一個要他去別人家做的動作都必須有手把手。
 */

import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const DOCS = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'docs')
const read = (name: string) => readFileSync(resolve(DOCS, name), 'utf-8')
const strip = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')
/** 收起來的時候看得到的字。展開的細節不算——那是他按了才出現的。 */
const visible = (html: string) => strip(html.replace(/<details[\s\S]*?<\/details>/g, ' '))

const PAGES = ['index.html', 'database.html', 'install.html'] as const

describe('引導：預設看起來短，展開之後做得完', () => {
  it('第一頁講 AI，兩個選項並排，差別具體', () => {
    const page = read('index.html')

    // 比對卡片標籤而不是內文：內文裡的「不用」多半屬於「不用註冊、不用付錢」。
    expect(page).toMatch(/class="who">\s*不用\s*</)
    expect(page).toMatch(/class="who">\s*用 AI\s*</)
    expect(strip(page)).toMatch(/照常|不影響/)
  })

  it('第一頁說得出金鑰怎麼申請，不只給一個網址', () => {
    const text = strip(read('index.html'))

    expect(read('index.html')).toContain('openrouter.ai')
    expect(text).toMatch(/Sign in|登入/)
    expect(text).toMatch(/Create Key/)
  })

  it('第一頁說得出金鑰要填在哪裡 —— 這是他被卡住的地方', () => {
    // 使用者：「我要去哪裡『填』API key？有申請卻不知道填哪裡？」
    //
    // 而這一頁存不了任何東西：它是 GitHub 上的一個靜態檔，沒有後端也沒有資料庫。
    // 所以它能做、也必須做的，是說清楚那一格在哪裡。
    const text = strip(read('index.html'))

    expect(text).toContain('AI_API_KEY')
    expect(text).toMatch(/AI 輔助/)
  })

  it('第二頁每一家都有手把手，而且點得過去', () => {
    // 使用者：「你可以去找哪幾家…但沒有給連結，那幾家要用什麼方式才能申請的成功，
    // 完全沒有。」
    const page = read('database.html')

    for (const [vendor, url] of [
      ['Neon', 'https://neon.tech'],
      ['Supabase', 'https://supabase.com'],
    ] as const) {
      const block = page.match(new RegExp(`<details>[\\s\\S]*?${vendor}[\\s\\S]*?</details>`))?.[0]
      expect(block, `${vendor} 沒有可展開的申請說明`).toBeTruthy()
      expect(block, `${vendor} 的說明裡沒有連結`).toContain(url)
      // 手把手＝有編號的步驟，不是一段話。
      expect(block, `${vendor} 的說明不是步驟`).toMatch(/<ol>/)
    }
  })

  it('第二頁講得出拿到的那串東西是什麼、長什麼樣', () => {
    // 「申請好要如何串也沒說。」
    const text = strip(read('database.html'))

    expect(text).toContain('連線字串')
    expect(text).toContain('postgresql://')
  })

  it('第二頁不指定某一家', () => {
    const text = strip(read('database.html'))

    expect(text).toMatch(/不是三個品牌/)
    for (const alternative of ['Railway', 'Fly.io', 'Neon', 'Supabase']) {
      expect(text, `缺少 ${alternative}`).toContain(alternative)
    }
  })

  it('第三頁是可以照著按的步驟，不是叫他去讀 GitHub', () => {
    // 使用者：「單純指點一個根本不知道資料庫是什麼的門外漢，要他去 github 看那一
    // 堆冷冰冰的檔案，根本無從做起，我就直接放棄。」
    const page = read('install.html')

    expect(page).toContain('render.com/deploy')
    expect(page).toContain('vercel.com/new')
    expect(strip(page)).toContain('VITE_API_BASE_URL')
    expect(strip(page)).toContain('DATABASE_URL')
    // GitHub 只能是「還是不行」時的最後一條路，不是主要動線。
    const beforeTrouble = page.split('卡住了')[0]
    expect(beforeTrouble).not.toContain('DEPLOYMENT.md')
  })

  it('第三頁也給得出「跑在自己電腦上」那條路', () => {
    const text = strip(read('install.html'))

    expect(text).toMatch(/docker compose up/)
    expect(text).toMatch(/localhost/)
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
      expect(page, `${name} 沒有 viewport`).toMatch(/<meta[^>]+viewport/i)
      // 行內樣式會讓三頁慢慢長出三套外觀。
      expect(page, `${name} 有行內樣式`).not.toMatch(/\sstyle="/)
    }
  })

  it('收起來的時候要短 —— 使用者說第一版讓人視覺疲勞', () => {
    // 只算預設看得到的字。展開的細節不算：那是他自己按開的，而且那些字正是他做得
    // 完的原因。兩件事都要，所以分開量。
    for (const name of PAGES) {
      const size = visible(read(name)).replace(/\s/g, '').length
      expect(size, `${name} 收起來還有 ${size} 個字元，太長了`).toBeLessThan(520)
    }
  })

  it('而且展開之後真的有東西 —— 不是三個空抽屜', () => {
    for (const name of PAGES) {
      const drawers = read(name).match(/<details>/g)?.length ?? 0
      expect(drawers, `${name} 可展開的說明太少`).toBeGreaterThanOrEqual(2)
    }
  })
})
