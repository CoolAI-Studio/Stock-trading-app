import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from '../lib/api'
import type { ChannelType, NotificationChannel } from '../lib/types'

function ChannelRow({ channel }: { channel: NotificationChannel }) {
  const queryClient = useQueryClient()
  const [testResult, setTestResult] = useState<string | null>(null)

  const testMutation = useMutation({
    mutationFn: () => api.post<{ ok: boolean; error: string | null }>(`/api/notifications/channels/${channel.id}/test`),
    onSuccess: (result) => setTestResult(result.ok ? 'Sent.' : `Failed: ${result.error}`),
  })

  const deleteMutation = useMutation({
    mutationFn: () => api.delete(`/api/notifications/channels/${channel.id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['notification-channels'] }),
  })

  return (
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
            Test
          </button>
          <button
            disabled={deleteMutation.isPending}
            onClick={() => deleteMutation.mutate()}
            className="rounded bg-red-900 px-3 py-1 text-sm font-medium text-red-200 hover:bg-red-800 disabled:opacity-50"
          >
            Delete
          </button>
          {testResult && <span className="text-sm text-slate-400">{testResult}</span>}
        </div>
      </td>
    </tr>
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
    onError: (err) => setError(err instanceof ApiError ? err.message : 'Failed to create'),
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
          Label
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
              Bot token
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
              Chat id
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
              Access token
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
              To (user id)
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
              SMTP host
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
              From address
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
              To address
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
        Create
      </button>
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
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Notification Channels</h1>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500"
        >
          Add channel
        </button>
      </div>

      {showForm && <NewChannelForm onDone={() => setShowForm(false)} />}

      <table className="w-full text-left text-sm">
        <thead className="text-slate-500">
          <tr>
            <th className="pb-2 font-normal">Label</th>
            <th className="pb-2 font-normal">Type</th>
            <th className="pb-2 font-normal">Config</th>
            <th className="pb-2 font-normal">Actions</th>
          </tr>
        </thead>
        <tbody>
          {(channelsQuery.data ?? []).map((channel) => (
            <ChannelRow key={channel.id} channel={channel} />
          ))}
        </tbody>
      </table>
    </div>
  )
}
