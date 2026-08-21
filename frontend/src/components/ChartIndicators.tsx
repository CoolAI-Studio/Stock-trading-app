/**
 * Picking indicators without writing Python.
 *
 * The owner's words: 「沒有任何指標可以選擇，重點就是要那些指標才有辦法下策略跟
 * 回測」. There are forty indicators in the runtime and the only way to reach one
 * was to write a strategy in a textarea. For this app's audience -- CLAUDE.md
 * says the reader is not an engineer -- that is the same as not having them.
 *
 * THIS FILE DOES NO ARITHMETIC, and must never start. Every value on the chart
 * is computed server-side by `spec.fn`, the very function object the strategy
 * sandbox hands to user code. A moving average implemented here would be a
 * second implementation of the same idea, and the first day the two disagreed
 * the chart would be a picture of something that is not happening -- which is
 * worse than having no indicators at all.
 *
 * The list, the tuning knobs and their defaults all come from the server too.
 * A hard-coded list here would drift the first time an indicator was added,
 * and would offer a choice the server then refuses.
 */

import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { AvailableIndicators, IndicatorSpec, ParamValue } from '../lib/types'

export type SelectedIndicator = { name: string; params: Record<string, ParamValue> }

/** Matches MAX_INDICATORS_PER_REQUEST on the server, which returns 422 above it.
 *
 * Enforced here as well so the answer is a sentence next to the button rather
 * than a chart that silently stops updating. */
export const MAX_INDICATORS = 8

export const INDICATOR_STORAGE_KEY = 'chart-indicators'

/** What was picked last time.
 *
 * Kept in localStorage rather than on the account: it is a view preference,
 * and a round trip to the server before the chart can draw would put a
 * cold-started backend between somebody and their own moving average.
 */
