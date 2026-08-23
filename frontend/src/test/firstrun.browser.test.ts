/**
 * 全新使用者的第一次，走一遍，用真的瀏覽器。
 *
 * 為什麼需要它：這個專案已經有 `deploy_smoke.py` 和 CI 的 `first-deploy` job，
 * 兩者都用全空環境啟動真的容器——但它們只問「起得來嗎、端點回得對嗎」。**沒有
 * 人看過那些畫面。** 而第一次真的看的時候，一眼就有四件事是壞的：
 *
 *   1. 登入頁的第一句話印出未渲染的 markdown（「你現在建立的是**第一個…**帳號」）
 *   2. 資料庫連不上時，畫面貼出 Python traceback（含 site-packages 路徑）
 *   3. 「步驟 2」連續出現三次
 *   4. 資料庫那一格只有一段散文，一句話裡塞四條路
 *
 * 四件都不是後端的錯，四件都不會讓任何既有的關卡變紅。單元測試看不到（它們各自
 * 只看一個元件），jsdom 看不到（沒有真的導向、沒有真的版面），而 `first-deploy`
 * 只看 HTTP 狀態碼。
 *
 * 這一關走的是使用者實際走的順序：
 *
 *   全空的部署 → 根網址 → /setup（他該填什麼、能自己生的有沒有按鈕）
 *   → 填好之後重開 → 還沒有擁有者 → 建立第一個帳號 → 引導
 *
 * 斷言刻意都是**使用者看得到的東西**：畫面上的字、按得到的按鈕、去得了的網址。
 * 不斷言 class、不斷言座標——那些會隨改版漂，而漂掉的紅燈會被當成雜訊，然後這
 * 一關就等於不存在了。
 */

import { spawn, type ChildProcess } from 'node:child_process'
import { existsSync } from 'node:fs'
import { readFile, rm } from 'node:fs/promises'
import { createServer, type Server } from 'node:http'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium, type Browser, type Page } from 'playwright'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'

const FRONTEND = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const BACKEND = resolve(FRONTEND, '..', 'backend')
const DIST = join(FRONTEND, 'dist-firstrun')
const API_PORT = 8331
const WEB_PORT = 8332
const WEB_ORIGIN = 'http://127.0.0.1:' + WEB_PORT
const API_ORIGIN = 'http://127.0.0.1:' + API_PORT

// venv 在本機，CI 上是系統的 python。兩邊都要跑得起來：一個只有 CI 跑得動的
// 關卡，在推之前等於不存在。
const PYTHON = (() => {
  const onWindows = join(BACKEND, 'venv', 'Scripts', 'python.exe')
  const onPosix = join(BACKEND, 'venv', 'bin', 'python')
  if (existsSync(onWindows)) return onWindows
  if (existsSync(onPosix)) return onPosix
  return process.env.PYTHON || 'python'
})()

// vite 的 JS 進入點，用 node 直接跑。npx 在 Windows 上是一個 .cmd，而 spawn
// 一個 .cmd 需要 shell，那又會把引號和空白的處理交給兩種不同的殼層。
const VITE = join(FRONTEND, 'node_modules', 'vite', 'bin', 'vite.js')

/** 一份全空部署的環境：什麼都沒填，而且看得出自己在雲端平台上。 */
const BLANK: Record<string, string> = {
  RENDER: 'true',
  RENDER_EXTERNAL_URL: 'https://my-copy.onrender.com',
  CORS_ORIGINS: WEB_ORIGIN,
}

/** 他照著設定頁按了三顆「產生」、貼回去之後的環境。 */
const FILLED: Record<string, string> = {
  CORS_ORIGINS: WEB_ORIGIN,
  PUBLIC_BASE_URL: API_ORIGIN,
  DATABASE_URL: 'sqlite:///./firstrun_walk.db',
  JWT_SECRET: 'a-long-random-value-for-this-walk-only-not-a-real-secret-0001',
  TV_WEBHOOK_SECRET: 'another-long-random-value-for-this-walk-only-0002',
}

