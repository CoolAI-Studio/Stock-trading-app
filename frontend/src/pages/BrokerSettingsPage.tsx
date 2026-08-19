import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from '../lib/api'
import type { AiAssistResult, BrokerCredential } from '../lib/types'

interface ConfigField {
  key: string
  value: string
}

function CredentialRow({ credential }: { credential: BrokerCredential }) {
  const queryClient = useQueryClient()

  const deleteMutation = useMutation({
    mutationFn: () => api.delete(`/api/broker-credentials/${credential.id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['broker-credentials'] }),
  })

  function handleDelete() {
    if (window.confirm(`確定要刪除「${credential.label}」這組憑證嗎？此操作無法復原。`)) {
      deleteMutation.mutate()
    }
  }

  return (
    <tr className="border-b border-slate-800">
      <td className="py-2 pr-4 font-medium">{credential.label}</td>
      <td className="py-2 pr-4">{credential.broker_name}</td>
      <td className="py-2 pr-4 text-slate-400">{credential.config_preview}</td>
      <td className="py-2">
        <button
          disabled={deleteMutation.isPending}
          onClick={handleDelete}
          className="rounded bg-red-900 px-3 py-1 text-sm font-medium text-red-200 hover:bg-red-800 disabled:opacity-50"
        >
          刪除
        </button>
      </td>
    </tr>
  )
}

function NewCredentialForm({ onDone }: { onDone: () => void }) {
  const [label, setLabel] = useState('')
  const [brokerName, setBrokerName] = useState('')
  const [fields, setFields] = useState<ConfigField[]>([{ key: '', value: '' }])
  const [error, setError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const createMutation = useMutation({
    mutationFn: () => {
      const config = Object.fromEntries(
        fields.filter((f) => f.key.trim()).map((f) => [f.key.trim(), f.value]),
      )
      return api.post('/api/broker-credentials', { label, broker_name: brokerName, config })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['broker-credentials'] })
      onDone()
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : '建立失敗'),
  })

  function updateField(index: number, patch: Partial<ConfigField>) {
    setFields((prev) => prev.map((f, i) => (i === index ? { ...f, ...patch } : f)))
  }

  function addField() {
    setFields((prev) => [...prev, { key: '', value: '' }])
  }

  function removeField(index: number) {
    setFields((prev) => prev.filter((_, i) => i !== index))
  }

  const hasAtLeastOneField = fields.some((f) => f.key.trim())

  return (
    <div className="space-y-3 rounded border border-slate-800 p-4">
      <div>
        <label htmlFor="credential-label" className="text-sm text-slate-400">
          名稱
        </label>
        <input
          id="credential-label"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
      </div>
      <div>
        <label htmlFor="credential-broker-name" className="text-sm text-slate-400">
          券商 / 交易所名稱
        </label>
        <input
          id="credential-broker-name"
          value={brokerName}
          onChange={(e) => setBrokerName(e.target.value)}
          placeholder="例如：元大證券 SPARK API"
          className="block w-full rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
      </div>

      <div className="space-y-2">
        <p className="text-sm text-slate-400">憑證欄位（自行輸入券商要求的名稱與值，例如 api_key / api_secret）</p>
        {fields.map((field, index) => (
          <div key={index} className="flex gap-2">
            <input
              aria-label={`欄位名稱 ${index + 1}`}
              value={field.key}
              onChange={(e) => updateField(index, { key: e.target.value })}
              placeholder="欄位名稱，例如 api_key"
              className="w-1/3 rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
            <input
              aria-label={`欄位值 ${index + 1}`}
              value={field.value}
              onChange={(e) => updateField(index, { value: e.target.value })}
              placeholder="值"
              className="flex-1 rounded border border-slate-700 bg-slate-950 px-2 py-1"
            />
            <button
              type="button"
              onClick={() => removeField(index)}
              className="rounded border border-slate-700 px-2 text-slate-400 hover:text-red-400"
            >
              移除
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={addField}
          className="rounded border border-slate-700 px-3 py-1 text-sm hover:bg-slate-800"
        >
          + 新增欄位
        </button>
      </div>

      {error && <p className="text-red-400">{error}</p>}

      <button
        disabled={createMutation.isPending || !label || !brokerName || !hasAtLeastOneField}
        onClick={() => createMutation.mutate()}
        className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
      >
        建立
      </button>
    </div>
  )
}

function AiAssistPanel() {
  const [message, setMessage] = useState('')
  const [history, setHistory] = useState<{ role: 'user' | 'assistant' | 'error'; text: string }[]>([])

  const askMutation = useMutation({
    mutationFn: (text: string) =>
      api.post<AiAssistResult>('/api/broker-credentials/ai-assist', { message: text }),
    onSuccess: (result) => {
      setHistory((prev) => [
        ...prev,
        result.ok
          ? { role: 'assistant' as const, text: result.reply ?? '' }
          : { role: 'error' as const, text: result.error ?? '發生錯誤' },
      ])
    },
    onError: (err) =>
      setHistory((prev) => [
        ...prev,
        { role: 'error' as const, text: err instanceof ApiError ? err.message : '發生錯誤' },
      ]),
  })

  function handleSend() {
    const text = message.trim()
    if (!text) return
    setHistory((prev) => [...prev, { role: 'user', text }])
    askMutation.mutate(text)
    setMessage('')
  }

  return (
    <div className="space-y-3 rounded border border-slate-800 p-4">
      <h2 className="text-sm font-semibold text-slate-300">AI 設定小幫手</h2>
      <p className="text-sm text-slate-500">
        只負責幫你看懂券商 API 文件、確認欄位怎麼填——不會幫你下單，也不會替你送出交易。
      </p>

      <div className="space-y-2">
        {history.map((entry, i) => (
          <p
            key={i}
            className={
              entry.role === 'user'
                ? 'text-slate-200'
                : entry.role === 'error'
                  ? 'text-red-400'
                  : 'text-emerald-300'
            }
          >
            {entry.role === 'user' ? '你：' : entry.role === 'error' ? '錯誤：' : 'AI：'}
            {entry.text}
          </p>
        ))}
      </div>

      <div className="flex gap-2">
        <label htmlFor="ai-assist-message" className="sr-only">
          問題
        </label>
        <input
          id="ai-assist-message"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSend()}
          placeholder="例如：元大證券的 API Key 要去哪裡申請？"
          className="flex-1 rounded border border-slate-700 bg-slate-950 px-2 py-1"
        />
        <button
          disabled={askMutation.isPending || !message.trim()}
          onClick={handleSend}
          className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          送出
        </button>
      </div>
    </div>
  )
}

export function BrokerSettingsPage() {
  const [showForm, setShowForm] = useState(false)
  const credentialsQuery = useQuery({
    queryKey: ['broker-credentials'],
    queryFn: () => api.get<BrokerCredential[]>('/api/broker-credentials'),
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">券商設定</h1>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="rounded bg-emerald-600 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-500"
        >
          新增券商憑證
        </button>
      </div>

      <p className="text-sm text-slate-500">
        目前系統只支援手動確認下單，不會用這裡存的憑證自動下單——這裡只是先幫你安全存放憑證，之後要接哪家券商的自動下單，再另外開發對應的功能。
      </p>

      {showForm && <NewCredentialForm onDone={() => setShowForm(false)} />}

      <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
        <table className="w-full text-left text-sm">
          <thead className="text-slate-500">
            <tr>
              <th className="pb-2 font-normal">名稱</th>
              <th className="pb-2 font-normal">券商 / 交易所</th>
              <th className="pb-2 font-normal">設定</th>
              <th className="pb-2 font-normal">操作</th>
            </tr>
          </thead>
          <tbody>
            {(credentialsQuery.data ?? []).map((credential) => (
              <CredentialRow key={credential.id} credential={credential} />
            ))}
          </tbody>
        </table>
      </div>

      <AiAssistPanel />
    </div>
  )
}
