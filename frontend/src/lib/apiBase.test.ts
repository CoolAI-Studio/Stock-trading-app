/**
 * 後端在哪裡。
 *
 * ＊ 後端直接供應前端之後，預設值必須是「同一個網址」。
 *
 * 原本預設 `http://localhost:8000`——那對「前後端分開跑」的開發環境是對的，但對「使
 * 用者打開他自己那一份部署」是致命的：瀏覽器會去打 **他自己電腦上的** 8000 埠，那
 * 裡什麼都沒有。而錯誤訊息會是連線被拒絕，跟真正的原因（設定預設值選錯了）差了十
 * 萬八千里。
 *
 * ＊ 但開發環境還是要能跑。
 *
 * `npm run dev` 的時候前端在 5173、後端在 8000，同源是錯的。分界是 `import.meta.
 * env.DEV`——那是建置時就決定的常數，不是執行時猜的。
 */

import { describe, expect, it } from 'vitest'
import { resolveApiBase } from './apiBase'

describe('後端的網址', () => {
  it('明確設了就用他設的 —— 前端另外部署的人靠這個', () => {
    // 這是「拿掉必須部署兩次，但沒有拿掉可以部署兩次」的那個開關。
    expect(resolveApiBase({ base: 'https://api.example.com', dev: false })).toBe(
      'https://api.example.com',
    )
  })

  it('沒設而且是正式版 —— 同源，因為後端就是供應這個頁面的那一個', () => {
    expect(resolveApiBase({ base: undefined, dev: false })).toBe('')
  })

  it('沒設而且在開發模式 —— localhost:8000，因為那時候兩邊是分開跑的', () => {
    expect(resolveApiBase({ base: undefined, dev: true })).toBe('http://localhost:8000')
  })

  it('空字串是一個明確的選擇，不是「沒設」', () => {
    // 有人就是要同源但又想寫出來。把空字串當成沒設，會讓他在開發模式下拿到
    // localhost:8000——一個他明明寫了設定卻沒被聽見的結果。
    expect(resolveApiBase({ base: '', dev: true })).toBe('')
  })
})
