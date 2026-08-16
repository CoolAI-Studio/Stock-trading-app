import { QueryClient } from '@tanstack/react-query'
import { renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useWebSocket } from './useWebSocket'
import { api } from './api'

vi.mock('./api', () => ({ api: { post: vi.fn() } }))

class MockWebSocket {
  static instances: MockWebSocket[] = []
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  url: string
  closed = false

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  close() {
    this.closed = true
  }

  emit(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) })
  }
}

describe('useWebSocket', () => {
  beforeEach(() => {
    MockWebSocket.instances = []
    vi.stubGlobal('WebSocket', MockWebSocket)
    vi.mocked(api.post).mockResolvedValue({ ticket: 'a-ticket', expires_in: 30 } as never)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function renderWithClient(queryClient: QueryClient) {
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    return renderHook(() => useWebSocket(true), { wrapper })
  }

  it('requests a ticket and opens a socket to /ws', async () => {
    const queryClient = new QueryClient()
    renderWithClient(queryClient)

    await vi.waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    expect(MockWebSocket.instances[0].url).toContain('/ws?ticket=a-ticket')
  })

  it('invalidates the orders query on an order.created event', async () => {
    const queryClient = new QueryClient()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    renderWithClient(queryClient)

    await vi.waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    MockWebSocket.instances[0].emit({ type: 'order.created', ts: 'now', v: 1, data: { order_id: 1 } })

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['orders'] })
  })

  it('invalidates market quotes on a quote.update event', async () => {
    const queryClient = new QueryClient()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    renderWithClient(queryClient)

    await vi.waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    MockWebSocket.instances[0].emit({ type: 'quote.update', ts: 'now', v: 1, data: {} })

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['market-quotes'] })
  })

  it('does not connect when disabled', async () => {
    const queryClient = new QueryClient()
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    renderHook(() => useWebSocket(false), { wrapper })

    await new Promise((resolve) => setTimeout(resolve, 10))
    expect(MockWebSocket.instances).toHaveLength(0)
  })
})
