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

import { existsSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const DOCS = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'docs')
const read = (name: string) => readFileSync(resolve(DOCS, name), 'utf-8')
const strip = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')
/** 收起來的時候看得到的字。展開的細節不算——那是他按了才出現的。 */
const visible = (html: string) => strip(html.replace(/<details[\s\S]*?<\/details>/g, ' '))

/** 把整頁掛進 jsdom，並且真的跑 assist.js。
 *
 * 問的是行為，不是文案裡有沒有那個字：這幾頁的「選了才看得到」是 JS 做的，而一條只
 * 比對字串的測試會在 JS 根本沒接上的時候照樣綠。
 */
function mountPage(name: string): void {
  const html = read(name)
  const body = html.slice(html.indexOf('<body'), html.indexOf('</body>'))
  document.body.innerHTML = body
    .slice(body.indexOf('>') + 1)
    .replace(/<script[\s\S]*?<\/script>/g, '')
  new Function(read('assist.js'))()
  document.dispatchEvent(new Event('DOMContentLoaded'))
}

const pathBlocks = (path: string) =>
  Array.from(document.querySelectorAll<HTMLElement>(`[data-path="${path}"]`))
const pathChoice = (path: string) =>
  document.querySelector<HTMLElement>(`[data-path-choice="${path}"]`)
