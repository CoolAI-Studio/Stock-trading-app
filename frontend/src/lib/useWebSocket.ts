import { useEffect } from 'react'
import { type QueryClient, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import { websocketBaseUrl } from './wsUrl'
import { resolveApiBase } from './apiBase'
import type { WsEvent } from './types'

// Derived from the API address unless VITE_WS_URL says otherwise. See
// lib/wsUrl.ts: asking a deployer to paste the same host twice with a
// different scheme is a blank that should not exist.
//
// **走 resolveApiBase，跟 REST 那一半同一條路。** 這裡本來自己寫
// `?? 'http://localhost:8000'`，而 #53 之後正式建置根本不設 VITE_API_BASE_URL（後端
// 直接供應前端＝同源）。結果是線上每一份副本的 socket 都指向使用者自己電腦上的 8000
// 埠：頁面正常、REST 正常，只有即時報價永遠不更新。兩個地方各自決定「後端在哪」就是
// 這樣漂移的。
const WS_BASE_URL = websocketBaseUrl(
  resolveApiBase({ base: import.meta.env.VITE_API_BASE_URL, dev: import.meta.env.DEV }),
  import.meta.env.VITE_WS_URL,
  typeof window === 'undefined' ? undefined : window.location.origin,
)
const RECONNECT_DELAY_MS = 3000

function handleMessage(message: WsEvent, queryClient: QueryClient): void {
  switch (message.type) {
    case 'quote.update':
      queryClient.invalidateQueries({ queryKey: ['market-quotes'] })
      break
    case 'order.created':
    case 'order.updated':
      queryClient.invalidateQueries({ queryKey: ['orders'] })
      queryClient.invalidateQueries({ queryKey: ['positions'] })
      break
    case 'strategy.error':
      queryClient.invalidateQueries({ queryKey: ['strategies'] })
      break
    // A watch-only strategy produces nothing else -- no order, no position --
    // so this event is the only thing that can move its record on screen, and
    // refetchOnWindowFocus is off. Without it the alert history the mode
    // exists to build sits frozen for as long as the page stays open.
    case 'strategy.alert':
      queryClient.invalidateQueries({ queryKey: ['strategy-alerts'] })
      queryClient.invalidateQueries({ queryKey: ['strategies'] })
      break
    default:
      break
  }
}

/**
 * Pushes never carry authoritative state directly into the cache -- they
 * just invalidate the relevant react-query key, which triggers a normal
 * REST refetch. This keeps REST and WS from ever disagreeing about what
 * "current" data looks like.
 */
export function useWebSocket(enabled: boolean): void {
  const queryClient = useQueryClient()

  useEffect(() => {
    if (!enabled) return

    let cancelled = false
    let socket: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null

    async function connect() {
      let ticket: string
      try {
        const response = await api.post<{ ticket: string }>('/api/ws/ticket')
        ticket = response.ticket
      } catch {
        if (!cancelled) reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS)
        return
      }
      if (cancelled) return

      socket = new WebSocket(`${WS_BASE_URL}/ws?ticket=${ticket}`)
      socket.onmessage = (event) => {
        const message = JSON.parse(event.data) as WsEvent
        handleMessage(message, queryClient)
      }
      socket.onclose = () => {
        if (!cancelled) reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS)
      }
    }

    connect()

    return () => {
      cancelled = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [enabled, queryClient])
}
