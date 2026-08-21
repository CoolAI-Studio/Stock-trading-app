/**
 * The numbers inside a strategy, edited without editing Python.
 *
 * CLAUDE.md says the audience is 「不會寫 Python 的使用者」. Every number a
 * strategy decides on -- the moving-average window, the threshold, how many
 * bars to look back -- was a literal in the source, so changing 5 to 20 meant
 * editing Python in a textarea. For this audience that is the same as not
 * being able to change it at all.
 *
 * The source declares `self.params = {"window": 5}`, the validator reports
 * those defaults, and this renders one field per parameter.
 *
 * IT HANDS BACK ONLY THE DIFFERENCES. Storing the whole merged dict would pin
 * the strategy to whatever the defaults were on the day it was saved, so a
 * later edit to the code could never change one again.
 */

import { useEffect, useState } from 'react'

export type ParamValue = number | boolean | string

/** A number box that lets you clear it and type a new number.
 *
 * Bound straight to the parent's value, it cannot: clearing the box removes
 * the override, the value falls back to the author's default, and the box
 * refills with it mid-edit -- so clearing 20 and typing 20 produces 520. That
 * is not a test artifact; it is what somebody typing into it gets.
 *
 * So the text being typed is state of its own, and the parent only ever hears
 * about a number that parsed. An empty box reports 「no override」 without
 * reporting a value, which is also what stops Number('') === 0 from silently
 * saving a window of zero.
 */
function NumberField({
  name,
  value,
  whole,
  onChange,
}: {
  name: string
  value: number
  whole: boolean
  onChange: (next: number | undefined) => void
}) {
  const [text, setText] = useState(String(value))

  // Follow the parent when it changes underneath -- a reset, a different
  // strategy loaded into the same form -- but never while this box is being
  // edited. An empty box is mid-edit by definition: clearing it drops the
  // override, the value falls back to the author's default, and syncing then
  // would refill the box with the very number the person is trying to
  // replace. That is how clearing 20 and typing 5 produced 55.
  useEffect(() => {
    if (text !== '' && Number(text) !== value) setText(String(value))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  return (
    <input
      aria-label={name}
      type="number"
      // Whole numbers stay whole: a window of 5.5 candles is not a thing, and
      // the backend refuses a float where an int was declared.
      step={whole ? 1 : 'any'}
      value={text}
      onChange={(e) => {
        const raw = e.target.value
        setText(raw)
        onChange(raw === '' ? undefined : Number(raw))
      }}
      className="w-32 rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono"
    />
  )
}

export function StrategyParams({
  declared,
  value,
  onChange,
}: {
  /** What the source declares, with the author's own defaults. */
  declared: Record<string, ParamValue>
  /** Only the ones the owner changed. */
  value: Record<string, ParamValue>
  onChange: (next: Record<string, ParamValue>) => void
}) {
  const names = Object.keys(declared)
  // A stored value with no matching declaration means the source was edited.
  // The backend refuses to save one; saying so here is what stops somebody
  // wondering why their setting does nothing.
  const orphans = Object.keys(value).filter((name) => !(name in declared))

  if (names.length === 0 && orphans.length === 0) return null

  function set(name: string, next: ParamValue | undefined) {
    const merged = { ...value }
    // Back at the author's default means 「not overridden」, not 「overridden to
    // the same thing」 -- otherwise a later change to the default could never
    // reach a strategy somebody had once touched.
    if (next === undefined || next === declared[name]) {
      delete merged[name]
    } else {
      merged[name] = next
    }
    onChange(merged)
  }

  return (
    <div className="space-y-2 rounded border border-slate-700 bg-slate-900/60 p-3">
      <p className="text-sm font-medium text-slate-200">
        參數 <span className="font-normal text-slate-500">（改這裡就好，不用動程式碼）</span>
      </p>

      {orphans.length > 0 && (
        <p role="alert" className="text-xs text-amber-400">
          存著的設定 {orphans.join('、')} 在現在的程式碼裡沒有對應的參數了 ——
          程式碼改過之後留下來的。存檔時會被擋下來，請先移除。
        </p>
      )}

      {names.map((name) => {
        const fallback = declared[name]
        const current = name in value ? value[name] : fallback
        const changed = name in value

        return (
          <label key={name} className="flex flex-wrap items-center gap-2 text-sm">
            <span className="w-32 shrink-0 font-mono text-slate-300">{name}</span>

            {typeof fallback === 'boolean' ? (
              <input
                aria-label={name}
                type="checkbox"
                checked={Boolean(current)}
                onChange={(e) => set(name, e.target.checked)}
                className="h-4 w-4"
              />
            ) : typeof fallback === 'number' ? (
              <NumberField
                name={name}
                value={current as number}
                whole={Number.isInteger(fallback)}
                onChange={(next) => set(name, next)}
              />
            ) : (
              <input
                aria-label={name}
                type="text"
                value={String(current)}
                onChange={(e) => set(name, e.target.value)}
                className="w-48 rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono"
              />
            )}

            {/* The author's own value, always shown. Without it there is no way
                back from an edit that made things worse. */}
            <span className={`text-xs ${changed ? 'text-amber-400' : 'text-slate-500'}`}>
              預設 {String(fallback)}
              {changed && '（已改）'}
            </span>
          </label>
        )
      })}
    </div>
  )
}
