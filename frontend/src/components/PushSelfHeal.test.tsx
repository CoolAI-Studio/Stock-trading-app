import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PushSelfHeal } from './PushSelfHeal'
import { api } from '../lib/api'
import * as pushHealth from '../lib/pushHealth'

vi.mock('../lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

vi.mock('../lib/pushHealth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/pushHealth')>()),
  healPushSubscription: vi.fn(),
}))

/**
 * Running the subscription check on every app start.
 *
 * There is no event to hang this off -- iOS does not fire
 * pushsubscriptionchange -- so "when the app opens" is the only moment
 * available. It has to be silent when things are fine (a banner that appears
 * on every load stops being read) and it has to say something when it cannot
 * fix things itself, because that is the case where the owner's alerts are
 * already not arriving.
 */

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <PushSelfHeal />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.get).mockResolvedValue([] as never)
  vi.mocked(pushHealth.healPushSubscription).mockResolvedValue({ kind: 'healthy' })
})

describe('開啟 app 時自動檢查推播訂閱', () => {
  it('一切正常時畫面上什麼都不出現', async () => {
    show()

    await waitFor(() => expect(pushHealth.healPushSubscription).toHaveBeenCalled())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('自動修好了也不要打擾 —— 使用者不需要知道', async () => {
    // The whole value is that it is invisible. Announcing a successful repair
    // trains the owner to dismiss the banner, and then the one that matters
    // gets dismissed too.
    vi.mocked(pushHealth.healPushSubscription).mockResolvedValue({ kind: 'repaired' })
    show()

    await waitFor(() => expect(pushHealth.healPushSubscription).toHaveBeenCalled())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('修不好就要講，因為這代表提醒現在收不到', async () => {
    vi.mocked(pushHealth.healPushSubscription).mockResolvedValue({
      kind: 'needs-attention',
      message: '訂閱失效了，請重新建立。',
    })
    show()

    expect(await screen.findByRole('alert')).toHaveTextContent('訂閱失效了，請重新建立。')
  })

  it('拿管道清單失敗時安靜跳過，不要在啟動時炸掉', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('offline'))
    show()

    await waitFor(() => expect(api.get).toHaveBeenCalled())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('檢查本身丟例外也不能讓頁面壞掉', async () => {
    vi.mocked(pushHealth.healPushSubscription).mockRejectedValue(new Error('boom'))
    show()

    await waitFor(() => expect(pushHealth.healPushSubscription).toHaveBeenCalled())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('只檢查一次，不要每次 render 都跑一遍', async () => {
    const { rerender } = show()
    await waitFor(() => expect(pushHealth.healPushSubscription).toHaveBeenCalled())

    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <PushSelfHeal />
      </QueryClientProvider>,
    )

    expect(pushHealth.healPushSubscription).toHaveBeenCalledTimes(1)
  })
})
