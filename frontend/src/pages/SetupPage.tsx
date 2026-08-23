import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError, api } from '../lib/api'
import type { SetupStatus } from '../lib/types'

/**
 * The first screen a stranger sees after clicking 「Deploy to Render」.
 *
 * They are not a developer. render.yaml asks them for seven values, and the
 * instructions for two of them used to be 「run this Python script on your own
 * machine」 -- which, for somebody who wants stock alerts on their phone, is
 * where the story ended: an empty blank, a process that died at import, and a
 * 502 with a stack trace in a log they would never find.
 *
 * The backend now stays up in setup mode and reports what is missing. This is
 * the half they look at, and it is built on one rule: NEVER SEND THEM
 * SOMEWHERE ELSE TO GET A VALUE. If the app can produce it, there is a button.
 * If it genuinely cannot -- the database lives on somebody else's service --
 * the page says so plainly instead of pretending.
 */

/** What each generator is called in the owner's language. */
const GENERATOR_LABEL: Record<string, string> = {
  fernet: '產生加密金鑰',
  token: '產生一組隨機密碼',
  vapid: '產生推播金鑰（一對）',
}

function GeneratedValues({ values }: { values: Record<string, string> }) {
  return (
    <div className="mt-2 space-y-2">
      {Object.entries(values).map(([name, value]) => (
        <label key={name} className="block">
          <span className="text-xs text-slate-400">{name}</span>
          {/* readOnly rather than plain text: a real input is what makes
              select-all-and-copy work the way somebody expects, on a phone as
              well as a desktop. */}
          <input
            readOnly
            value={value}
            onFocus={(e) => e.currentTarget.select()}
            className="block w-full rounded border border-emerald-800 bg-slate-950 px-2 py-1 font-mono text-xs text-emerald-300"
          />
        </label>
      ))}
    </div>
  )
}

