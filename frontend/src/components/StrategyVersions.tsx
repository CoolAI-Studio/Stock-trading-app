/**
 * 版本清單、差異、還原。
 *
 * 後端做完了（#35），但那些端點對這個使用者等於不存在——他不會去打 API。這個元件是
 * 「改壞了有路可以回去」唯一到得了他手上的地方。
 *
 * ＊ 這個面板最重要的一句話：還原是可逆的。
 *
 * 他不會寫 Python，而他即將把正在盯盤的策略換成三個月前的版本。不說清楚那件事可以
 * 再還原回來，他就不敢按——而一個不敢按的還原鍵等於沒有還原功能。
 *
 * ＊ 還原被拒絕的時候，要說清楚不是他的錯。
 *
 * 舊版本可能因為我們收緊了沙箱而編不過（#50 就是在處理那件事的後果）。顯示一句
 * 「編譯失敗」會讓他去改一段其實沒有問題的程式碼——而他改不動，因為問題不在那裡。
 * 後端已經把那句話寫好了，這裡原樣顯示。
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError, api } from '../lib/api'
import { lineDiff } from '../lib/lineDiff'

interface StrategyVersion {
  id: number
  source_code: string
  params: Record<string, unknown>
  code_hash: string
  author: string
  created_at: string
}

/** manual / ai / restore 對他的意義完全不同，所以用他的話講。 */
const AUTHOR: Record<string, string> = {
  manual: '你自己改的',
  ai: 'AI 改的',
  restore: '從舊版本還原來的',
}

function when(iso: string): string {
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? iso : at.toLocaleString()
}

export function StrategyVersions({
  strategyId,
  currentSource,
}: {
  strategyId: number
  currentSource: string
}) {
  const queryClient = useQueryClient()
  const [comparing, setComparing] = useState<StrategyVersion | null>(null)
  const [problem, setProblem] = useState<string | null>(null)

  const versions = useQuery({
    queryKey: ['strategy-versions', strategyId],
    queryFn: () => api.get<StrategyVersion[]>(`/api/strategies/${strategyId}/versions`),
  })

  const restore = useMutation({
    mutationFn: (versionId: number) =>
      api.post(`/api/strategies/${strategyId}/versions/${versionId}/restore`),
    onSuccess: () => {
      setProblem(null)
      setComparing(null)
      queryClient.invalidateQueries({ queryKey: ['strategy-versions', strategyId] })
      queryClient.invalidateQueries({ queryKey: ['strategies'] })
    },
    onError: (error: unknown) => {
      // 後端那句話已經解釋了不是他的錯，原樣顯示比重寫一句好。
      setProblem(
        error instanceof ApiError && error.message
          ? error.message
          : '還原沒有成功，請再試一次。',
      )
    },
  })

  const rows = versions.data ?? []

  if (versions.isLoading) {
    return <p className="text-sm text-slate-400">正在讀版本歷史…</p>
  }

  if (rows.length <= 1) {
    // 剛建立的策略只有一版，而那一版就是現在在跑的。一個只有一列、按鈕還不能按的
    // 清單，只是在佔畫面。
    return (
      <p className="text-sm text-slate-400">
        還沒有其他版本。每次你或 AI 改動程式碼或參數，這裡就會留下一版，隨時可以回去。
      </p>
    )
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-400">
        每次改動都留了一版。<strong>還原不會刪掉任何東西</strong>——它是新增一版，所以
        你隨時可以再還原回來。
      </p>

      <ul className="space-y-2">
        {rows.map((version, index) => (
          <li
            key={version.id}
            className="flex flex-wrap items-center gap-3 rounded border border-slate-800 p-2 text-sm"
          >
            <span className="text-slate-300">{when(version.created_at)}</span>
            <span className="text-slate-500">{AUTHOR[version.author] ?? version.author}</span>
            {index === 0 && (
              <span className="rounded bg-sky-900 px-2 py-0.5 text-xs text-sky-200">
                現在在跑的就是這一版
              </span>
            )}
            <span className="grow" />
            <button
              type="button"
              className="rounded border border-slate-700 px-2 py-1 text-xs"
              onClick={() => setComparing(comparing?.id === version.id ? null : version)}
            >
              {comparing?.id === version.id ? '收起來' : '看差在哪裡'}
            </button>
            {index !== 0 && (
              <button
                type="button"
                className="rounded bg-sky-700 px-2 py-1 text-xs disabled:opacity-50"
                onClick={() => restore.mutate(version.id)}
                disabled={restore.isPending}
              >
                還原成這一版
              </button>
            )}
          </li>
        ))}
      </ul>

      {problem && (
        <p role="status" className="rounded bg-slate-800 p-3 text-sm text-amber-300">
          {problem}
        </p>
      )}

      {comparing && (
        <div className="space-y-1">
          <p className="text-xs text-slate-400">
            紅色是那一版有、現在沒有的；綠色是現在有、那一版沒有的。
          </p>
          <pre className="overflow-x-auto rounded bg-slate-950 p-3 text-xs leading-5">
            {lineDiff(comparing.source_code, currentSource).map((row, index) => (
              <div
                key={`${index}-${row.text}`}
                className={
                  row.kind === 'removed'
                    ? 'text-rose-300'
                    : row.kind === 'added'
                      ? 'text-emerald-300'
                      : 'text-slate-400'
                }
              >
                {row.kind === 'removed' ? '- ' : row.kind === 'added' ? '+ ' : '  '}
                {row.text}
              </div>
            ))}
          </pre>
        </div>
      )}
    </div>
  )
}
