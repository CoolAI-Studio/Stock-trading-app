/**
 * 兩版程式碼差在哪裡，逐行。
 *
 * 使用者不會寫 Python。給他兩段看起來幾乎一樣的程式碼、叫他自己找出差在哪，等於沒
 * 給——他要回答的問題是「我（或 AI）到底改了什麼」，而只有差異回答得了。
 *
 * ＊ 用最長共同子序列，不是逐位置比對。
 *
 * 逐位置比對在中間插一行就會讓後面每一行都錯位而被標成改過——使用者看到的是「整支
 * 策略被重寫了」，一個會讓他不敢按還原的假象。
 *
 * ＊ 「改一行」故意沒有獨立的表示法。
 *
 * 一行改動就是「刪掉舊的、加上新的」。硬要湊一個 modified 出來，會在多行改動時對錯
 * 配對——讓他看到兩行毫不相干的東西被說成是同一行的前後。
 *
 * 策略是幾十行的東西，所以這裡用最直觀的 O(n×m) 動態規劃：清楚比快重要。
 */

export interface DiffRow {
  kind: 'same' | 'added' | 'removed'
  text: string
}

export function lineDiff(before: string, after: string): DiffRow[] {
  const oldLines = before.split('\n')
  const newLines = after.split('\n')

  // lcs[i][j] = oldLines[i:] 和 newLines[j:] 的最長共同子序列長度
  const lcs: number[][] = Array.from({ length: oldLines.length + 1 }, () =>
    new Array<number>(newLines.length + 1).fill(0),
  )
  for (let i = oldLines.length - 1; i >= 0; i -= 1) {
    for (let j = newLines.length - 1; j >= 0; j -= 1) {
      lcs[i][j] =
        oldLines[i] === newLines[j]
          ? lcs[i + 1][j + 1] + 1
          : Math.max(lcs[i + 1][j], lcs[i][j + 1])
    }
  }

  const rows: DiffRow[] = []
  let i = 0
  let j = 0
  while (i < oldLines.length && j < newLines.length) {
    if (oldLines[i] === newLines[j]) {
      rows.push({ kind: 'same', text: oldLines[i] })
      i += 1
      j += 1
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      rows.push({ kind: 'removed', text: oldLines[i] })
      i += 1
    } else {
      rows.push({ kind: 'added', text: newLines[j] })
      j += 1
    }
  }
  while (i < oldLines.length) {
    rows.push({ kind: 'removed', text: oldLines[i] })
    i += 1
  }
  while (j < newLines.length) {
    rows.push({ kind: 'added', text: newLines[j] })
    j += 1
  }
  return rows
}
