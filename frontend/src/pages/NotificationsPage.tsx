import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { DeleteButton } from '../components/DeleteButton'
import { ApiError, api } from '../lib/api'
import { isPushSupported, subscribeToPush, unsubscribeFromPush } from '../lib/push'
import type { ChannelType, NotificationChannel, NotificationLog } from '../lib/types'

const STATUS_LABEL: Record<'sent' | 'failed', string> = { sent: '已送出', failed: '失敗' }

/** The SMTP settings, shaped the way the backend's EmailConfig reads them.
 *
 * All six have been accepted since day one; the form only ever sent three, so
 * Gmail, Outlook, SendGrid -- anything that authenticates -- could not be
 * configured at all, and the test button came back with an authentication
 * error the page had no field to fix. */
/** Three states worth telling apart: off, on-but-failing, and working.
 *
 * The middle one is the dangerous one -- a channel that says 啟用中 while
 * every send bounces. The retry sweep switches a permanently dead channel off
 * and writes what to do about it into last_error, and this is where the owner
 * reads it. */
/** The owner did not build this and should not have to read its enum names.
 * WEB_PUSH and order.created were appearing verbatim on the page. */
const CHANNEL_LABEL: Record<ChannelType, string> = {
  line: 'LINE',
  telegram: 'Telegram',
  email: 'Email',
  web_push: '瀏覽器推播',
}

const EVENT_LABEL: Record<string, string> = {
  'order.created': '新的待確認訂單',
  'order.updated': '訂單狀態變更',
  'strategy.error': '策略發生錯誤',
  'strategy.alert': '策略提醒',
}

function ChannelHealth({ channel }: { channel: NotificationChannel }) {
  if (!channel.is_enabled) {
    return (
      <div className="space-y-1">
        <span className="rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-400">
          已停用
        </span>
        {channel.last_error && (
          <p className="max-w-xs text-xs text-amber-300">{channel.last_error}</p>
        )}
      </div>
    )
  }
  if (channel.last_error) {
    return (
      <div className="space-y-1">
        <span className="rounded border border-red-800 bg-red-950/40 px-2 py-0.5 text-xs text-red-300">
          上次失敗
        </span>
        <p className="max-w-xs text-xs text-red-300">{channel.last_error}</p>
      </div>
    )
  }
  return (
    <div className="space-y-1">
      <span className="rounded border border-emerald-800 bg-emerald-950/40 px-2 py-0.5 text-xs text-emerald-300">
        正常
      </span>
      {channel.last_sent_at && (
        <p className="text-xs text-slate-500">
          上次送出 {new Date(channel.last_sent_at).toLocaleString()}
        </p>
      )}
    </div>
  )
}

function emailConfig(fields: {
  host: string
  port: string
  username: string
  password: string
  fromAddr: string
  toAddr: string
}): Record<string, string | number | boolean> {
  const port = Number(fields.port)
  return {
    host: fields.host,
    port: Number.isFinite(port) && port > 0 ? port : 587,
    username: fields.username,
    password: fields.password,
    from_addr: fields.fromAddr,
    to_addr: fields.toAddr,
    // TLS on submit-port SMTP is what every hosted provider requires, and
    // there is no field for it because turning it off is not a thing anyone
    // configuring Gmail needs to do.
    use_tls: true,
  }
}

