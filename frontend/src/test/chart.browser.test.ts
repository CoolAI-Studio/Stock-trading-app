/**
 * 這一支開真的 Chromium，用真的滑鼠拖曳這張圖。
 *
 * 為什麼需要它，有一次實際的紀錄：`e8124ce` 修「圖表往前拉是一片空白」，十條
 * 測試全綠、CI 四個 job 全綠、部署驗證通過，而使用者拉下去**畫布紋風不動**。
 * 原因是 `fixLeftEdge: true` 加上 `fitContent()`：可見範圍等於全部載入的 K
 * 棒，左邊已經沒有東西，函式庫就把捲動夾死。
 *
 * 那件事**沒有任何一個既有關卡看得到**：
 *
 * - PriceChart.test.tsx 把繪圖庫換成手寫的替身，而替身的 `fitContent` 是一個
 *   空函式，它永遠不會表現出「拒絕移動」。
 * - PriceChart.realLibrary.test.ts 用真貨，但在 jsdom 裡——真函式庫要等
 *   animation frame 才套用版面，jsdom 不會自己跑，所以互動測不到。
 * - `tsc`、`oxlint`、`vite build` 都不會對「這個選項組合的行為」有意見。
 *
 * 只有真的瀏覽器看得到。這裡量到的對照（同一份設定，只差初始視角）：
 *
 *     fitContent()          打開 from 0   拖六次後 from 0     沒有動
 *     最近 120 根            打開 from 180 拖六次後 from 0     一路拉到最舊
 *
 * 斷言刻意只看**函式庫自己算出來的 logical range**，不看像素、不看座標：
 * Playwright 換版、字型不同、預設視窗大小改了都會讓像素漂，而漂掉的紅燈會被
 * 當成雜訊，然後這一關就等於不存在了。
 */

import { chromium, type Browser, type Page } from 'playwright'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { INITIAL_VISIBLE_BARS, LOAD_MORE_WITHIN_BARS } from '../lib/chartViewport.ts'

// 從 node_modules 讀，不是從 CDN 抓：這一關要問的是「我們會裝出去的那一份函式
// 庫」，而不是網路上今天的那一份。IIFE 版本直接注入頁面，所以不需要起一個
// HTTP server——那會是另一個跟圖表無關、但會讓 CI 變紅的東西。
// 走套件的進入點再往旁邊拿：package.json 的 `exports` 不開放直接指 dist 裡的
// 檔案，而寫死一段相對路徑會在套件改版換目錄時安靜地找不到。
const LIBRARY = readFileSync(
  join(
    dirname(fileURLToPath(import.meta.resolve('lightweight-charts'))),
    'lightweight-charts.standalone.production.js',
  ),
  'utf-8',
)

const BARS = 300

let browser: Browser

beforeAll(async () => {
  browser = await chromium.launch()
}, 60_000)

afterAll(async () => {
  await browser?.close()
})

/** 開一張圖，回傳「打開時的可見範圍」「拖到底之後的可見範圍」「全程最小的 from」。
 *
 * `initialView` 是唯一的變數，因為它就是這一關存在的理由。
 */
/** 等版面真的套用完。
 *
 * lightweight-charts 把 fitContent() 和 setVisibleLogicalRange() 排進下一個
 * animation frame 才生效，所以呼叫完立刻讀 getVisibleLogicalRange()，讀到的是**上
 * 一個**狀態。在我的機器上剛好都來得及，在忙碌的 CI runner 上就不一定——
 * f8a6825 那一次 chart job 紅在
 *
 *     expected 150 to be close to +0
 *
 * 150 正是「還沒套用、仍是預設視角」的值。下一次推送又綠了，也就是說它是抖的。
 *
 * 等兩個 frame，不是等一個：第一個 frame 是套用，第二個才保證那次套用已經反映在
 * 讀得到的狀態上。也不用固定毫秒——那只是把同一個賭注換一個賠率。
 */
async function afterLayout(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((done) => {
        requestAnimationFrame(() => requestAnimationFrame(() => done()))
      }),
  )
}

