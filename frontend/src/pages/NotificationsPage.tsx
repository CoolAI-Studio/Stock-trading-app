import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from '../lib/api'
import type { ChannelType, NotificationChannel, NotificationLog } from '../lib/types'

const STATUS_LABEL: Record<'sent' | 'failed', string> = { sent: '已送出', failed: '失敗' }

function EditChannelForm({ channel, onDone }: { channel: NotificationChannel; onDone: () => void }) {
  const [label, setLabel] = useState(channel.label)
  const [isEnabled, setIsEnabled] = useState(channel.is_enabled)
  const [botToken, setBotToken] = useState('')
  const [chatId, setChatId] = useState('')
  const [accessToken, setAccessToken] = useState('')
  const [to, setTo] = useState('')
  const [host, setHost] = useState('')
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
            : { host, from_addr: fromAddr, to_addr: toAddr }
      const hasNewConfig = Object.values(config).some((v) => v.trim() !== '')
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
    mutationFn: () => api.delete(`/api/notifications/channels/${channel.id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['notification-channels'] }),
  })

  return (
    <>
      <tr className="border-b border-slate-800">
        <td className="py-2 pr-4 font-medium">{channel.label}</td>
        <td className="py-2 pr-4 uppercase text-slate-400">{channel.channel_type}</td>
        <td className="py-2 pr-4 text-slate-400">{channel.config_preview}</td>
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
  const [fromAddr, setFromAddr] = useState('')
  const [toAddr, setToAddr] = useState('')
  const [error, setError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const createMutation = useMutation({
    mutationFn: () => {
      const config =
        channelType === 'telegram'
          ? { bot_token: botToken, chat_id: chatId }
          : channelType === 'line'
            ? { access_token: accessToken, to }
            : { host, from_addr: fromAddr, to_addr: toAddr }
      return api.post('/api/notifications/channels', { channel_type: channelType, label, config })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-channels'] })
      onDone()
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : '建立失敗'),
  })

  return (
    <div className="space-y-3 rounded border border-slate-800 p-4">
      <div className="flex gap-4">
        {(['telegram', 'line', 'email'] as const).map((type) => (
          <label key={type} className="flex items-center gap-1 text-sm">
            <input
              type="radio"
              name="channel-type"
              checked={channelType === type}
              onChange={() => setChannelType(type)}
            />
            {type}
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

      {error && <p className="text-red-400">{error}</p>}

      <button
        disabled={createMutation.isPending || !label}
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
  const labelFor = (channelId: number) =>
    channels.find((c) => c.id === channelId)?.label ?? `#${channelId}`

  return (
    <div className="space-y-2">
      <h2 className="text-sm font-semibold text-slate-300">發送紀錄</h2>
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
            </tr>
          </thead>
          <tbody>
            {logs.map((log) => (
              <tr key={log.id} className="border-b border-slate-800 text-slate-300">
                <td className="py-2 pr-4 font-medium">{labelFor(log.channel_id)}</td>
                <td className="py-2 pr-4">{log.event}</td>
                <td className={`py-2 pr-4 ${log.status === 'sent' ? 'text-emerald-400' : 'text-red-400'}`}>
                  {STATUS_LABEL[log.status]}
                </td>
                <td className="py-2 pr-4 text-red-400">{log.error ?? ''}</td>
                <td className="py-2 text-slate-500">{new Date(log.created_at).toLocaleString()}</td>
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
