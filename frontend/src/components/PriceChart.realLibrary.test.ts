/**
 * 這一支不用替身，用真的 lightweight-charts。
 *
 * PriceChart.test.tsx 把整個繪圖庫 mock 掉了——那是對的，jsdom 沒有 canvas，而
 * 「TradingView 的函式庫畫不畫得出正確的像素」不是我們的測試。但**替身回答不了
 * 一整類問題**：我們傳過去的選項名字對不對、我們呼叫的方法存不存在、
 * `fixLeftEdge` 到底是真的被吃進去還是被安靜地忽略。
 *
 * 這件事已經咬過一次。上一輪修「往前拉是空白」的時候，十條測試全綠、CI 全綠、
 * 部署成功，而使用者拉下去還是空白——因為 `fixLeftEdge` ＋ `fitContent()` 湊在
 * 一起會讓畫布**拒絕移動**，而手寫的替身裡 `fitContent` 是一個空函式，永遠不會
 * 表現出那件事。
 *
 * 這個 repo 已經有一個 commit 在講同一件事：「替身在真貨會失敗的地方成功，就不
 * 是替身，是一塊遮布」。
 *
 * 所以這裡只測替身測不到的那一類：**API 契約**。互動（拖曳、縮放）不在這裡測，
 * 因為真函式庫要等 animation frame 才套用，而 jsdom 不會自己跑——那種測試會給
 * 出看起來很像結論的假數字。
 */

import { beforeAll, describe, expect, it, vi } from 'vitest'

vi.unmock('lightweight-charts')

// eslint-disable-next-line import/first
import { CandlestickSeries, createChart } from 'lightweight-charts'

beforeAll(() => {
  // jsdom 沒有 canvas。給一個什麼方法都回 undefined 的 2D context 就夠讓
  // lightweight-charts 建得起來——它畫出來的東西我們不看，我們看它接不接受。
  const context = new Proxy(
    {
      canvas: {},
      measureText: () => ({ width: 10 }),
      getImageData: () => ({ data: [] }),
    },
    { get: (target, key) => (key in target ? (target as never)[key] : () => undefined) },
  )
  HTMLCanvasElement.prototype.getContext = (() => context) as never
  ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
})

function realChart() {
  const element = document.createElement('div')
  Object.defineProperty(element, 'clientWidth', { value: 800 })
  Object.defineProperty(element, 'clientHeight', { value: 400 })
  document.body.appendChild(element)
  return createChart(element, {
    width: 800,
    height: 400,
    timeScale: { borderColor: '#334155', timeVisible: false, fixLeftEdge: true },
  })
}

describe('真的 lightweight-charts 認得我們傳的東西', () => {
  it('fixLeftEdge 是真的被吃進去，不是被安靜地忽略', () => {
    // 打錯一個字母的選項不會有任何錯誤，只會什麼都不做——而「什麼都不做」的樣
    // 子就是使用者回報的那片空白。讀回來才知道它進去了。
    const chart = realChart()

    expect(chart.timeScale().options().fixLeftEdge).toBe(true)

    chart.remove()
  })

  it('這張圖用到的每一個 timeScale 方法都存在', () => {
    // 替身有的方法是我照著自己的記憶寫的，所以替身永遠有。真貨沒有的話，線上會
    // 是「x is not a function」而整個儀表板白畫面——這張圖跟持倉、訂單、自選股
    // 在同一頁。
    const scale = realChart().timeScale()

    for (const method of [
      'fitContent',
      'getVisibleRange',
      'setVisibleRange',
      'getVisibleLogicalRange',
      'setVisibleLogicalRange',
      'subscribeVisibleLogicalRangeChange',
      'unsubscribeVisibleLogicalRangeChange',
    ] as const) {
      expect(typeof scale[method], method).toBe('function')
    }
  })

  it('可見範圍的 from 會小於載入的根數 —— 初始視角本來就該留歷史在畫面外', () => {
    // 這是「往前拉」存在的前提：畫面裝不下全部，左邊才有東西可以拉過去。
    // 800px 配預設的棒距，裝得下約 133 根，而我們載入 300 根。
    const chart = realChart()
    const series = chart.addSeries(CandlestickSeries, {})
    series.setData(
      Array.from({ length: 300 }, (_, i) => ({
        time: (1_700_000_000 + i * 86_400) as never,
        open: 100,
        high: 101,
        low: 99,
        close: 100,
      })),
    )

    const range = chart.timeScale().getVisibleLogicalRange()!

    expect(range.to).toBeGreaterThan(range.from)
    expect(range.to - range.from).toBeLessThan(300)
    chart.remove()
  })
})
