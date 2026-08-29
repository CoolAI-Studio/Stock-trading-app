import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../lib/api'
import { FRONTEND_COMMIT } from '../lib/buildInfo'
import type { SystemStatus } from '../lib/types'

/**
 * 「是不是還在跑」, answered inside the app.
 *
 * CLAUDE.md asks for a Prometheus endpoint and a Grafana dashboard, and gives
 * the reason: 「警告不能停擺，就必須看得到它有沒有在跑」. That reason is right and
 * this page serves it; the instruments were wrong for the audience. A scraped
 * metrics endpoint is only real if something scrapes it, which on a free-tier
 * box means a Grafana Cloud account and an eighth blank in the deploy form --
 * for a dashboard that somebody who wants stock alerts on their phone will
 * never open.
 *
 * Every number here was already in the process. What was missing was a screen.
 */

const HEADLINE = {
  ok: { text: '一切正常，提醒正在運作。', tone: 'border-emerald-800 bg-emerald-950/40 text-emerald-300' },
  warn: { text: '有問題，但提醒還在跑。下面標黃色的部分值得看一下。', tone: 'border-amber-800 bg-amber-950/40 text-amber-300' },
  fail: { text: '提醒可能已經停擺。下面標紅色的就是原因。', tone: 'border-red-800 bg-red-950/40 text-red-300' },
} as const

function Row({ label, value, bad }: { label: string; value: string; bad?: boolean }) {
  return (
    <div className="flex justify-between gap-4 border-b border-slate-800 py-2 text-sm">
      <span className="text-slate-400">{label}</span>
      <span className={bad ? 'font-medium text-red-400' : 'text-slate-200'}>{value}</span>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded border border-slate-700 bg-slate-900 p-4">
      <h2 className="mb-2 text-sm font-semibold text-slate-100">{title}</h2>
      {children}
    </section>
  )
}

/** Seconds as something a person reads at a glance rather than divides. */
function age(seconds: number | null): string {
  if (seconds === null) return '還沒跑過'
  if (seconds < 90) return `${Math.round(seconds)} 秒前`
  if (seconds < 5400) return `${Math.round(seconds / 60)} 分鐘前`
  return `${(seconds / 3600).toFixed(1)} 小時前`
}

/** 「Something is wrong and I do not know what」, answered against this
 * deployment's own numbers.
 *
 * That is the question a non-developer actually asks -- never 「what does
 * last_loop_age_sec mean」 -- and a general chatbot answers it with a
 * checklist. The backend attaches the current state to the question so the
 * answer can name the actual problem.
 *
 * Rendered only when the backend says an assistant exists. AI_API_KEY is one
 * more blank in a deploy form and is optional by design; a box that answers
 * every question with 「尚未設定 AI_API_KEY」 makes the optional thing feel
 * broken. */
function Assistant() {
  const [question, setQuestion] = useState('')
  const [reply, setReply] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const ask = useMutation({
    mutationFn: (message: string) =>
      api.post<{ ok: boolean; reply: string | null; error: string | null }>(
        '/api/system/assist',
        { message },
      ),
    onSuccess: (result) => {
      setReply(result.ok ? result.reply : null)
      setError(result.ok ? null : (result.error ?? '問不到答案，請再試一次。'))
    },
    onError: (err) => setError(err instanceof Error ? err.message : '問不到答案，請再試一次。'),
  })

  return (
    <Section title="看不懂上面在說什麼？問一下">
      <p className="mb-2 text-sm text-slate-400">
        會把這一頁的狀態一起送給 AI，所以它答得出「你這台現在是哪裡有問題」。
        送出去的只有上面看得到的數字和代號，不含任何金鑰或密碼。
      </p>
      <textarea
        aria-label="問題"
        rows={2}
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        placeholder="例如：為什麼我都收不到通知？"
        className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-sm"
      />
      <button
        type="button"
        disabled={ask.isPending || !question.trim()}
        onClick={() => ask.mutate(question.trim())}
        className="mt-2 rounded bg-sky-700 px-3 py-1 text-sm font-medium text-white hover:bg-sky-600 disabled:opacity-50"
      >
        {ask.isPending ? '問問看…' : '問'}
      </button>

      {error && (
        <p role="alert" className="mt-2 text-sm text-red-400">
          {error}
        </p>
      )}
      {reply && <p className="mt-2 whitespace-pre-wrap text-sm text-slate-200">{reply}</p>}
    </Section>
  )
}

