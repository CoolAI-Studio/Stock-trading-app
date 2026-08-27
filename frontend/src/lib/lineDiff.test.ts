/**
 * 兩版程式碼差在哪裡。
 *
 * ＊ 為什麼要有差異，而不是只給兩份全文。
 *
 * 使用者不會寫 Python。給他兩段看起來幾乎一樣的程式碼，叫他自己找出差在哪，等於沒
 * 給。他要回答的問題是「我（或 AI）到底改了什麼」，而那個問題只有差異回答得了。
 *
 * ＊ 這裡最容易做錯的地方：把「一行都沒改」畫成整份都改了。
 *
 * 逐行對齊如果只用位置比對，那麼在中間插入一行就會讓後面每一行都被標成改過——而使
 * 用者看到的是「AI 把整支策略重寫了」，一個會讓他不敢按還原的假象。
 */

import { describe, expect, it } from 'vitest'
import { lineDiff } from './lineDiff'

describe('逐行差異', () => {
  it('一模一樣就是全部沒變', () => {
    const rows = lineDiff('a\nb\nc', 'a\nb\nc')

    expect(rows.every((row) => row.kind === 'same')).toBe(true)
    expect(rows).toHaveLength(3)
  })

  it('中間插一行，不會把後面每一行都算成改過', () => {
    // 這是最重要的一條。只用位置比對的話，b 之後的每一行都會錯位而被標成改過，
    // 而使用者會以為整支策略被重寫了——一個會讓他不敢按還原的假象。
    const rows = lineDiff('a\nb\nc', 'a\nb\nNEW\nc')

    expect(rows.filter((row) => row.kind === 'added').map((r) => r.text)).toEqual(['NEW'])
    expect(rows.filter((row) => row.kind === 'removed')).toHaveLength(0)
    expect(rows.filter((row) => row.kind === 'same')).toHaveLength(3)
  })

  it('刪掉一行就只有那一行被標成刪掉', () => {
    const rows = lineDiff('a\nb\nc', 'a\nc')

    expect(rows.filter((row) => row.kind === 'removed').map((r) => r.text)).toEqual(['b'])
    expect(rows.filter((row) => row.kind === 'added')).toHaveLength(0)
  })

  it('改一行 = 刪掉舊的、加上新的', () => {
    // 「改」在逐行差異裡沒有獨立的表示法，而硬要湊一個出來會在多行改動時對錯配
    // 對——讓他看到兩行毫不相干的東西被說成是同一行的前後。
    const rows = lineDiff('a\nb\nc', 'a\nB\nc')

    expect(rows.filter((row) => row.kind === 'removed').map((r) => r.text)).toEqual(['b'])
    expect(rows.filter((row) => row.kind === 'added').map((r) => r.text)).toEqual(['B'])
  })

  it('空的那一邊也要處理', () => {
    expect(lineDiff('', 'a').filter((r) => r.kind === 'added')).toHaveLength(1)
    expect(lineDiff('a', '').filter((r) => r.kind === 'removed')).toHaveLength(1)
  })
})