function EditChannelForm({ channel, onDone }: { channel: NotificationChannel; onDone: () => void }) {
  const [label, setLabel] = useState(channel.label)
  const [isEnabled, setIsEnabled] = useState(channel.is_enabled)
  const [botToken, setBotToken] = useState('')
  const [chatId, setChatId] = useState('')
  const [accessToken, setAccessToken] = useState('')
  const [to, setTo] = useState('')
  const [host, setHost] = useState('')
  const [port, setPort] = useState('587')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [fromAddr, setFromAddr] = useState('')
  const [toAddr, setToAddr] = useState('')
  const [error, setError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload: Record<string, unknown> = { label, is_enabled: isEnabled }
      const config =
        channel.channel_type === 'telegram'
          ? { bot_token: botToken, chat_id: chatId }
          : channel.channel_type === 'line'
            ? { access_token: accessToken, to }
            : emailConfig({ host, port, username, password, fromAddr, toAddr })
      // Port is excluded on purpose: it is prefilled with 587, so counting it
      // would make every save look like a new configuration and overwrite the
      // stored credentials with the blank boxes the form shows for them.
      const hasNewConfig = Object.entries(config).some(
        ([key, value]) => key !== 'port' && String(value).trim() !== '',
      )
      if (hasNewConfig) payload.config = config
      return api.patch<NotificationChannel>(`/api/notifications/channels/${channel.id}`, payload)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-channels'] })
      onDone()
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : '儲存失敗'),
  })

  return (
    <div className="space-y-3 rounded border border-slate-800 p-4">
      <div>
        <label htmlFor={`edit-channel-label-${channel.id}`} className="text-sm text-slate-400">
          名稱
        </label>
        <input
          id={`edit-channel-label-${channel.id}`}
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={isEnabled} onChange={(e) => setIsEnabled(e.target.checked)} />
        啟用
      </label>

      <p className="text-sm text-slate-500">以下欄位留空表示不變更現有設定：</p>

      {channel.channel_type === 'telegram' && (
        <>
          <div>
            <label htmlFor={`edit-bot-token-${channel.id}`} className="text-sm text-slate-400">
              機器人權杖（Bot Token）
            </label>
            <input
              id={`edit-bot-token-${channel.id}`}
              value={botToken}
              onChange={(e) => setBotToken(e.target.value)}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
          <div>
            <label htmlFor={`edit-chat-id-${channel.id}`} className="text-sm text-slate-400">
              聊天室代號（Chat ID）
            </label>
            <input
              id={`edit-chat-id-${channel.id}`}
              value={chatId}
              onChange={(e) => setChatId(e.target.value)}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
        </>
      )}

      {channel.channel_type === 'line' && (
        <>
          <div>
            <label htmlFor={`edit-access-token-${channel.id}`} className="text-sm text-slate-400">
              存取權杖（Access Token）
            </label>
            <input
              id={`edit-access-token-${channel.id}`}
              value={accessToken}
              onChange={(e) => setAccessToken(e.target.value)}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
          <div>
            <label htmlFor={`edit-line-to-${channel.id}`} className="text-sm text-slate-400">
              接收者使用者 ID
            </label>
            <input
              id={`edit-line-to-${channel.id}`}
              value={to}
              onChange={(e) => setTo(e.target.value)}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
        </>
      )}

      {channel.channel_type === 'email' && (
        <>
          <div>
            <label htmlFor={`edit-smtp-host-${channel.id}`} className="text-sm text-slate-400">
              SMTP 主機
            </label>
            <input
              id={`edit-smtp-host-${channel.id}`}
              value={host}
              onChange={(e) => setHost(e.target.value)}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label htmlFor={`edit-smtp-port-${channel.id}`} className="text-sm text-slate-400">
                連接埠
              </label>
              <input
                id={`edit-smtp-port-${channel.id}`}
                value={port}
                onChange={(e) => setPort(e.target.value)}
                inputMode="numeric"
                className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
              />
            </div>
            <div className="flex-1">
              <label htmlFor={`edit-smtp-username-${channel.id}`} className="text-sm text-slate-400">
                帳號
              </label>
              <input
                id={`edit-smtp-username-${channel.id}`}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
              />
            </div>
          </div>
          <div>
            <label htmlFor={`edit-smtp-password-${channel.id}`} className="text-sm text-slate-400">
              密碼
            </label>
            <input
              id={`edit-smtp-password-${channel.id}`}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
            <p className="mt-1 text-xs text-slate-500">
              Gmail 要用「應用程式密碼」，不是你平常登入的密碼。連接埠留 587 即可。
            </p>
          </div>
          <div>
            <label htmlFor={`edit-from-addr-${channel.id}`} className="text-sm text-slate-400">
              寄件人信箱
            </label>
            <input
              id={`edit-from-addr-${channel.id}`}
              value={fromAddr}
              onChange={(e) => setFromAddr(e.target.value)}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
          <div>
            <label htmlFor={`edit-to-addr-${channel.id}`} className="text-sm text-slate-400">
              收件人信箱
            </label>
            <input
              id={`edit-to-addr-${channel.id}`}
              value={toAddr}
              onChange={(e) => setToAddr(e.target.value)}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
        </>
      )}

      {error && <p className="text-red-400">{error}</p>}

      <div className="flex gap-2">
        <button
          disabled={saveMutation.isPending || !label}
          onClick={() => saveMutation.mutate()}
          className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          儲存
        </button>
        <button
          onClick={onDone}
          className="rounded bg-slate-800 px-3 py-1 text-sm font-medium text-slate-300 hover:bg-slate-700"
        >
          取消
        </button>
      </div>
    </div>
  )
}