function MissingRow({
  item,
  showStep,
}: {
  item: SetupStatus['missing'][number]
  /** 同一個步驟裡的第一項才標。三把金鑰本來就是同一個階段，數字沒有錯——但連續
   *  三個「步驟 2」，讀的人問的是「我現在到底在第幾步」，而畫面回答他三次一樣的
   *  數字。 */
  showStep: boolean
}) {
  const [values, setValues] = useState<Record<string, string> | null>(null)
  const [error, setError] = useState<string | null>(null)

  const generate = useMutation({
    mutationFn: () =>
      api.post<Record<string, string>>('/api/setup/generate', { kind: item.generator }),
    onSuccess: (result) => {
      setValues(result)
      setError(null)
    },
    // A button that does nothing when it fails is worse than no button: the
    // person concludes the page is broken and stops.
    onError: (err) => setError(err instanceof Error ? err.message : '產生失敗，請再試一次。'),
  })

  return (
    <li className="rounded border border-slate-700 bg-slate-900 p-3">
      <p className="flex items-baseline gap-2">
        {/* The step number is the part render.yaml could not show: seven blanks
            presented side by side look independent, and two of them cannot even
            be KNOWN until the one before has happened. */}
        {showStep && (
          <span className="rounded bg-slate-700 px-1.5 text-xs text-slate-300">
            步驟 {item.step}
          </span>
        )}
        <span className="font-mono text-sm font-medium text-amber-300">{item.name}</span>
      </p>
      <p className="mt-1 text-sm text-slate-300">{item.why}</p>
      <p className="mt-1 text-sm text-slate-400">{item.how}</p>

      {/* 方案，攤開並排。原本這一格是一段散文，一句話裡塞四條路——而讀它的人
          按這個專案的定義不是工程師，一段話沒有辦法讓人「選」。
          有網址的就給連結：叫他自己去搜尋，等於把流程交還給運氣。 */}
      {(item.options?.length ?? 0) > 0 && (
        <ul className="mt-2 space-y-2">
          {item.options!.map((option) => (
            <li
              key={option.label}
              className="rounded border border-slate-700 bg-slate-950/60 px-3 py-2"
            >
              <p className="text-sm font-medium text-slate-200">
                {option.url ? (
                  <a
                    href={option.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-sky-400 underline"
                  >
                    {option.label}
                  </a>
                ) : (
                  option.label
                )}
              </p>
              <p className="mt-0.5 text-sm text-slate-400">{option.detail}</p>
            </li>
          ))}
        </ul>
      )}

      {/* CORS_ORIGINS is the last step of the flow and the one most likely to
          be got wrong, because it cannot be known until the frontend exists.
          But by the time anybody is reading this, the frontend DOES exist --
          they are looking at it -- so the browser can print exactly what to
          paste instead of sending them to go and find it. */}
      {item.name === 'CORS_ORIGINS' && (
        <label className="mt-2 block">
          <span className="text-xs text-slate-400">把這一串貼進去就對了</span>
          <input
            readOnly
            value={window.location.origin}
            onFocus={(e) => e.currentTarget.select()}
            className="block w-full rounded border border-emerald-800 bg-slate-950 px-2 py-1 font-mono text-xs text-emerald-300"
          />
        </label>
      )}

      {item.generator && (
        <button
          type="button"
          disabled={generate.isPending}
          onClick={() => generate.mutate()}
          className="mt-2 rounded bg-sky-700 px-3 py-1 text-sm font-medium text-white hover:bg-sky-600 disabled:opacity-50"
        >
          {generate.isPending ? '產生中…' : (GENERATOR_LABEL[item.generator] ?? '產生')}
        </button>
      )}

      {error && (
        <p role="alert" className="mt-2 text-sm text-red-400">
          {error}
        </p>
      )}
      {values && <GeneratedValues values={values} />}
    </li>
  )
}

export function SetupPage() {
  const statusQuery = useQuery({
    queryKey: ['setup-status'],
    queryFn: () => api.get<SetupStatus>('/api/setup/status'),
    retry: false,
  })

  // The endpoint disappears the moment there is nothing left to configure, so
  // a 404 is the SUCCESS signal here rather than an error worth reporting.
  const finished =
    (statusQuery.error instanceof ApiError && statusQuery.error.status === 404) ||
    (statusQuery.isSuccess && statusQuery.data.missing.length === 0)

  // Any other failure must not read as 「finished」: that would send somebody to
  // a login page that cannot work, with nothing on screen explaining why.
  const failed = statusQuery.isError && !finished

  // Split rather than sorted. The two groups answer different questions --
  // 「can I use this at all」 and 「what will not work once I can」 -- and a
  // reader who sees one list assumes every row in it has the same weight.
  const missing = statusQuery.data?.missing ?? []
  const blocking = missing.filter((item) => item.blocking)
  const advisory = missing.filter((item) => !item.blocking)

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-6">
      <h1 className="text-xl font-semibold text-slate-100">完成你的部署設定</h1>

      {statusQuery.isPending && <p className="text-slate-500">檢查中…</p>}

      {failed && (
        <p role="alert" className="rounded border border-red-800 bg-red-950/40 px-3 py-2 text-red-300">
          讀不到設定狀態。可能是後端還在啟動（免費方案的冷啟動常常要一分鐘左右），
          稍等一下重新整理；如果一直這樣，去你的部署平台看那個服務的 Log，確認它有沒有起來。
        </p>
      )}

      {finished && (
        <div className="rounded border border-emerald-800 bg-emerald-950/40 px-3 py-2 text-emerald-300">
          <p className="font-medium">設定完成，可以開始用了。</p>
          <p className="mt-1 text-sm">
            重新整理這一頁就會回到登入畫面。這個部署還沒有任何帳號的話，那一頁會直接讓你
            建立第一個——就在網頁上，不用打指令，也不用再改任何設定。建立完成之後註冊就會
            自己關起來，別人進不來。
          </p>
        </div>
      )}

      {statusQuery.isSuccess && statusQuery.data.missing.length > 0 && (
        <>
          <p className="rounded border border-slate-700 bg-slate-800/60 px-3 py-2 text-sm text-slate-300">
            {statusQuery.data.where}
          </p>

          {blocking.length > 0 && (
            <section>
              <h2 className="mb-2 text-sm font-semibold text-red-300">
                這些沒填，系統現在完全不能用（{blocking.length} 項）
              </h2>
              <ul className="space-y-3">
                {blocking.map((item, index, arr) => (
                  <MissingRow
                    key={item.name}
                    item={item}
                    showStep={index === 0 || arr[index - 1].step !== item.step}
                  />
                ))}
              </ul>
            </section>
          )}

          {advisory.length > 0 && (
            <section>
              {/* Deliberately a separate heading. 「it will not start」 and
                  「TradingView will send to the wrong address」 are not the same
                  urgency, and one list containing both teaches people to skim
                  past the half that matters. */}
              <h2 className="mb-2 text-sm font-semibold text-amber-300">
                這些不會擋住啟動，但沒填就會有東西不能用（{advisory.length} 項）
              </h2>
              <ul className="space-y-3">
                {advisory.map((item, index, arr) => (
                  <MissingRow
                    key={item.name}
                    item={item}
                    showStep={index === 0 || arr[index - 1].step !== item.step}
                  />
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  )
}
