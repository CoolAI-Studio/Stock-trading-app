import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { Pager } from '../components/Pager'
import { QueryError } from '../components/QueryError'
import type { WebhookLog, WebhookSetup } from '../lib/types'

const PAGE_SIZE = 50

/** Setting up TradingView, and seeing what it actually sent.
 *
 * The audit rows have been written on every authenticated call since the
 * webhook existed, and pruned on a schedule -- created and then deleted
 * without anybody ever having been able to read them. When an alert did not
 * become an order, there was no way to tell whether it arrived at all,
 * whether the secret was wrong, whether the JSON was malformed, or whether a
 * risk gate refused it.
 */
export function WebhooksPage() {
  const [offset, setOffset] = useState(0)

  const setupQuery = useQuery({
    queryKey: ['webhook-setup'],
    queryFn: () => api.get<WebhookSetup>('/api/webhooks/tradingview/setup'),
  })
  const logsQuery = useQuery({
    queryKey: ['webhook-logs', offset],
    queryFn: () =>
      api.get<WebhookLog[]>(
        `/api/webhooks/tradingview/logs?limit=${PAGE_SIZE}&offset=${offset}`,
      ),
  })

  const logs = logsQuery.data ?? []

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-semibold">TradingView 訊號</h1>

      {setupQuery.isError && <QueryError error={setupQuery.error} />}
      {setupQuery.data && <SetupPanel setup={setupQuery.data} />}

      <div className="space-y-2">
        <h2 className="text-sm font-semibold text-slate-300">收件紀錄</h2>
        {logsQuery.isError && (
          <QueryError error={logsQuery.error} onRetry={() => logsQuery.refetch()} />
        )}
        {logsQuery.isSuccess && logs.length === 0 && (
          <p className="text-slate-500">
            還沒收到任何 TradingView 訊號。設定好之後，這裡會列出每一次呼叫，包含被擋下來的。
          </p>
        )}
        {logs.length > 0 && (
          <div className="overflow-x-auto">
            <table aria-label="收件紀錄" className="w-full text-left text-sm">
              <thead className="text-slate-500">
                <tr>
                  <th className="pb-2 font-normal">時間</th>
                  <th className="pb-2 font-normal">結果</th>
                  <th className="pb-2 font-normal">內容</th>
                  <th className="pb-2 font-normal">來源 IP</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <tr key={log.id} className="border-b border-slate-800 align-top text-slate-300">
                    <td className="py-2 pr-4 whitespace-nowrap text-slate-500">
                      {new Date(log.received_at).toLocaleString()}
                    </td>
                    <td className="py-2 pr-4">
                      <Outcome log={log} />
                    </td>
                    <td className="py-2 pr-4">
                      <pre className="max-w-md overflow-x-auto text-xs text-slate-400">
                        {log.raw_body}
                      </pre>
                    </td>
                    <td className="py-2 text-xs text-slate-500">{log.remote_ip ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pager offset={offset} pageSize={PAGE_SIZE} shown={logs.length} onChange={setOffset} />
      </div>
    </div>
  )
}

/** Four outcomes worth telling apart, because each one has a different fix.
 *
 * "沒有變成訂單" is the subtle one: the call was fine and a risk gate refused
 * it, which is not a TradingView problem at all. */
function Outcome({ log }: { log: WebhookLog }) {
  if (!log.signature_valid) {
    return <Badge tone="bad">密鑰不符</Badge>
  }
  if (!log.parsed_ok) {
    return (
      <div className="space-y-1">
        <Badge tone="bad">格式看不懂</Badge>
        {log.error && <p className="max-w-xs text-xs text-red-300">{log.error}</p>}
      </div>
    )
  }
  if (log.order_id === null) {
    return (
      <div className="space-y-1">
        <Badge tone="warn">沒有變成訂單</Badge>
        {log.error && <p className="max-w-xs text-xs text-amber-300">{log.error}</p>}
      </div>
    )
  }
  return <Badge tone="good">已建立訂單 #{log.order_id}</Badge>
}

function Badge({ tone, children }: { tone: 'good' | 'warn' | 'bad'; children: React.ReactNode }) {
  const skin = {
    good: 'border-emerald-800 bg-emerald-950/40 text-emerald-300',
    warn: 'border-amber-700 bg-amber-950/40 text-amber-300',
    bad: 'border-red-800 bg-red-950/40 text-red-300',
  }[tone]
  return (
    <span className={`whitespace-nowrap rounded border px-2 py-0.5 text-xs ${skin}`}>
      {children}
    </span>
  )
}

function SetupPanel({ setup }: { setup: WebhookSetup }) {
  const [copied, setCopied] = useState<'url' | 'message' | null>(null)

  async function copy(value: string, which: 'url' | 'message') {
    await navigator.clipboard.writeText(value)
    setCopied(which)
    setTimeout(() => setCopied(null), 2000)
  }

  return (
    <section className="space-y-3 rounded border border-slate-800 p-4">
      <h2 className="text-sm font-semibold text-slate-300">怎麼設定</h2>

      <div>
        <p className="text-sm text-slate-400">Webhook URL</p>
        <div className="flex flex-wrap items-center gap-2">
          <code className="rounded bg-slate-950 px-2 py-1 text-xs">{setup.url}</code>
          <button
            onClick={() => copy(setup.url, 'url')}
            className="rounded bg-slate-700 px-2 py-1 text-xs font-medium text-white hover:bg-slate-600"
          >
            {copied === 'url' ? '已複製' : '複製'}
          </button>
        </div>
      </div>

      <div>
        <p className="text-sm text-slate-400">警報訊息</p>
        <pre className="overflow-x-auto rounded bg-slate-950 p-2 text-xs text-slate-300">
          {setup.example_message}
        </pre>
        <button
          onClick={() => copy(setup.example_message, 'message')}
          className="mt-1 rounded bg-slate-700 px-2 py-1 text-xs font-medium text-white hover:bg-slate-600"
        >
          {copied === 'message' ? '已複製' : '複製'}
        </button>
      </div>

      <ul className="list-inside list-disc space-y-1 text-xs text-slate-500">
        {setup.notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </section>
  )
}