async function dragToTheOldest(initialView: 'fitContent' | 'recent') {
  const page = await browser.newPage({ viewport: { width: 1000, height: 600 } })
  const pageErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(String(error)))

  await page.setContent('<div id="chart" style="width:900px;height:460px"></div>')
  await page.addScriptTag({ content: LIBRARY })

  await page.evaluate(
    ({ bars, initialVisible, view }) => {
      const lib = (window as never as { LightweightCharts: never })
        .LightweightCharts as unknown as typeof import('lightweight-charts')
      // 跟 PriceChart.tsx 建圖時同一組 timeScale 設定。fixLeftEdge 是這一關的
      // 主角之一：它擋住捲進空白，也正是它跟 fitContent 湊在一起會把圖釘死。
      const chart = lib.createChart(document.getElementById('chart')!, {
        height: 460,
        timeScale: { borderColor: '#334155', timeVisible: false, fixLeftEdge: true },
      })
      const series = chart.addSeries(lib.CandlestickSeries, {})
      series.setData(
        Array.from({ length: bars }, (_, i) => ({
          time: (1_700_000_000 + i * 86_400) as never,
          open: 100 + i,
          high: 102 + i,
          low: 99 + i,
          close: 101 + i,
        })),
      )

      const scale = chart.timeScale()
      if (view === 'fitContent') scale.fitContent()
      else scale.setVisibleLogicalRange({ from: Math.max(0, bars - initialVisible), to: bars - 1 })

      const seen: { from: number; to: number }[] = []
      scale.subscribeVisibleLogicalRangeChange((range) => {
        if (range) seen.push({ from: range.from, to: range.to })
      })
      Object.assign(window, {
        __seen: seen,
        __range: () => scale.getVisibleLogicalRange(),
      })
    },
    { bars: BARS, initialVisible: INITIAL_VISIBLE_BARS, view: initialView },
  )

  // 等版面真的算完，而不是等一個固定的毫秒數：固定等待在慢一點的 CI runner 上
  // 就是偶發紅燈，而偶發紅燈會擋掉修通知路徑的 hotfix。
  await page.waitForFunction(() => (window as never as { __range: () => unknown }).__range() !== null)
  await afterLayout(page)
  const opened = await page.evaluate(() =>
    (window as never as { __range: () => { from: number; to: number } }).__range(),
  )

  // 真的拖曳。往右拖＝把圖往過去拉。
  const box = (await page.locator('#chart').boundingBox())!
  const y = box.y + box.height / 2
  for (let i = 0; i < 6; i += 1) {
    await page.mouse.move(box.x + 200, y)
    await page.mouse.down()
    await page.mouse.move(box.x + 800, y, { steps: 20 })
    await page.mouse.up()
  }
  await page.waitForFunction(() => (window as never as { __range: () => unknown }).__range() !== null)
  await afterLayout(page)

  const dragged = await page.evaluate(() =>
    (window as never as { __range: () => { from: number; to: number } }).__range(),
  )
  const seen = await page.evaluate(
    () => (window as never as { __seen: { from: number }[] }).__seen,
  )
  await page.close()

  return { opened, dragged, minFrom: Math.min(...seen.map((r) => r.from), opened.from), pageErrors }
}

describe('在真的瀏覽器裡，這張圖拉得動', () => {
  it('打開的時候歷史留在畫面左外側 —— 這是「往前拉」能成立的前提', async () => {
    const { opened, pageErrors } = await dragToTheOldest('recent')

    expect(pageErrors).toEqual([])
    expect(opened.to).toBeCloseTo(BARS - 1, 0)
    // 不是全部。全部＝左邊沒有東西可以拉。
    expect(opened.from).toBeGreaterThan(0)
  })

  it('往左拖真的會動，而且拉得到最舊那一根', async () => {
    const { opened, dragged } = await dragToTheOldest('recent')

    expect(dragged.from).toBeLessThan(opened.from)
    // 而且要落進「去要更早的資料」的門檻裡，否則拉到底也不會補資料。
    expect(dragged.from).toBeLessThanOrEqual(LOAD_MORE_WITHIN_BARS)
  })

  it('任何一刻都沒有捲進空白 —— from 不會變成負數', async () => {
    // fixLeftEdge 在做的事。它壞掉的樣子就是使用者最早回報的那張截圖：右邊有
    // K 棒，左邊一整片黑。
    const { minFrom } = await dragToTheOldest('recent')

    expect(minFrom).toBeGreaterThanOrEqual(0)
  })

  it('用 fitContent 當初始視角的話，畫布會被釘死 —— 這是回歸的哨兵', async () => {
    // 這一條斷言的是**壞掉的行為**，而且刻意留著。它綠，代表那個組合仍然會把
    // 圖釘死，也就代表上面三條不是巧合綠的；它紅，代表函式庫改了語意，而那時
    // 上面三條的理由需要重新讀一遍。
    const { opened, dragged } = await dragToTheOldest('fitContent')

    expect(opened.from).toBeCloseTo(0, 0)
    expect(dragged.from).toBeCloseTo(opened.from, 0)
    expect(dragged.to).toBeCloseTo(opened.to, 0)
  })
})