function restore(): SelectedIndicator[] {
  // localStorage is editable by hand and survives every reload AND every
  // deploy, so a shape this version does not understand WILL eventually be
  // read back. That makes this the most dangerous function in the file: an
  // entry that throws throws on every single mount, the dashboard is
  // permanently broken, and the fix is 「open developer tools and clear
  // localStorage」 -- which CLAUDE.md says is the same as no fix at all for
  // somebody who is not an engineer.
  //
  // So every value is REBUILT here rather than passed through. Downstream code
  // may then assume `params` is an object of primitives, because nothing else
  // can get out of this function.
  try {
    const raw = window.localStorage.getItem(INDICATOR_STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []

    const clean: SelectedIndicator[] = []
    for (const entry of parsed) {
      if (typeof entry !== 'object' || entry === null) continue
      const { name, params } = entry as { name?: unknown; params?: unknown }
      if (typeof name !== 'string' || !name) continue

      // Only values the server will accept. A nested object or an array shown
      // as 「the default」 in the box but still SENT is the worst of both: the
      // chart answers 422 forever and the number on screen looks fine.
      const kept: Record<string, ParamValue> = {}
      if (typeof params === 'object' && params !== null && !Array.isArray(params)) {
        for (const [key, value] of Object.entries(params)) {
          if (typeof value === 'number' && Number.isFinite(value)) kept[key] = value
          else if (typeof value === 'boolean' || typeof value === 'string') kept[key] = value
        }
      }
      clean.push({ name, params: kept })
    }
    return clean.slice(0, MAX_INDICATORS)
  } catch {
    return []
  }
}

/** A number box that can be cleared and retyped.
 *
 * Bound straight to the parent's value it cannot be: clearing the box reports
 * nothing, the parent keeps the old number, and the field refills mid-edit --
 * so clearing 20 and typing 60 produces 2060. Same bug, same fix as
 * StrategyParams.
 */
function NumberField({
  label,
  value,
  whole,
  onCommit,
}: {
  label: string
  value: number
  whole: boolean
  onCommit: (next: number | undefined) => void
}) {
  const [text, setText] = useState(String(value))

  useEffect(() => {
    if (text !== '' && Number(text) !== value) setText(String(value))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  return (
    <input
      aria-label={label}
      type="number"
      // A period of 5.5 candles is not a thing, and the server refuses a float
      // where an int was declared rather than letting range() raise inside the
      // library.
      step={whole ? 1 : 'any'}
      min={whole ? 1 : undefined}
      value={text}
      onChange={(e) => {
        const raw = e.target.value
        setText(raw)
        onCommit(raw === '' ? undefined : Number(raw))
      }}
      className="w-20 rounded border border-slate-700 bg-slate-950 px-2 py-0.5 text-xs font-mono"
    />
  )
}

export function ChartIndicators({
  selected,
  onChange,
}: {
  selected: SelectedIndicator[]
  onChange: (next: SelectedIndicator[]) => void
}) {
  const query = useQuery({
    queryKey: ['indicators-available'],
    // The catalogue is compiled into the backend and cannot change while this
    // page is open, so refetching it is pure cost on a free-tier dyno.
    staleTime: Infinity,
    queryFn: () => api.get<AvailableIndicators>('/api/market/indicators/available'),
  })

  useEffect(() => {
    try {
      window.localStorage.setItem(INDICATOR_STORAGE_KEY, JSON.stringify(selected))
    } catch {
      // Private browsing, a full quota. Losing the preference is not worth
      // taking the dashboard down for.
    }
  }, [selected])

  const available = query.data?.indicators ?? []
  const full = selected.length >= MAX_INDICATORS

  function add(name: string) {
    if (!name || full) return
    // Two identical lines drawn on top of each other, and one of the eight
    // slots spent on it.
    if (selected.some((entry) => entry.name === name)) return
    const spec = available.find((entry) => entry.name === name)
    if (!spec) return
    // The author's own defaults, so the first thing drawn is the indicator as
    // its author meant it rather than as a blank form.
    const params: Record<string, ParamValue> = {}
    for (const param of spec.params) params[param.name] = param.default
    onChange([...selected, { name, params }])
  }

  function setParam(name: string, param: string, value: ParamValue | undefined) {
    onChange(
      selected.map((entry) => {
        if (entry.name !== name) return entry
        const params = { ...(entry.params ?? {}) }
        if (value === undefined) delete params[param]
        else params[param] = value
        return { ...entry, params }
      }),
    )
  }

  const byName = new Map(available.map((spec) => [spec.name, spec]))
  const groups = new Map<string, IndicatorSpec[]>()
  for (const spec of available) {
    // Already on the chart: not offered again. Selecting it would draw a
    // second identical line on top of the first and spend one of the eight
    // slots, and a menu entry that does nothing is worse than no entry.
    if (selected.some((entry) => entry.name === spec.name)) continue
    const label = spec.category_label || spec.category
    const list = groups.get(label) ?? []
    list.push(spec)
    groups.set(label, list)
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label="加入指標"
          disabled={full || query.isPending}
          // Always back to the placeholder: this is an 「add」 control, not a
          // 「current selection」 one, and leaving the last pick showing reads
          // as though only one indicator can be on at a time.
          value=""
          onChange={(e) => add(e.target.value)}
          className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-sm disabled:opacity-50"
        >
          <option value="">＋ 加入指標…</option>
          {[...groups.entries()].map(([category, specs]) => (
            <optgroup key={category} label={category}>
              {specs.map((spec) => (
                <option key={spec.name} value={spec.name}>
                  {spec.title}（{spec.name}）
                </option>
              ))}
            </optgroup>
          ))}
        </select>

        {full && <span className="text-xs text-amber-400">最多 8 個，要換的話先移除一個。</span>}

        {query.isError && (
          <span role="alert" className="text-xs text-red-400">
            讀不到指標清單 —— 後端可能還沒醒，或者部署的版本比這個畫面舊。稍後重新整理看看。
          </span>
        )}
      </div>

      {selected.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {selected.map((entry) => {
            const spec = byName.get(entry.name)
            return (
              <div
                key={entry.name}
                className="flex items-center gap-2 rounded border border-slate-700 bg-slate-900/60 px-2 py-1 text-xs"
              >
                <span className="font-mono text-slate-200">{spec?.title ?? entry.name}</span>

                {(spec?.params ?? []).map((param) =>
                  typeof param.default === 'number' ? (
                    <label key={param.name} className="flex items-center gap-1 text-slate-400">
                      {param.name}
                      <NumberField
                        label={`${entry.name} ${param.name}`}
                        value={
                          typeof entry.params?.[param.name] === 'number'
                            ? (entry.params[param.name] as number)
                            : (param.default as number)
                        }
                        whole={param.type === 'int'}
                        onCommit={(next) => setParam(entry.name, param.name, next)}
                      />
                    </label>
                  ) : null,
                )}

                <button
                  type="button"
                  aria-label={`移除 ${entry.name}`}
                  onClick={() => onChange(selected.filter((other) => other.name !== entry.name))}
                  className="text-slate-500 hover:text-red-400"
                >
                  ✕
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

ChartIndicators.restore = restore