const OWNER_EMAIL = 'first-owner@example.com'
const OWNER_PASSWORD = 'a-long-enough-passphrase-1234'

function runToCompletion(
  command: string,
  args: string[],
  env: Record<string, string>,
  cwd = BACKEND,
) {
  return new Promise<string>((ok, fail) => {
    const child = spawn(command, args, { cwd, env: { ...process.env, ...env }, shell: false })
    let output = ''
    child.stdout.on('data', (chunk) => (output += chunk))
    child.stderr.on('data', (chunk) => (output += chunk))
    child.on('error', fail)
    child.on('close', (code) => (code === 0 ? ok(output) : fail(new Error(output))))
  })
}

let api: ChildProcess | null = null

async function stopApi() {
  if (!api) return
  const dying = api
  api = null
  dying.kill()
  await new Promise((r) => setTimeout(r, 800))
}

async function startApi(env: Record<string, string>) {
  await stopApi()
  api = spawn(
    PYTHON,
    ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(API_PORT),
      '--log-level', 'warning'],
    {
      cwd: BACKEND,
      // 一個乾淨的環境，不是繼承來的。開發者機器上殘留的變數會決定這一關看到
      // 什麼，而那正是「拿自己已登入的狀態當設計依據」的同一個錯，只是換成環
      // 境變數。
      env: {
        PATH: process.env.PATH ?? '',
        SYSTEMROOT: process.env.SYSTEMROOT ?? '',
        PYTHONIOENCODING: 'utf-8',
        ...env,
      },
      shell: false,
    },
  )

  const deadline = Date.now() + 90_000
  while (Date.now() < deadline) {
    try {
      await fetch(API_ORIGIN + '/healthz')
      return
    } catch {
      await new Promise((r) => setTimeout(r, 500))
    }
  }
  throw new Error('後端沒有在 90 秒內起來')
}

let web: Server | null = null
let browser: Browser

beforeAll(async () => {
  // 用「會裝出去的那一份」前端，而且指著這一關自己起的後端。
  await runToCompletion(
    process.execPath,
    [VITE, 'build', '--outDir', DIST, '--emptyOutDir'],
    { VITE_API_BASE_URL: API_ORIGIN },
    FRONTEND,
  )

  // 單頁應用：認不得的路徑一律回 index.html，否則直接開 /guide 是 404。
  web = createServer((request, response) => {
    const path = (request.url ?? '/').split('?')[0]
    const wanted = join(DIST, path === '/' ? 'index.html' : path.slice(1))
    const file = existsSync(wanted) ? wanted : join(DIST, 'index.html')
    readFile(file)
      .then((body) => {
        const type = file.endsWith('.js')
          ? 'text/javascript'
          : file.endsWith('.css')
            ? 'text/css'
            : 'text/html'
        response.writeHead(200, { 'content-type': type })
        response.end(body)
      })
      .catch(() => response.writeHead(500).end())
  })
  await new Promise<void>((ok) => web!.listen(WEB_PORT, '127.0.0.1', ok))

  browser = await chromium.launch()
}, 240_000)

afterAll(async () => {
  await browser?.close()
  await new Promise<void>((ok) => (web ? web.close(() => ok()) : ok()))
  await stopApi()
})

async function open(): Promise<{ page: Page; errors: string[] }> {
  const page = await browser.newPage({ viewport: { width: 1280, height: 950 } })
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(String(error)))
  return { page, errors }
}

