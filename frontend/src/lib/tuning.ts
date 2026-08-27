/**
 * 「5, 10, 20」變成 [5, 10, 20]。
 *
 * 逗號、空白、換行都當分隔——使用者從別的地方複製一串數字過來，中間是什麼分隔符不
 * 該由他負責。
 *
 * 看起來像數字的轉成數字，true/false 轉成布林，其餘原樣送出去：參數不一定是數字，
 * 有些策略的開關是布林，有些是字串。全部當成字串送出去的話，後端那邊 `window` 會
 * 變成 "5" 而策略拿它去做算術——那不會報錯，只會安靜地算錯。
 */
export function parseValues(raw: string): (number | boolean | string)[] {
  return raw
    .split(/[,\s]+/)
    .map((piece) => piece.trim())
    .filter(Boolean)
    .map((piece) => {
      if (piece === 'true') return true
      if (piece === 'false') return false
      const asNumber = Number(piece)
      return Number.isFinite(asNumber) ? asNumber : piece
    })
}