function ChannelRow({ channel }: { channel: NotificationChannel }) {
  const queryClient = useQueryClient()
  const [testResult, setTestResult] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)

  const testMutation = useMutation({
    mutationFn: () => api.post<{ ok: boolean; error: string | null }>(`/api/notifications/channels/${channel.id}/test`),
    onSuccess: (result) => setTestResult(result.ok ? '已送出。' : `失敗：${result.error}`),
  })

  const deleteMutation = useMutation({
    mutationFn: async () => {
      if (channel.channel_type === 'web_push') await unsubscribeFromPush()
      return api.delete(`/api/notifications/channels/${channel.id}`)
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['notification-channels'] }),
  })

  return (
    <>
      <tr className="border-b border-slate-800">
        <td className="py-2 pr-4 font-medium">{channel.label}</td>
        <td className="py-2 pr-4 text-slate-400">{CHANNEL_LABEL[channel.channel_type]}</td>
        <td className="py-2 pr-4 text-slate-400">{channel.config_preview}</td>
        {/* Whether it is actually delivering. A disabled channel and a
            silently failing one used to look exactly like a working one, so
            "my phone stopped ringing" had no answer anywhere on screen. */}
        <td className="py-2 pr-4" data-testid="channel-health">
          <ChannelHealth channel={channel} />
        </td>
        <td className="py-2 pr-4">
          <div className="flex items-center gap-2">
            <button
              disabled={testMutation.isPending}
              onClick={() => testMutation.mutate()}
              className="rounded bg-slate-700 px-3 py-1 text-sm font-medium hover:bg-slate-600 disabled:opacity-50"
            >
              測試
            </button>
            <button
              onClick={() => setEditing((v) => !v)}
              className="rounded bg-slate-700 px-3 py-1 text-sm font-medium hover:bg-slate-600"
            >
              編輯
            </button>
            <button
              disabled={deleteMutation.isPending}
              onClick={() => deleteMutation.mutate()}
              className="rounded bg-red-900 px-3 py-1 text-sm font-medium text-red-200 hover:bg-red-800 disabled:opacity-50"
            >
              刪除
            </button>
            {testResult && <span className="text-sm text-slate-400">{testResult}</span>}
          </div>
        </td>
      </tr>
      {editing && (
        <tr>
          <td colSpan={4} className="pb-4">
            <EditChannelForm channel={channel} onDone={() => setEditing(false)} />
          </td>
        </tr>
      )}
    </>
  )
}