/** 這一頁上有哪幾條路。 */
const pathsIn = (html: string) => [
  ...new Set([...html.matchAll(/data-path="([^"]+)"/g)].map((m) => m[1])),
]

/** 把某一條路的內容整段拿掉。
 *
 * 標籤會巢狀（步驟裡面還有步驟、區塊裡面還有區塊），所以要數開合，不能用非貪婪比對
 * ——那會停在第一個內層的結束標籤，看起來有效、實際上什麼都沒拿掉。（我先寫了那一
 * 版，量出來的字數一個都沒少才發現。）
 */
function withoutPath(html: string, path: string): string {
  let out = html
  for (;;) {
    const found = new RegExp(`<([a-z]+)[^>]*?data-path="${path}"`).exec(out)
    if (!found) return out
    const tag = found[1]
    const start = found.index
    const open = `<${tag}`
    const close = `</${tag}>`
    let depth = 0
    let i = start
    for (;;) {
      const nextOpen = out.indexOf(open, i)
      const nextClose = out.indexOf(close, i)
      if (nextClose === -1) return out
      if (nextOpen !== -1 && nextOpen < nextClose) {
        depth += 1
        i = nextOpen + open.length
      } else {
        depth -= 1
        i = nextClose + close.length
        if (depth === 0) break
      }
    }
    out = `${out.slice(0, start)} ${out.slice(i)}`
  }
}

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
    // 這裡問的是**系統要用的那一份**。這幾頁沒有後端，存不進他的部署裡，所以能做
    // 也必須做的，是把那一格指出來：部署表單的 AI_API_KEY，或裝完後的 AI 輔助頁。
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

  it('第二頁不可以把「放前端的地方」講成必要的一樣', () => {
    // #53 之後畫面跟 API 出自同一份部署，所以要準備的東西從三樣變成兩樣。
    //
    // 這一頁原本寫著「它要的是三樣東西」並列出 Vercel，而那會做兩件壞事：讓一個
    // 不是工程師的人以為還要再註冊一家、再部署一次；以及讓他在安裝那一頁找不到
    // 對應的步驟時以為自己漏掉了什麼。
    //
    // 這一條跟 docker compose up 那一條同一個教訓：**驗文案不驗事實**，而事實已經
    // 變了。分開放仍然是一條路，所以不是拿掉那一段，是把它從「要求」改成「選擇」。
    const page = read('database.html')

    expect(page).not.toMatch(/它要的是三樣東西/)
    const block = page.match(/<details>[\s\S]*?放前端的地方[\s\S]*?<\/details>/)?.[0]
    expect(block, '「放前端的地方」那一段不見了').toBeTruthy()
    expect(block, '還在把前端主機講成必要的').toMatch(/不需要/)
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

  it('第一頁不只介紹一家 AI —— 而且每一家都給得出接進系統要填什麼', () => {
    // 使用者：「AI 其他金鑰介紹的網站不夠多，我發現 nVidia 似乎不錯，也是免費，
    // 請在頁面多介紹並且真的可以接入系統的 API。」
    //
    // 「真的可以接入」＝要給網址。系統的供應者選「OpenAI 相容」之後，能不能用就
    // 只取決於那個 base URL 對不對。
    const page = read('index.html')

    for (const [vendor, baseUrl] of [
      ['OpenRouter', 'https://openrouter.ai/api/v1'],
      ['NVIDIA', 'https://integrate.api.nvidia.com/v1'],
      ['Groq', 'https://api.groq.com/openai/v1'],
      ['OpenAI', 'https://api.openai.com/v1'],
    ] as const) {
      expect(page, `沒有介紹 ${vendor}`).toContain(vendor)
      expect(page, `${vendor} 沒有給接進系統要填的網址`).toContain(baseUrl)
    }
  })

  it('第三頁的替代平台不是一句帶過 —— 不然就是擺明只能用 Render', () => {
    // 使用者：「不想用 Render 的說明太少，這已經擺明直接用 render 了，對嗎？」
    const page = read('install.html')

    // 切開來找，不用正則：template literal 裡的 \s 會被 JS 當成跳脫吃掉，寫出
    // 一個看起來對、實際上在找 [sS] 的表達式。
    const drawers = page.split('<details').slice(1)
    for (const vendor of ['Railway', 'Fly.io']) {
      const block = drawers.find((chunk) => chunk.slice(0, chunk.indexOf('</details>')).includes(vendor))
      expect(block, `${vendor} 沒有自己的說明`).toBeTruthy()
      expect(block, `${vendor} 的說明不是步驟`).toMatch(/<ol>/)
    }
  })

  it('第一頁收得了金鑰，第二三頁就問得了 AI', () => {
    // 使用者：「不是第一頁 API key 填完之後，第二、三頁的部分就可以導入 AI 輔助
    // 了嗎？但這個我看不出來。」——原本完全沒有。
    expect(read('index.html')).toContain('id="ai-key-form"')
    for (const name of ['database.html', 'install.html']) {
      const page = read(name)
      expect(page, `${name} 沒有問 AI 的區塊`).toContain('id="ai-ask"')
      // 沒有金鑰就不該出現：按了會失敗的東西不要給。
      expect(page, `${name} 的問 AI 區塊預設沒有藏起來`).toMatch(/id="ai-ask"[^>]*\shidden/)
      // 要帶著這一頁在講什麼過去，不然它只會泛泛地回答。
      expect(page, `${name} 沒有把這一步的脈絡帶給 AI`).toMatch(/data-context="[^"]{40,}"/)
    }
  })

  it('分得清「引導頁暫存的金鑰」和「系統要用的金鑰」', () => {
    // 使用者：「你這邊怎麼有辦法第一頁就輸入 API key 啟動第二三頁的說明？」
    //
    // 因為那是兩件事，而我把它們寫成同一件：同一頁上一邊寫「這一頁存不了你的金
    // 鑰」，一邊放一個收金鑰的表單。兩句話都對不了同一個對象——引導頁暫存的那一
    // 份只讓這幾頁能問 AI，系統要用的那一份在部署表單或 AI 輔助頁。
    const text = strip(read('index.html'))

    expect(text).toMatch(/這不是系統的設定|不等於系統設定/)
    // 而且不可以再出現「存不了金鑰」那種絕對句：頁面現在真的會暫存一份。
    expect(text).not.toMatch(/這一頁存不了/)
  })

  it('模型名稱一律問供應商，不可以寫死', () => {
    // 使用者隨手一試就撞到：
    //   The model `llama-3.3-70b-versatile` does not exist or you do not have access to it.
    //
    // 那是我從記憶裡寫進去的 id。模型會改名、會下架，各家帳號的權限也不同——而
    // 系統自己早就做對了（AI 輔助那一頁的清單是抓來的）。
    const script = read('assist.js')

    expect(script, '沒有去問供應商要模型清單').toContain("'/models'")
    // 程式面只斷言「有去問供應商」。assist.js 的註解裡引用了使用者實際看到的錯
    // 誤訊息，那是這條規則存在的理由，不該被自己的規則擋掉。寫死的 id 出現在文
    // 案裡才是他會撞到的東西。
    for (const source of PAGES.map(read)) {
      expect(source).not.toMatch(/llama-3\.3-70b/)
      expect(source).not.toMatch(/gpt-4o-mini/)
      expect(source).not.toMatch(/gemma-\d/)
    }
  })

  it('沒有選到模型就不給「直接問」 —— 有金鑰不等於能用', () => {
    // 原本這一條斷言的是 `if (!get(KEY) || !get(MODEL)) return`，也就是**整個框
    // 藏起來**。用意（沒選模型就不要送出一個一定會失敗的請求）是對的，但那個做法
    // 連帶把下面那一條擋掉了，所以改成驗用意：沒有金鑰或沒有模型的時候，不出現
    // 那個會直接送出請求的表單。
    const script = read('assist.js')

    expect(script).toMatch(/get\(KEY\).*get\(MODEL\)/s)
    expect(script, '沒有模型時仍然會掛上直接送出的表單').toMatch(/ai-go/)
  })

  it('沒有金鑰也要問得到 —— 卡住的人正是還沒有金鑰的那一個', () => {
    // 使用者：「移到最後，中間有疑問要問誰?」——AI 排在第一步不是因為它是功能，
    // 是因為**卡住的時候要有人問**。這一點我一開始判斷錯了。
    //
    // 但原本的做法有一個雞生蛋：mountAsk 開頭就 `if (!get(KEY)) return`，所以沒有
    // 金鑰的人**連「這裡可以問」都看不到**。而金鑰只存在 sessionStorage——關掉分頁
    // 就沒了。也就是說最需要幫忙的那個人（還沒申請金鑰、或分頁關過的人）看到的是
    // 一片空白。
    //
    // 解法不需要金鑰：這幾頁每一個 ai-ask 都帶著 data-context（他正在做什麼的描
    // 述）。把 context 加上他的問題整理成一段話、複製到剪貼簿，他貼到任何免費的
    // AI 網頁版就有答案。零註冊、零金鑰，而且對還沒決定要不要用 AI 的人也成立。
    const script = read('assist.js')

    expect(script, '沒有複製到剪貼簿的退路').toMatch(/clipboard|execCommand/)
    // 而且要講得出貼到哪裡去——「自己找一個 AI」對這個使用者等於沒說。
    expect(script).toMatch(/ChatGPT|Claude|Gemini/)

    for (const page of PAGES.filter((p) => p !== 'index.html')) {
      const source = read(page)
      if (!source.includes('id="ai-ask"')) continue
      expect(source, `${page} 的問 AI 區塊寫成要有金鑰才有用`).not.toMatch(
        /<summary>問 AI（用你第 1 步存的金鑰）<\/summary>/,
      )
    }
  })

  it('每一個抽屜都有自己的標題 —— 不然瀏覽器會畫出「詳細資料」', () => {
    // 使用者：「而且頁面圖示也沒有統一。」少一個 <summary>，瀏覽器就用它自己的
    // 預設標題和三角形，跟旁邊自訂的「＋」對不起來。
    for (const name of PAGES) {
      const drawers = read(name).split('<details').slice(1)
      drawers.forEach(function (chunk, index) {
        const head = chunk.slice(0, chunk.indexOf('</details>'))
        expect(head, `${name} 第 ${index + 1} 個抽屜沒有 summary`).toContain('<summary>')
      })
    }
  })

  it('金鑰不落地 —— 只留在這個分頁，關掉就沒了', () => {
    // 把 API 金鑰貼進網頁本來就是釣魚網站訓練人做的動作。這幾頁沒有後端、原始碼
    // 公開、金鑰只往他自己填的網址送——但至少不要留在硬碟上。
    const script = read('assist.js')

    expect(script).toContain('sessionStorage')
    expect(script, '不可以用 localStorage：那會留在硬碟上').not.toContain('localStorage')
  })

  it('第三頁是可以照著按的步驟，不是叫他去讀 GitHub', () => {
    // 使用者：「單純指點一個根本不知道資料庫是什麼的門外漢，要他去 github 看那一
    // 堆冷冰冰的檔案，根本無從做起，我就直接放棄。」
    const page = read('install.html')

    expect(page).toContain('render.com/deploy')
    expect(strip(page)).toContain('VITE_API_BASE_URL')
    expect(strip(page)).toContain('DATABASE_URL')
    // GitHub 只能是「還是不行」時的最後一條路，不是主要動線。
    const beforeTrouble = page.split('卡住了')[0]
    expect(beforeTrouble).not.toContain('DEPLOYMENT.md')
  })

  it('第三頁也給得出「跑在自己電腦上」那條路，而且那條路真的存在', () => {
    // 這一條原本只驗「頁面上有沒有寫 docker compose up」——而那句話當時是假的：
    // repo 裡根本沒有 compose 檔，照著做會直接失敗。測試通過，因為它驗的是文案，
    // 不是事實。跟先前把 render.com/deploy 寫進斷言是同一種錯。
    //
    // 現在驗兩件事：頁面上寫了，而且那個檔案在。實際跑得起來由人驗過一次
    // （容器起來、資料庫 ok、註冊是開的），這裡守的是「不要再指向不存在的東西」。
    const text = strip(read('install.html'))

    expect(text).toMatch(/docker compose up/)
    expect(text).toMatch(/localhost/)

    const compose = resolve(DOCS, '..', 'docker-compose.yml')
    expect(existsSync(compose), 'install.html 叫人跑 docker compose up，但沒有 compose 檔').toBe(
      true,
    )
    const yaml = readFileSync(compose, 'utf-8')
    // 頁面說開哪個埠，compose 就必須真的把那個埠開出來。
    //
    // 值從 5173 變成 8000，是因為後端現在直接供應前端——**一個服務、一個埠**，跟
    // 正式部署跑的是同一個東西。不變量沒變：頁面和 compose 不可以各說各話。
    expect(yaml).toContain('8000:8000')
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
      // 禁的是外部主機，不是同一個 repo 裡的相對檔案：assist.js 三頁共用，
      // 抄三份才是問題。
      expect(page, `${name} 抓了外部腳本`).not.toMatch(/<script[^>]+src=["']https?:/i)
      expect(page, `${name} 抓了外部樣式表`).not.toMatch(/<link[^>]+stylesheet[^>]*https?:/i)
      expect(page, `${name} 沒有 viewport`).toMatch(/<meta[^>]+viewport/i)
      // 行內樣式會讓三頁慢慢長出三套外觀。
      expect(page, `${name} 有行內樣式`).not.toMatch(/\sstyle="/)
    }
  })

  it('每一個外部連結都是 https', () => {
    // 這一頁教人去別人家貼金鑰和連線字串。http 的連結在那個情境下不只是不安全，
    // 是把「注意網址」這件事教反。
    for (const name of PAGES) {
      for (const [, url] of read(name).matchAll(/href="(https?:\/\/[^"]+)"/g)) {
        expect(url, `${name} 有非 https 的連結`).toMatch(/^https:\/\//)
      }
    }
  })

  it('一鍵部署的網址帶對參數 —— 少一個他會停在一個空表單前面', () => {
    // 這是連結裡唯一「壞掉但看起來正常」的部分：網址還在、頁面也打得開，只是
    // 少了 root-directory，Vercel 就會去建置整個 repo 而不是 frontend。
    //
    // 刻意不在這裡打網路。platform.openai.com 對機器人回 403（真人瀏覽器 200），
    // 所以一條會連線的檢查在 CI 上就是看別人臉色的紅燈。連結還活著這件事由每週
    // 排程去看，不擋 push。
    const page = read('install.html')

    const render = page.match(/href="(https:\/\/render\.com\/deploy[^"]*)"/)?.[1] ?? ''
    expect(render).toContain('repo=https://github.com/CoolAI-Studio/Stock-trading-app')

    // **只有一顆按鈕。**
    //
    // 後端現在直接供應前端，所以只要部署一次。原本這裡還檢查 Vercel 那顆的
    // root-directory 和 env 參數——那些不變量沒有被違反，是**那顆按鈕不該在這裡
    // 了**：兩顆按鈕、兩個網址、要對起來的一格 VITE_API_BASE_URL，全都是那個多
    // 出來的部署帶來的。
    expect(page, '還留著第二顆部署按鈕').not.toContain('vercel.com/new/clone')
  })

  it('把畫面另外放這條路還在 —— 拿掉的是要求，不是選擇', () => {
    // 通用化不等於只剩一種做法。想用 Vercel／Netlify／Cloudflare 的人照樣可以，
    // 而他需要知道的兩件事是那一格環境變數、和後端那邊要放行他的網址。
    const text = strip(read('install.html'))

    expect(text).toMatch(/VITE_API_BASE_URL/)
    expect(text, '分開放的話後端要放行那個網址，不然每一次請求都會被擋掉').toMatch(
      /CORS_ORIGINS/,
    )
  })

  it('要人去別人家點的步驟，都給得出找不到時的搜尋字', () => {
    // 這是這一輪唯一驗不了的一類：各家後台的實際點擊路徑。要真的有帳號才試得
    // 出來，而且那些介面本來就會改。
    //
    // 驗不了就不要假裝驗過——但也不能只寫「自己找找看」。給一個穩定的關鍵字，
    // 是介面改版之後仍然找得到那個東西的唯一辦法。
    for (const [name, vendor] of [
      ['database.html', 'Neon'],
      ['database.html', 'Supabase'],
      ['index.html', 'NVIDIA'],
      ['index.html', 'Groq'],
      ['install.html', 'Railway'],
    ] as const) {
      const drawer = read(name)
        .split('<details')
        .slice(1)
        .find((chunk) => chunk.slice(0, chunk.indexOf('</details>')).includes(vendor))
      expect(drawer, `${vendor} 沒有說明`).toBeTruthy()
      expect(drawer, `${vendor} 沒有給「找不到就找這個字」`).toMatch(/找不到/)
    }
  })

  it('收起來的時候要短 —— 使用者說第一版讓人視覺疲勞', () => {
    // 只算預設看得到的字。展開的細節不算：那是他自己按開的，而且那些字正是他做得
    // 完的原因。兩件事都要，所以分開量。
    //
    // 第三頁現在有兩條路（雲端／本機），而**他一次只看得到一條**——選了之後另一條
    // 就收起來。所以那一頁量的是「選擇 ＋ 其中一條路」，兩條各量一次，兩次都要在
    // 預算內。這比原本的算法**嚴格**：原本只要總和過關，現在是每一條路各自過關。
    for (const name of PAGES) {
      const page = read(name)
      const paths = pathsIn(page)
      const views = paths.length
        ? paths.map((keep) => paths.filter((p) => p !== keep).reduce(withoutPath, page))
        : [page]
      for (const view of views) {
        const size = visible(view).replace(/\s/g, '').length
        expect(size, `${name} 收起來還有 ${size} 個字元，太長了`).toBeLessThan(520)
      }
    }
  })

  it('而且展開之後真的有東西 —— 不是三個空抽屜', () => {
    for (const name of PAGES) {
      const drawers = read(name).match(/<details>/g)?.length ?? 0
      expect(drawers, `${name} 可展開的說明太少`).toBeGreaterThanOrEqual(2)
    }
  })
})

describe('引導：#47 的驗收條件，逐項守住', () => {
  it('本機和雲端是並排的選擇，不是塞在抽屜裡的補述', () => {
    // 票上的驗收條件第一項。並排不是版面美感：看 docker-compose.yml，本機那條路
    // 用的是 SQLite，**完全不需要外部資料庫**。所以這個選擇決定了這一整頁要不要
    // 做，而它必須在使用者開始做之前就看得到。
    const page = read('database.html')

    expect(visible(page), '本機那條路在收起來的時候看不到').toMatch(/自己的電腦|本機/)
    expect(visible(page), '雲端那條路在收起來的時候看不到').toMatch(/雲端/)
    // 票上指名要寫的那一件事：這個決定的真正差異。
    expect(visible(page), '沒說電腦關機會怎樣 —— 那是這個決定的全部重點').toMatch(
      /關機|睡著|沒開/,
    )
  })

  it('那個選擇要在申請步驟之前出現 —— 擺在後面就不是選擇了', () => {
    const page = read('database.html')
    const choiceAt = page.search(/自己的電腦/)
    const signupAt = page.indexOf('neon.tech')

    expect(choiceAt, '頁面上找不到本機那條路').toBeGreaterThan(-1)
    expect(signupAt, '頁面上找不到申請資料庫的步驟').toBeGreaterThan(-1)
    expect(choiceAt, '選擇出現在申請步驟後面').toBeLessThan(signupAt)
  })

  it('選了本機的人要被告知這一頁剩下的不用做', () => {
    // 原本沒有這句話。他看完兩張卡、選了「自己的電腦」，接下來整頁都是 Neon／
    // Supabase 手把手，而唯一的出口是「下一步：安裝 →」。一個不知道資料庫是什麼
    // 的人會以為那些步驟他也得做——一整頁白做的工，而且做到一半卡住就放棄了。
    const text = strip(read('database.html'))

    expect(text, '沒告訴選本機的人可以跳過').toMatch(/不用申請|不需要申請|可以跳過|不必做|跳到/)

    // 而且那句話必須是真的。跟 docker compose up 那一條同一個教訓：驗文案不驗事
    // 實，就會寫出一句通過測試的假話。
    const yaml = readFileSync(resolve(DOCS, '..', 'docker-compose.yml'), 'utf-8')
    expect(yaml, '頁面說本機不用申請資料庫，但 compose 指向外部資料庫').toMatch(
      /DATABASE_URL:\s*sqlite:/,
    )
  })

  it('說得出資料庫是唯一一件要去別人家拿的東西', () => {
    // CLAUDE.md 的第一條規則：app 生得出來的（加密金鑰、推播金鑰）就在設定頁給
    // 按鈕；真的生不出來的（資料庫在別人家的服務上）就老實說，不要假裝。
    //
    // 對一個不是工程師的人，「還有幾樣要這樣自己去弄？」是他決定要不要開始的依
    // 據。說不清楚，他會以為每一格空白都是一趟這樣的旅程。
    const text = strip(read('database.html'))

    expect(text, '沒說這是唯一一件要去別人家拿的東西').toMatch(/唯一/)
  })

  it('AI 不設定會怎樣，要說出「少掉什麼」和「什麼照常」', () => {
    // 票上的驗收條件第四項是「AI 是選配，**並說得出不設定會怎樣**」。
    //
    // 使用者來這個 app 的原因是提醒。他讀到「先跳過也行」的時候真正想知道的是
    // 「提醒還會不會動」，而答案是「會，一模一樣」。不寫出來他只能猜，而猜錯的
    // 方向是放棄。
    const text = strip(read('index.html'))

    expect(text, '沒說少掉的是哪些功能').toMatch(/產生策略|幫你寫|問它|問 AI/)
    expect(text, '沒說提醒本身照常 —— 而那才是他來的原因').toMatch(/照常|不受影響|一樣/)
  })
})

/**
 * 「雲端還是本機」要在第一步之前問，不是收在最後面。
 *
 * 使用者實際走了一遍之後說的：「使用本機端還是需要這個嗎？沒有不需要真的只在本機
 * 跑的版本？」——有，而且完全不用雲端。可是頁面把五個雲端步驟從上到下排好，「我要
 * 跑在自己的電腦上」是最後一個折疊區塊。
 *
 * 決定發生在第 1 步，選項卻在最底下：想跑本機的人要嘛找不到，要嘛照著 Render 那幾
 * 步做完才發現自己不需要。他接著說的就是這一條測試要守的東西——「那選擇本機，你這
 * 個就應該隱藏起來才是」。
 *
 * 這跟 #46（「部署你自己的一份」直接跳 Render，把選擇拿走了）是同一類問題，往下一
 * 層：選擇還在，只是排在他做完決定之後。
 */
describe('第三頁：雲端還是本機，一開始就選', () => {
  const mount = () => mountPage('install.html')

  const stepsFor = pathBlocks
  const choice = pathChoice

  it('選擇排在步驟之前', () => {
    const page = read('install.html')

    expect(page.indexOf('data-path-choice')).toBeGreaterThan(-1)
    expect(page.indexOf('data-path-choice')).toBeLessThan(page.indexOf('<ol class="steps"'))
  })

  it('兩條路都是真的步驟，不是折疊起來的附註', () => {
    // 收在 <details> 裡的東西，等於他要先知道有這個選項才會去按開。
    mount()

    expect(stepsFor('cloud').length).toBeGreaterThan(0)
    expect(stepsFor('local').length).toBeGreaterThan(0)
    for (const step of [...stepsFor('cloud'), ...stepsFor('local')]) {
      expect(step.closest('details')).toBeNull()
    }
  })

  it('選了本機，雲端那幾步就不見', () => {
    mount()

    // 先確認東西真的在。少了這兩行，選擇鈕不存在時 `?.click()` 什麼都不做、
    // `[].every(...)` 又是 true，整條測試會在「還沒實作」的狀態下就通過。
    expect(choice('local')).not.toBeNull()
    expect(stepsFor('cloud').length).toBeGreaterThan(0)

    choice('local')?.click()

    expect(stepsFor('cloud').every((step) => step.hidden)).toBe(true)
    expect(stepsFor('local').some((step) => step.hidden)).toBe(false)
  })

  it('選了雲端，本機那幾步就不見', () => {
    mount()

    expect(choice('cloud')).not.toBeNull()
    expect(stepsFor('local').length).toBeGreaterThan(0)

    choice('cloud')?.click()

    expect(stepsFor('local').every((step) => step.hidden)).toBe(true)
    expect(stepsFor('cloud').some((step) => step.hidden)).toBe(false)
  })

  it('還沒選之前兩條都看得到，JS 沒跑起來也是', () => {
    // 失敗的方向要選對：JS 掛掉的時候寧可兩條路都攤開，也不要一步都看不到。原始檔
    // 裡不可以先把任何一條藏起來——藏起來的話，沒有 JS 的瀏覽器上那條路就消失了。
    const page = read('install.html')
    expect(page).not.toMatch(/data-path="(cloud|local)"[^>]*\shidden/)

    mount()
    const both = [...stepsFor('cloud'), ...stepsFor('local')]
    expect(both.length).toBeGreaterThan(1)
    expect(both.some((step) => step.hidden)).toBe(false)
  })

  it('叫他下載，就要給得出下載的連結', () => {
    // 使用者：「我沒有看到哪裡可以下載，應該多一個連結去下載。」原本寫的是「GitHub
    // 頁面上綠色的 Code → Download ZIP」——那是一段路線指示，不是一個連結，而他要
    // 先自己找到那個 GitHub 頁面。
    //
    // CLAUDE.md：**永遠不要叫他去別的地方拿一個值。** 拿得到的就直接給。
    //
    // 指向 stable 不是 main：那是 CI 綠燈、部署送達、線上健康之後才前進的那一個分支，
    // 也就是我們自己的實例已經跑起來而且活著的那一版。
    mountPage('install.html')

    const link = document.querySelector<HTMLAnchorElement>('a[href$=".zip"]')
    expect(link, '本機那條路沒有可以按的下載連結').not.toBeNull()
    expect(link?.getAttribute('href')).toContain('/archive/refs/heads/stable.zip')
    expect(link?.closest('[data-path="local"]'), '下載連結不在本機那條路上').not.toBeNull()
  })

  it('兩張卡片都要說得出自己的條件，不能只有一張有', () => {
    // 雲端那張寫著「要一個免費資料庫」，本機那張原本只寫「什麼都不用填」——聽起來
    // 完全沒有條件。可是它有，而且是這兩條路裡唯一會把他的電腦弄到當掉的那一個。
    //
    // 條件要在**做決定的那一刻**看得到，不是選完之後才在步驟裡出現。
    mountPage('install.html')

    const cards = Array.from(document.querySelectorAll<HTMLElement>('.path-choice .card'))
    expect(cards).toHaveLength(2)
    for (const card of cards) {
      expect(card.textContent, `這張卡片沒有說出它要什麼：${card.textContent}`).toMatch(
        /資料庫|GB/,
      )
    }
  })

  it('本機那條路要說出它吃多少記憶體 —— 那是他唯一會踩到的硬體條件', () => {
    // 這一條是維護者自己在這台機器上踩出來的：他去試「跑在自己的電腦上」那條路，
    // 機器當掉了兩次。量到的原因是 Docker 背後那個 WSL 虛擬機——**Docker Desktop 關
    // 掉、每一個 distro 都停掉之後它還握著 2.8 GB**，而 `wsl --shutdown` 讓可用記憶
    // 體從 5.7 GB 跳到 10.9 GB。
    //
    // 那一頁原本只說「約五分鐘，要先裝 Docker Desktop」。雲端那條路的條件寫得很清楚
    // （要一個免費資料庫），本機這條的條件卻是隱形的——而它是這兩條路裡唯一會把他的
    // 電腦弄到當掉的那一條。
    //
    // 要在**不用展開**的地方講：一個藏在折疊區塊裡的硬體需求，等於沒有講。
    mountPage('install.html')

    const step = pathBlocks('local')[0]
    expect(step?.textContent).toMatch(/GB/)

    const said = Array.from(step.querySelectorAll<HTMLElement>('p, li')).find((el) =>
      /GB/.test(el.textContent ?? ''),
    )
    expect(said, '沒有任何一段講到記憶體').toBeDefined()
    expect(said?.closest('details'), '記憶體需求被收在折疊區塊裡，等於沒有講').toBeNull()
  })

  it('而且要說得出不用的時候怎麼把記憶體要回來', () => {
    // 「需要 8 GB」單獨講，對一台只有 8 GB 的機器等於一句壞消息。真正有用的是下一
    // 句：關掉 Docker Desktop（Windows 上再一句 wsl --shutdown）就還你，而且映像檔
    // 和設定都還在——是停用，不是刪除。
    mountPage('install.html')

    const step = pathBlocks('local')[0]
    expect(step?.textContent).toMatch(/wsl --shutdown/)
    expect(step?.textContent).toMatch(/關掉|停用/)
  })

  it('本機那條路上該有的字還在，而且不用展開就看得到', () => {
    mount()

    const text = stepsFor('local')
      .map((step) => step.textContent ?? '')
      .join(' ')
    expect(text).toMatch(/docker compose up/)
    expect(text).toMatch(/localhost:8000/)
    // 誠實的代價：電腦睡著，盯盤就停了。這句話不可以在改版時掉。
    expect(text).toMatch(/關機|睡著/)
  })
})

/**
 * 第一頁和第二頁也要「選了才看細節」。
 *
 * 使用者看完第三頁的兩選一之後說的：「像要不要用 AI 這邊是可以點選的，選不要才跳不
 * 要的描述及選項出來，選要的話，跳出 AI 相關的資訊及選項出來，當然若中間後悔可以回
 * 來重選。資料頁面也是一樣，只顯示他要看的資訊就好，這樣更容易懂。」
 *
 * 原本兩頁都是「兩張卡片並排說明差別，然後把兩條路的細節一路往下攤開」——決定做完
 * 了，可是螢幕上還有另一條路的每一個字。第一頁尤其明顯：選了「不用 AI」的人，底下
 * 還是四家供應商的申請步驟。
 *
 * 這一組跟第三頁那一組守的是同一件事，只是預設不同（見底下 DEFAULT_VISIBLE 那段
 * 註解），所以合起來跑同一份契約。
 */
describe('三頁都是「選了才看細節」', () => {
  /**
   * 選之前看得到什麼。
   *
   * 第一、二頁是 none：那兩頁的兩條路是**互斥的說明**，選了才看細節正是使用者要的。
   *
   * 第三頁是 both，而且是刻意的：那一頁的路是一個編號步驟清單，把兩條都藏起來的話，
   * 清單會從「打開你的網址」開始編號 1——一份缺了前兩步、而且看不出缺了的步驟表，
   * 比多看幾行字糟得多。
   */
  const CASES = [
    { page: 'index.html', paths: ['no-ai', 'ai'], defaultVisible: 'none' },
    { page: 'database.html', paths: ['local', 'cloud'], defaultVisible: 'none' },
    { page: 'install.html', paths: ['cloud', 'local'], defaultVisible: 'both' },
  ] as const

  for (const { page, paths, defaultVisible } of CASES) {
    describe(page, () => {
      const [first, second] = paths

      it('兩條路都選得動，而且細節真的在頁面上', () => {
        mountPage(page)

        for (const path of paths) {
          expect(pathChoice(path), `${path} 沒有可以選的東西`).not.toBeNull()
          expect(pathBlocks(path).length, `${path} 沒有任何細節`).toBeGreaterThan(0)
        }
      })

      it(`選之前是「${defaultVisible}」`, () => {
        mountPage(page)

        const total = paths.flatMap(pathBlocks).length
        // 沒有這一行，還沒實作的時候 `[]` 的長度是 0，`expect(0).toBe(0)` 就過了。
        expect(total, '這一頁根本沒有任何一條路').toBeGreaterThan(0)
        const shown = paths.flatMap(pathBlocks).filter((el) => !el.hidden).length
        if (defaultVisible === 'none') expect(shown).toBe(0)
        else expect(shown).toBe(total)
      })

      it('選了一條，另一條就收起來', () => {
        mountPage(page)
        expect(pathBlocks(second).length).toBeGreaterThan(0)

        pathChoice(first)?.click()

        expect(pathBlocks(first).every((el) => el.hidden)).toBe(false)
        expect(pathBlocks(second).every((el) => el.hidden)).toBe(true)
      })

      it('後悔了可以回來重選', () => {
        // 選擇的那兩張卡片不可以跟著被收走——不然他改變主意就只能重新整理。
        mountPage(page)

        pathChoice(first)?.click()
        pathChoice(second)?.click()

        expect(pathBlocks(second).every((el) => el.hidden)).toBe(false)
        expect(pathBlocks(first).every((el) => el.hidden)).toBe(true)
      })

      it('選了哪一個，畫面上看得出來', () => {
        // 不然回頭的時候他不知道自己現在在哪一條路上。
        mountPage(page)

        pathChoice(first)?.click()

        expect(pathChoice(first)?.closest('.card')?.classList.contains('chosen')).toBe(true)
        expect(pathChoice(second)?.closest('.card')?.classList.contains('chosen')).toBe(false)
      })

      it('沒有 JS 的時候什麼都不會不見', () => {
        // 失敗的方向要選對：JS 掛掉的時候寧可全部攤開，也不要一片空白。藏起來這件事
        // 只能由 JS 做，原始檔裡不可以先藏。
        expect(read(page)).not.toMatch(/data-path="[^"]+"[^>]*\shidden/)
      })
    })
  }
})

describe('第三頁：雲端那條路會睡著，而睡著的時候提醒不會送出', () => {
  /**
   * Render 的免費方案，**沒有外來流量 15 分鐘就休眠**。休眠 = 行程結束 = 盯盤迴圈停
   * 掉 = 那段時間裡穿價、跌破均線、觸發停損，一則提醒都不會送出。
   *
   * 而這件事在他真的會讀的那一頁上，原本只寫成一句「等一下重新整理」——講的是冷啟動
   * 慢，不是**提醒停擺**。照著這一頁走完的人，會有一份看起來完全正常、實際上一天大
   * 部分時間都沒在盯盤的部署。
   *
   * 這是這個產品唯一那句承諾（「想在手機上收到股票提醒」）失效得最徹底的一種，而它
   * 不在流程裡。DEPLOYMENT.md 第 4 節早就寫著怎麼做——但那份文件目標使用者不會打開，
   * 他打開的是這一頁。
   *
   * app 裡也有一半的答案（系統狀態頁偵測得到「這個服務有 N 小時沒有在盯盤」），但那
   * 是**事後**：他已經漏掉那幾個小時的提醒了。這一頁是事前。
   */
  /** 選了雲端之後看得到的步驟文字。`collapsed` 代表只算不用點開就看得到的。 */
  const cloudOnly = ({ collapsed = false } = {}) => {
    mountPage('install.html')
    pathChoice('cloud')?.click()
    const steps = Array.from(document.querySelectorAll<HTMLElement>('li')).filter(
      (li) => !li.hidden && !li.closest('[hidden]'),
    )
    if (collapsed) {
      for (const drawer of document.querySelectorAll('details')) drawer.remove()
    }
    return steps.map((li) => li.textContent ?? '').join(' ')
  }

  it('後果要在他不用點開就看得到的地方', () => {
    // 收在抽屜裡等於他要先知道有這回事才會去按開，而這件事的重點正是他不知道。
    const text = cloudOnly({ collapsed: true })

    expect(text).toMatch(/休眠|睡著/)
    // 重點是提醒停擺，不是開啟變慢——原本那句「等一下重新整理」講的是後者。
    expect(text).toMatch(/提醒.{0,12}(不會|收不到|送不出|停)/)
  })

  it('說得出怎麼辦，而且那個辦法是照著按就好的', () => {
    // 這一段可以收在抽屜裡：他讀到後果之後才需要它，而這一頁的預算是有限的
    // （「收起來的時候要短」那條測試）。
    const text = cloudOnly()

    expect(text).toMatch(/healthz/)
    expect(text).toMatch(/5 分鐘|五分鐘/)
  })

  it('本機那條路不用聽這一段', () => {
    // 他自己的電腦不會因為沒人連就把程式關掉。講了只是多一個他做不到也不用做的
    // 步驟，而這一頁的預算是有限的。
    mountPage('install.html')
    pathChoice('local')?.click()

    const visibleText = Array.from(document.querySelectorAll<HTMLElement>('li'))
      .filter((li) => !li.hidden && !li.closest('[hidden]'))
      .map((li) => li.textContent ?? '')
      .join(' ')

    expect(visibleText).not.toMatch(/healthz/)
  })
})