describe('一份全空的部署，第一次被打開', () => {
  beforeAll(async () => {
    await startApi(BLANK)
  }, 120_000)

  it('根網址不會丟一個空儀表板給他，會帶他去設定頁', async () => {
    const { page, errors } = await open()
    await page.goto(WEB_ORIGIN)
    await page.waitForURL(/\/setup/, { timeout: 30_000 })

    expect(await page.locator('h1').first().innerText()).toContain('設定')
    expect(errors).toEqual([])
    await page.close()
  })

  it('app 生得出來的值就給按鈕 —— 不要叫他回自己的電腦上跑腳本', async () => {
    const { page } = await open()
    await page.goto(WEB_ORIGIN + '/setup')
    await page.waitForSelector('text=DATABASE_URL', { timeout: 30_000 })

    const buttons = await page.locator('button:visible').allInnerTexts()
    expect(buttons.filter((text) => text.includes('產生')).length).toBeGreaterThanOrEqual(3)
    await page.close()
  })

  it('資料庫那一格給的是方案，不是一段話', async () => {
    // 而且這一頁是**雲端使用者唯一能做這個選擇的地方**：資料庫還沒接上的時候
    // 整個 app 是鎖住的，他連帳號都還沒有，走不到登入之後的設定引導。
    const { page } = await open()
    await page.goto(WEB_ORIGIN + '/setup')
    await page.waitForSelector('text=DATABASE_URL', { timeout: 30_000 })

    expect(await page.locator('body').innerText()).toContain('就跑在自己的電腦或自己的機器上')
    expect(await page.locator('a[href="https://neon.tech"]').count()).toBeGreaterThan(0)
    await page.close()
  })

  it('畫面上沒有沒被渲染的 markdown', async () => {
    const { page } = await open()
    await page.goto(WEB_ORIGIN + '/setup')
    await page.waitForSelector('text=DATABASE_URL', { timeout: 30_000 })

    expect(await page.locator('body').innerText()).not.toMatch(/\*\*/)
    await page.close()
  })

  it('也沒有 Python 的 traceback 或某個人硬碟上的路徑', async () => {
    // 設定頁寫著「對方回的是：」，而它一度接的是 alembic stderr 的最後八行——
    // sqlalchemy 的內部檔名，加上這台機器上的絕對路徑。
    //
    // 老實說這一條覆蓋到哪裡：它守的是「**任何**時候都不要有 traceback 出現在
    // 這一頁」。真正產生那段文字的路徑（開機遷移失敗 → DATABASE_MIGRATION_ERROR）
    // 需要一個連不上的資料庫和 start.py 的完整開機，那在這裡起得動、卻停不乾淨
    // ——start.py 會再生一個 uvicorn 子行程。那一段由
    // tests/test_a_wrong_database_url_still_leaves_a_page_to_read.py 用單元測試
    // 蓋住（readable_reason 的輸入輸出），這裡蓋的是它畫出來的樣子。
    const { page } = await open()
    await page.goto(WEB_ORIGIN + '/setup')
    await page.waitForSelector('text=DATABASE_URL', { timeout: 30_000 })
    const body = await page.locator('body').innerText()

    expect(body).not.toContain('site-packages')
    expect(body).not.toContain('Traceback')
    expect(body).not.toMatch(/File "/)
    await page.close()
  })

  it('同一個步驟只標一次', async () => {
    // 三把金鑰是同一個階段，數字沒有錯——但讀的人問的是「我到底在第幾步」，而
    // 畫面一度回答他三次一樣的數字。
    const { page } = await open()
    await page.goto(WEB_ORIGIN + '/setup')
    await page.waitForSelector('text=DATABASE_URL', { timeout: 30_000 })

    const labels = await page.locator('span').filter({ hasText: /^步驟 \d+$/ }).allInnerTexts()
    expect(labels.length).toBeGreaterThan(0)
    expect(labels.length).toBe(new Set(labels).size)
    await page.close()
  })
})

describe('設定填好之後，他要建立第一個帳號', () => {
  beforeAll(async () => {
    await stopApi()
    // 每一次都從「還沒有任何帳號」開始。上一次跑留下的資料庫裡已經有擁有者，
    // 註冊就是關的——而這一關要走的正是註冊那一步。留著它，這一關會在第二次跑
    // 的時候紅，紅的原因跟程式碼無關。
    await rm(join(BACKEND, 'firstrun_walk.db'), { force: true })
    const key = (
      await runToCompletion(
        PYTHON,
        ['-c', 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'],
        {},
      )
    ).trim()
    const env = { ...FILLED, SECRET_ENCRYPTION_KEY: key }
    await runToCompletion(PYTHON, ['-m', 'alembic', 'upgrade', 'head'], env)
    await startApi(env)
  }, 180_000)

  it('還沒有擁有者的時候，登入頁給的是「建立帳號」', async () => {
    // 安裝的最後一步，而它一度整段不存在：設定頁把人指去 DEPLOYMENT.md，而那
    // 份文件教的是改環境變數加 curl。
    const { page, errors } = await open()
    await page.goto(WEB_ORIGIN + '/login')
    await page.waitForSelector('button:has-text("建立帳號")', { timeout: 30_000 })

    // 而且那句話要讀得下去。它一度是
    //   「你現在建立的是**第一個也是唯一一個**帳號」
    // ——兩坨星號原樣印出來，在全新使用者看到的第一個畫面的第一句話裡。JSX 不是
    // markdown，而沒有任何既有的關卡會為了這件事變紅。
    expect(await page.locator('body').innerText()).not.toMatch(/\*\*/)
    expect(errors).toEqual([])
    await page.close()
  })

  it('建完直接進得去，而且第一個畫面不是一個空儀表板', async () => {
    const { page, errors } = await open()
    await page.goto(WEB_ORIGIN + '/login')
    await page.waitForSelector('button:has-text("建立帳號")', { timeout: 30_000 })

    await page.locator('input[type=email]').first().fill(OWNER_EMAIL)
    const passwords = page.locator('input[type=password]')
    for (let i = 0; i < (await passwords.count()); i += 1) {
      await passwords.nth(i).fill(OWNER_PASSWORD)
    }
    await page.locator('button:has-text("建立帳號")').click()

    await page.waitForURL(/\/welcome|\/guide/, { timeout: 30_000 })
    expect(errors).toEqual([])
    await page.close()
  })

  it('帳號建好之後，登入頁改成指路給下一個陌生人', async () => {
    // 註冊在這一份上關掉了（一份部署一個擁有者），但註冊那條路不該消失——它通往
    // 的是「部署你自己那一份」。使用者的話：「不然每次都用我的流量。」
    //
    // 這一條跑在建立帳號那一條之後，所以它看到的正是一個誤闖進來的陌生人會看到
    // 的東西：已經有擁有者的登入頁。
    const { page, errors } = await open()
    await page.goto(WEB_ORIGIN + '/login')
    await page.waitForSelector('a:has-text("看怎麼自己部署一份")', { timeout: 30_000 })

    // **不可以直接跳到某一家的部署按鈕。** 第一版指的是 render.com/deploy，等於
    // 替他決定了要用哪一家——而「不要綁死廠商」是這個專案最早提出的三個需求之
    // 一。它要的是三樣東西，不是三個品牌，而且三樣都可以跑在他自己的電腦上。
    const href = await page.locator('a:has-text("看怎麼自己部署一份")').getAttribute('href')
    expect(href).not.toContain('render.com/deploy')
    expect(href).toContain('github.com')
    expect(await page.locator('body').innerText()).toMatch(/自己的電腦|自己的機器/)
    // 而且不要留一顆按了必定失敗的註冊鈕。
    expect(await page.locator('button:has-text("建立帳號")').count()).toBe(0)
    expect(errors).toEqual([])
    await page.close()
  })

  it('設定引導的三格都在', async () => {
    const { page } = await open()
    await page.goto(WEB_ORIGIN + '/login')
    await page.waitForSelector('input[type=email]', { timeout: 30_000 })
    await page.locator('input[type=email]').first().fill(OWNER_EMAIL)
    await page.locator('input[type=password]').first().fill(OWNER_PASSWORD)
    await page.locator('button:has-text("登入")').click()
    await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 30_000 })

    await page.goto(WEB_ORIGIN + '/guide')
    await page.waitForSelector('[role=tab]', { timeout: 30_000 })
    const tabs = (await page.locator('[role=tab]').allInnerTexts()).join(' ')

    expect(await page.locator('[role=tab]').count()).toBe(3)
    expect(tabs).toMatch(/資料庫/)
    expect(tabs).toMatch(/AI/)
    expect(tabs).toMatch(/通知/)
    await page.close()
  })
})
