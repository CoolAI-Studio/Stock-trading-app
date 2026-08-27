/**
 * 前端也要說得出自己是哪一版。
 *
 * ＊ 為什麼這件事重要。
 *
 * 後端是從我們的 repo 部署的，追 `stable`、自動更新（#52）。前端不是——Vercel 的
 * `new/clone` 會在使用者的 GitHub 帳號下複製一份 repo，**來源就斷了**。
 *
 * 我們替那份複製品加了一個每天從上游同步的工作流程，但那件事可能不會發生：他的
 * repo 上 Actions 沒開、同步遇到衝突、或者他改過那份程式碼。而那些情況下**畫面上
 * 什麼都不會變**——他看到的還是一個正常運作的 app，只是它是三個月前的。
 *
 * 一個看不見的失效，在這個專案裡等於沒有處理。所以前端要說得出自己是哪一版，而系
 * 統狀態頁把它跟後端擺在一起比。
 *
 * ＊ 「不知道」跟「最新」是兩件事。
 *
 * 本機開發、或任何沒有把 commit 傳進建置的地方，這個值是 null。那要說成不知道，不
 * 可以說成最新——跟 build_info.commit() 和 update_check 是同一條規則。
 */

import { describe, expect, it } from 'vitest'
import { frontendCommit } from './buildInfo'

describe('前端的版本標記', () => {
  it('沒有人告訴它是哪一版的時候，回 null 而不是編一個', () => {
    // 「unknown」這種字串在畫面上讀起來像一個答案，而這裡唯一誠實的答案是沒有答案。
    expect(frontendCommit(undefined)).toBeNull()
    expect(frontendCommit('')).toBeNull()
  })

  it('拿到的是完整 sha 的時候，縮成跟後端一樣的七碼', () => {
    // 後端的 build_info.commit() 回七碼。兩邊格式不一樣的話，比對永遠不相等——
    // 而畫面上會永遠寫著「前端是舊的」，然後他會學會忽略它。
    expect(frontendCommit('5a54cddcab66a33a6e9818ddd7469220aaad1fe6')).toBe('5a54cdd')
  })

  it('不是 sha 的東西一律當成不知道', () => {
    // 建置環境塞什麼進來我們控制不了。一個看起來像版本但其實不是的東西，比沒有
    // 版本更糟：它會讓比對得到一個確定而錯誤的答案。
    expect(frontendCommit('main')).toBeNull()
    expect(frontendCommit('not-a-sha')).toBeNull()
  })
})