function NewChannelForm({ onDone }: { onDone: () => void }) {
  const [channelType, setChannelType] = useState<ChannelType>('telegram')
  const [label, setLabel] = useState('')
  const [botToken, setBotToken] = useState('')
  const [chatId, setChatId] = useState('')
  const [accessToken, setAccessToken] = useState('')
  const [to, setTo] = useState('')
  const [host, setHost] = useState('')
  const [port, setPort] = useState('587')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [fromAddr, setFromAddr] = useState('')
  const [toAddr, setToAddr] = useState('')
  const [error, setError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const createMutation = useMutation({
    mutationFn: async () => {
      if (channelType === 'web_push') {
        const { public_key } = await api.get<{ public_key: string }>(
          '/api/notifications/push/vapid-public-key',
        )
        const config = await subscribeToPush(public_key)
        return api.post('/api/notifications/channels', { channel_type: channelType, label, config })
      }
      const config =
        channelType === 'telegram'
          ? { bot_token: botToken, chat_id: chatId }
          : channelType === 'line'
            ? { access_token: accessToken, to }
            : emailConfig({ host, port, username, password, fromAddr, toAddr })
      return api.post('/api/notifications/channels', { channel_type: channelType, label, config })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-channels'] })
      onDone()
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : '建立失敗'),
  })

  return (
    <div className="space-y-3 rounded border border-slate-800 p-4">
      <div className="flex gap-4">
        {(['telegram', 'line', 'email', 'web_push'] as const).map((type) => (
          <label key={type} className="flex items-center gap-1 text-sm">
            <input
              type="radio"
              name="channel-type"
              checked={channelType === type}
              onChange={() => setChannelType(type)}
            />
            {type === 'web_push' ? '瀏覽器推播' : type}
          </label>
        ))}
      </div>

      <div>
        <label htmlFor="channel-label" className="text-sm text-slate-400">
          名稱
        </label>
        <input
          id="channel-label"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
      </div>

      {channelType === 'telegram' && (
        <>
          <div>
            <label htmlFor="bot-token" className="text-sm text-slate-400">
              機器人權杖（Bot Token）
            </label>
            <input
              id="bot-token"
              value={botToken}
              onChange={(e) => setBotToken(e.target.value)}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
          <div>
            <label htmlFor="chat-id" className="text-sm text-slate-400">
              聊天室代號（Chat ID）
            </label>
            <input
              id="chat-id"
              value={chatId}
              onChange={(e) => setChatId(e.target.value)}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
        </>
      )}

      {channelType === 'line' && (
        <>
          <div>
            <label htmlFor="access-token" className="text-sm text-slate-400">
              存取權杖（Access Token）
            </label>
            <input
              id="access-token"
              value={accessToken}
              onChange={(e) => setAccessToken(e.target.value)}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
          <div>
            <label htmlFor="line-to" className="text-sm text-slate-400">
              接收者使用者 ID
            </label>
            <input
              id="line-to"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
        </>
      )}

      {channelType === 'email' && (
        <>
          <div>
            <label htmlFor="smtp-host" className="text-sm text-slate-400">
              SMTP 主機
            </label>
            <input
              id="smtp-host"
              value={host}
              onChange={(e) => setHost(e.target.value)}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label htmlFor="smtp-port" className="text-sm text-slate-400">
                連接埠
              </label>
              <input
                id="smtp-port"
                value={port}
                onChange={(e) => setPort(e.target.value)}
                inputMode="numeric"
                className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
              />
            </div>
            <div className="flex-1">
              <label htmlFor="smtp-username" className="text-sm text-slate-400">
                帳號
              </label>
              <input
                id="smtp-username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
              />
            </div>
          </div>
          <div>
            <label htmlFor="smtp-password" className="text-sm text-slate-400">
              密碼
            </label>
            <input
              id="smtp-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
            <p className="mt-1 text-xs text-slate-500">
              Gmail 要用「應用程式密碼」，不是你平常登入的密碼。連接埠留 587 即可。
            </p>
          </div>
          <div>
            <label htmlFor="from-addr" className="text-sm text-slate-400">
              寄件人信箱
            </label>
            <input
              id="from-addr"
              value={fromAddr}
              onChange={(e) => setFromAddr(e.target.value)}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
          <div>
            <label htmlFor="to-addr" className="text-sm text-slate-400">
              收件人信箱
            </label>
            <input
              id="to-addr"
              value={toAddr}
              onChange={(e) => setToAddr(e.target.value)}
              className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
          </div>
        </>
      )}

      {channelType === 'web_push' && (
        <p className="text-sm text-slate-500">
          {isPushSupported()
            ? '按下方「建立」後，瀏覽器會詢問是否允許通知——請按允許。即使沒有開著這個網頁，只要瀏覽器（或手機）在背景執行，還是能收到通知。'
            : '這個瀏覽器不支援推播通知（iOS 需先將本網站加入主畫面才能使用）。'}
        </p>
      )}

      {error && <p className="text-red-400">{error}</p>}

      <button
        disabled={createMutation.isPending || !label || (channelType === 'web_push' && !isPushSupported())}
        onClick={() => createMutation.mutate()}
        className="rounded bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
      >
        建立
      </button>
    </div>
  )
}

function NotificationLogs({ channels }: { channels: NotificationChannel[] }) {
  const logsQuery = useQuery({
    queryKey: ['notification-logs'],
    queryFn: () => api.get<NotificationLog[]>('/api/notifications/logs'),
  })
  const logs = logsQuery.data ?? []
  const logQueryClient = useQueryClient()

  const deleteLog = useMutation({
    mutationFn: (id: number) => api.delete(`/api/notifications/logs/${id}`),
    onSuccess: () => logQueryClient.invalidateQueries({ queryKey: ['notification-logs'] }),
  })

  const clearLogs = useMutation({
    mutationFn: () => api.delete<{ deleted: number }>('/api/notifications/logs'),
    onSuccess: () => logQueryClient.invalidateQueries({ queryKey: ['notification-logs'] }),
  })
  const labelFor = (channelId: number) =>
    channels.find((c) => c.id === channelId)?.label ?? `#${channelId}`

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-300">發送紀錄</h2>
        {logs.length > 0 && (
          // Nothing else prunes this table, so "clear" is the only thing
          // standing between a few channels running for a year and tens of
          // thousands of rows on a free-tier database. Anything still queued
          // for retry survives -- that is a delivery the owner has not had
          // yet, not history.
          <DeleteButton
            what="全部的發送紀錄（還在重送的不會被刪）"
            label="清空全部"
            tone="loud"
            onConfirm={() => clearLogs.mutate()}
            pending={clearLogs.isPending}
            error={clearLogs.error}
          />
        )}
      </div>
      {logs.length === 0 && logsQuery.isSuccess && (
        <p className="text-slate-500">目前沒有發送紀錄。</p>
      )}
      {logs.length > 0 && (
        <table className="w-full text-left text-sm">
          <thead className="text-slate-500">
            <tr>
              <th className="pb-2 font-normal">管道</th>
              <th className="pb-2 font-normal">事件</th>
              <th className="pb-2 font-normal">狀態</th>
              <th className="pb-2 font-normal">錯誤</th>
              <th className="pb-2 font-normal">時間</th>
              <th className="pb-2 font-normal">操作</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((log) => (
              <tr key={log.id} className="border-b border-slate-800 text-slate-300">
                <td className="py-2 pr-4 font-medium">{labelFor(log.channel_id)}</td>
                <td className="py-2 pr-4">{EVENT_LABEL[log.event] ?? log.event}</td>
                <td className={`py-2 pr-4 ${log.status === 'sent' ? 'text-emerald-400' : 'text-red-400'}`}>
                  {STATUS_LABEL[log.status]}
                </td>
                <td className="py-2 pr-4 text-red-400">{log.error ?? ''}</td>
                <td className="py-2 pr-4 text-slate-500">
                  {new Date(log.created_at).toLocaleString()}
                </td>
                <td className="py-2">
                  <DeleteButton
                    what="這筆發送紀錄"
                    onConfirm={() => deleteLog.mutate(log.id)}
                    pending={deleteLog.isPending && deleteLog.variables === log.id}
                    error={deleteLog.variables === log.id ? deleteLog.error : null}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

export function NotificationsPage() {
  const [showForm, setShowForm] = useState(false)
  const channelsQuery = useQuery({
    queryKey: ['notification-channels'],
    queryFn: () => api.get<NotificationChannel[]>('/api/notifications/channels'),
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">通知管道</h1>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500"
        >
          新增管道
        </button>
      </div>

      {showForm && <NewChannelForm onDone={() => setShowForm(false)} />}

      <table className="w-full text-left text-sm">
        <thead className="text-slate-500">
          <tr>
            <th className="pb-2 font-normal">名稱</th>
            <th className="pb-2 font-normal">類型</th>
            <th className="pb-2 font-normal">設定</th>
            <th className="pb-2 font-normal">狀態</th>
            <th className="pb-2 font-normal">操作</th>
          </tr>
        </thead>
        <tbody>
          {(channelsQuery.data ?? []).map((channel) => (
            <ChannelRow key={channel.id} channel={channel} />
          ))}
        </tbody>
      </table>

      <NotificationLogs channels={channelsQuery.data ?? []} />
    </div>
  )
}
