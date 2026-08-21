import { useEffect } from 'react'
import { type QueryClient, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import { websocketBaseUrl } from './wsUrl'
import type { WsEvent } from './types'

// Derived from the API address unless VITE_WS_URL says otherwise. See
// lib/wsUrl.ts: asking a deployer to paste the same host twice with a
// different scheme is a blank that should not exist.
const WS_BASE_URL = websocketBaseUrl(
  import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000',
  import.meta.env.VITE_WS_URL,
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
