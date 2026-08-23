/**
 * 陌生人看到的第一個頁面（GitHub Pages）。
 *
 * 為什麼它存在：這個 repo 是公開的，而一個只想在手機上收股票提醒的人打開它，看到
 * 的是 509 個檔案，其中 181 個是測試。使用者的話：「使用者也看不懂到底哪些是要用
 * 到的。」
 *
 * 他提過的另一個做法是開第二個「乾淨骨架」repo。那個做法會製造第二個事實來源——
 * 這一份修了策略沙箱逃逸，骨架那一份要有人記得同步，而沒同步的那段時間，照骨架
 * 部署的人拿到的是有洞的版本，且他不會知道。同一個 repo 裡的一頁 HTML 沒有這個
 * 問題：安全修補一次到位，Pages 自動跟著更新。
 *
 * 這裡守的是這一頁的**承重規則**，不是它的排版：
 *
 *   一、本機和雲端都要在，而且是並排的選擇，不是一句補述。
 *   二、不可以指定某一家。這一頁的前身（登入頁那顆按鈕）就是直接跳 Render，而
 *       「不要綁死廠商」是這個專案最早提出的三個需求之一。
 *   三、AI 是選配。設定流程不可以依賴它——AI 需要一把金鑰，那本身就是一格空白，
 *       讓設定依賴它就循環了（CLAUDE.md）。
 */

import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..')
const page = readFileSync(resolve(REPO, 'docs', 'index.html'), 'utf-8')
const text = page.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')

describe('引導頁：一個陌生人打開它就知道下一步', () => {
  it('本機和雲端是並排的兩條路，不是一句補述', () => {
    expect(text).toMatch(/自己的電腦|本機/)
    expect(text).toMatch(/雲端/)
  })

  it('不指定某一家 —— 它要的是三樣東西，不是三個品牌', () => {
    // 這一頁的前身是登入頁上一顆直接跳 render.com/deploy 的按鈕，而那把選擇拿走
    // 了。這裡列得出多家當例子是好的，列成「唯一的路」不行。
    expect(text).toMatch(/哪一家|任何一家|不是三個品牌/)
    for (const alternative of ['Railway', 'Fly.io']) {
      expect(text, `雲端只舉了一家的例子，缺 ${alternative}`).toContain(alternative)
    }
    for (const database of ['Neon', 'Supabase']) {
      expect(text).toContain(database)
    }
  })

  it('說得出資料庫是唯一要去別人家拿的東西', () => {
    // 整份設定裡只有這一格 app 生不出來。不說清楚，他會卡在第一步而不知道為什麼
    // 別人說「五分鐘就好」。
    expect(text).toMatch(/連線字串/)
  })

  it('AI 是選配，而且說得出不設定會怎樣', () => {
    // CLAUDE.md：AI 輔助不能是設定流程的必需品。AI 需要 AI_API_KEY，那本身就是
    // 一格空白，讓設定依賴它就循環了。
    expect(text).toMatch(/選配|可以不用|不設定也/)
    expect(text).toMatch(/提醒|盯盤|通知/)
  })

  it('給得出回到原始碼的路', () => {
    expect(page).toContain('github.com/CoolAI-Studio/Stock-trading-app')
  })

  it('自己站得住 —— 沒有外部樣式表或腳本', () => {
    // GitHub Pages 上沒有建置步驟，而一個抓不到 CDN 就散掉的頁面，是這個專案第
    // 一個對外的畫面。
    expect(page).not.toMatch(/<script[^>]+src=/i)
    expect(page).not.toMatch(/<link[^>]+stylesheet[^>]+https?:/i)
  })

  it('手機看得下去', () => {
    // 目標使用者是「想在手機上收股票提醒的人」，而他很可能就是在手機上讀這一頁。
    expect(page).toMatch(/<meta[^>]+viewport/i)
  })
})
