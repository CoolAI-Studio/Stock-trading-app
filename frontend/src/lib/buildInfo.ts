/**
 * 這一份前端是哪一版。
 *
 * 後端追 `stable` 自動更新（#52）。前端不是——Vercel 的 `new/clone` 會在使用者的
 * GitHub 帳號下複製一份 repo，來源就斷了。我們替那份複製品加了一個每天從上游同步
 * 的工作流程，但那件事**可能不會發生**：Actions 沒開、同步遇到衝突、或者他改過那
 * 份程式碼。
 *
 * 而那些情況下畫面上什麼都不會變——他看到的還是一個正常運作的 app，只是它是三個月
 * 前的。一個看不見的失效，在這個專案裡等於沒有處理。
 *
 * 值從哪裡來：Vercel 建置時會給 VERCEL_GIT_COMMIT_SHA，vite.config.ts 把它（或
 * APP_GIT_COMMIT）注入成建置期常數。沒有的話就是 null。
 */

/** 七碼十六進位，跟後端的 build_info.commit() 同一個格式。 */
const SHORT = 7
const SHA = /^[0-9a-f]{7,40}$/i

/**
 * 把建置環境給的東西變成一個版本，或者 null。
 *
 * **null 代表不知道，不是最新。** 「unknown」這種字串在畫面上讀起來像一個答案；而
 * 一個看起來像版本但其實不是的東西（例如分支名 "main"）更糟——它會讓比對得到一個
 * 確定而錯誤的答案。跟後端 build_info.commit() 是同一條規則。
 */
export function frontendCommit(raw: string | undefined): string | null {
  const value = (raw ?? '').trim()
  if (!value || !SHA.test(value)) return null
  return value.toLowerCase().slice(0, SHORT)
}

/** 這一次建置的版本。建置期就決定了，執行時不會變。 */
export const FRONTEND_COMMIT = frontendCommit(__APP_COMMIT__)