interface Change {
  sha: string
  title: string
  at: string | null
}

/** 從他這一版到最新之間，每一個 commit 的第一行。 */
function ChangeList() {
  const updates = useQuery({
    queryKey: ['system-updates'],
    queryFn: () => api.get<{ changes: Change[] }>('/api/system/updates'),
    staleTime: 5 * 60 * 1000,
  })

  if (updates.isLoading) return <p className="text-xs">正在讀更新內容…</p>

  const changes = updates.data?.changes ?? []
  if (changes.length === 0) {
    // 空清單有兩個原因：真的沒有更新，或者比不出來（這一份分岔了、問不到 GitHub）。
    // 畫成「已經是最新」會讓他錯過安全修補。
    return (
      <p className="text-xs text-amber-300/80">
        列不出改了什麼（可能是這一份被改過，或現在問不到 GitHub）。
      </p>
    )
  }

  return (
    <ul className="space-y-1 text-xs">
      {changes.map((change) => (
        <li key={change.sha} className="flex gap-2">
          <code className="shrink-0 text-amber-300/60">{change.sha}</code>
          <span>{change.title}</span>
        </li>
      ))}
    </ul>
  )
}

export function SystemStatusPage() {
  const query = useQuery({
    queryKey: ['system-status'],
    queryFn: () => api.get<SystemStatus>('/api/system/status'),
    // This is the page somebody opens because they suspect something is wrong.
    // A stale answer is the one thing it must not give.
    refetchInterval: 15_000,
  })

  if (query.isError) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold text-slate-100">系統狀態</h1>
        <p role="alert" className="rounded border border-red-800 bg-red-950/40 px-3 py-2 text-red-300">
          讀不到系統狀態。後端可能沒有在跑 —— 而如果後端沒在跑，提醒也沒在跑。
          先去 Render 的 Logs 看看服務起來了沒有。
        </p>
      </div>
    )
  }

  if (!query.data) {
    return <p className="text-slate-500">載入中…</p>
  }

  const { overall, worker, market_data, notifications, assistant_available, update } = query.data
  const headline = HEADLINE[overall]

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-slate-100">系統狀態</h1>

      <p className={`rounded border px-3 py-2 ${headline.tone}`}>{headline.text}</p>

      {/*
        他這一份是不是舊的。

        **已經是最新的時候什麼都不說。** 一個永遠有話要說的區塊會讓他學會不看
        它——而真的有新版的那一次，他也不會看。

        而「不知道」跟「已經是最新」是兩件事，畫面上必須分得出來：說成最新會讓他
        錯過安全修補，而那正是他打開這一頁想確認的事。
      */}
      {update?.behind === true && (
        <div className="space-y-2 rounded border border-amber-700 bg-amber-950/40 px-3 py-2 text-sm text-amber-200">
          <p>
            <strong>有新版可以更新。</strong>
            你這一份是 <code>{update.running}</code>，最新的是 <code>{update.latest}</code>。
            更新裡可能包含安全修補——去你部署後端的平台按一次重新部署就會拿到。
          </p>
          {/*
            「有新版」不夠。他要決定的是「值不值得現在更新」，而那個決定只有看得到
            改了什麼才做得出來——尤其是「這裡面有沒有安全修補」。
          */}
          <ChangeList />
        </div>
      )}
      {/*
        前端自己是不是舊的。

        後端會自己更新（追 stable、autoDeploy，#52）。前端不會——Vercel 的 clone
        會複製一份 repo，來源就斷了。我們替那份複製品加了每天同步的工作流程，但它
        可能不會發生：Actions 沒開、同步有衝突、或者他改過那份程式碼。

        而那些情況下畫面上什麼都不會變。**「後端最新、前端很舊」正是最可能發生、
        也最不容易被發現的組合**，所以它要自己一格，跟後端那格分開講。

        比不出來（latest 是 null，或這次建置沒有帶 commit）就什麼都不說——誤報會讓
        他去重新部署一個其實沒有問題的東西。
      */}
      {update?.latest && FRONTEND_COMMIT && FRONTEND_COMMIT !== update.latest && (
        <p className="rounded border border-amber-700 bg-amber-950/40 px-3 py-2 text-sm text-amber-200">
          <strong>你看到的這個畫面是舊的。</strong>
          它是 <code>{FRONTEND_COMMIT}</code>，最新的是 <code>{update.latest}</code>。
          前端跟後端是分開部署的，所以它們可以不同步——去你部署前端的平台按一次重新部署。
        </p>
      )}

      {update && update.behind === null && (
        <p className="rounded border border-slate-700 px-3 py-2 text-sm text-slate-400">
          查不到有沒有新版{update.why ? `：${update.why}` : '。'}
          {update.running && <> 你這一份是 <code>{update.running}</code>。</>}
        </p>
      )}

      <Section title="背景 worker（負責跑策略、盯價格）">
        {worker.enabled ? (
          <>
            <Row
              label="最後一次循環"
              value={age(worker.last_loop_age_sec)}
              bad={worker.last_loop_age_sec === null || worker.last_loop_age_sec > 300}
            />
            <Row
              label="最後一次成功抓行情"
              value={age(worker.last_poll_age_sec)}
              bad={worker.last_poll_age_sec === null || worker.last_poll_age_sec > 300}
            />
            <Row label="已經連續運作" value={age(worker.uptime_sec).replace('前', '')} />
          </>
        ) : (
          <Row label="狀態" value="被關掉了（WORKER_ENABLED=false）—— 策略不會執行" bad />
        )}
      </Section>

      <Section title="行情">
        <Row
          label="連續抓不到任何價格的次數"
          value={`${market_data.consecutive_empty_polls} 次`}
          bad={market_data.consecutive_empty_polls > 0}
        />
        {market_data.stale_symbols.length === 0 ? (
          <Row label="抓不到報價的代號" value="沒有" />
        ) : (
          <div className="pt-2">
            {/* Named, not counted. 「有 1 個代號有問題」 sends somebody off to
                work out which; the fix is to correct or delete that one row,
                and they cannot do either from a number. */}
            <p className="mb-1 text-sm text-red-400">
              這些代號抓不到報價，它們上面的提醒等於停擺：
            </p>
            <ul className="space-y-1">
              {market_data.stale_symbols.map((item) => (
                <li key={item.symbol} className="flex justify-between text-sm">
                  <span className="font-mono text-red-300">{item.symbol}</span>
                  <span className="text-slate-400">已經 {age(item.gap_sec)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </Section>

      <Section title={`通知（最近 ${notifications.window_hours} 小時）`}>
        {!notifications.enabled && (
          <p className="mb-2 rounded border border-red-800 bg-red-950/40 px-2 py-1 text-sm text-red-300">
            通知功能被關掉了（NOTIFICATIONS_ENABLED）。策略照跑，但一則警告都不會送出。
          </p>
        )}
        <Row label="已送出" value={`${notifications.sent} 則`} />
        <Row label="還在重試" value={`${notifications.retrying} 則`} />
        <Row label="等靜音時段結束" value={`${notifications.deferred} 則`} />
        <Row
          label="已放棄，不會再送"
          value={`${notifications.given_up} 則`}
          bad={notifications.given_up > 0}
        />
        <Row
          label="沒有送到任何管道"
          value={`${notifications.reached_nobody} 則`}
          bad={notifications.reached_nobody > 0}
        />
      </Section>

      {assistant_available && <Assistant />}
    </div>
  )
}
