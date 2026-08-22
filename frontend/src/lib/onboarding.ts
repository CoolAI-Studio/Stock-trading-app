const SEEN_KEY = 'onboarding-seen'

/**
 * 引導流程只自動出現一次，這是那個「一次」的記號。
 *
 * WHY IT IS NEEDED AT ALL. 判斷「這個帳號還是空的」很容易（沒有策略、沒有自選股、
 * 沒有通知管道），但只有那個判斷會把人關在裡面：使用者按下「我知道自己在做什麼，
 * 直接進儀表板」，帳號還是空的，於是又被導回引導——一個沒有出口的迴圈。
 *
 * IN THE BROWSER, ON PURPOSE. 它記的是「這個人已經看過並且自己決定離開了」，
 * 那是一個關於這台裝置上這個人的事實，不是關於這份部署的設定。放進資料庫會多一
 * 張表、一個遷移和一個端點，換來的差別只有換一台電腦會再看到一次引導——而那一次
 * 其實也不算壞事。
 *
 * 每一個存取都包在 try/catch 裡：無痕視窗和「封鎖網站資料」的瀏覽器會直接丟例外，
 * 而那種時候正確的行為是「就當作沒看過」，不是讓整頁白掉。
 */
export function markOnboardingSeen(): void {
  try {
    localStorage.setItem(SEEN_KEY, '1')
  } catch {
    // 記不住就算了：最壞的結果是他下次再看到一次引導，而引導本身可以跳過。
  }
}

export function hasSeenOnboarding(): boolean {
  try {
    return localStorage.getItem(SEEN_KEY) !== null
  } catch {
    return false
  }
}
