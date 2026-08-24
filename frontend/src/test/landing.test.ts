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

  it('沒有選到模型就不給問 —— 有金鑰不等於能用', () => {
    const script = read('assist.js')

    expect(script).toMatch(/if \(!get\(KEY\) \|\| !get\(MODEL\)\) return/)
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
    expect(page).toContain('vercel.com/new')
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
    // 頁面說開 localhost:5173，compose 就必須真的把那個埠開出來。
    expect(yaml).toContain('5173:5173')
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

    const vercel = page.match(/href="(https:\/\/vercel\.com\/new\/clone[^"]*)"/)?.[1] ?? ''
    expect(vercel).toContain('repository-url=https://github.com/CoolAI-Studio/Stock-trading-app')
    expect(vercel, 'Vercel 會去建置整個 repo 而不是 frontend').toContain('root-directory=frontend')
    expect(vercel, '不問這一格，前端就不知道後端在哪').toContain('env=VITE_API_BASE_URL')
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
