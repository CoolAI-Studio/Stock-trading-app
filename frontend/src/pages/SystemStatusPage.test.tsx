import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SystemStatusPage } from './SystemStatusPage'
import { api } from '../lib/api'
import type { SystemStatus } from '../lib/types'

vi.mock('../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }))

/**
 * 「是不是還在跑」, answered inside the app.
 *
 * CLAUDE.md asks for Prometheus and Grafana and gives the reason: 「警告不能停擺，
 * 就必須看得到它有沒有在跑」. The reason is right and this page serves it; the
 * instruments were wrong for the audience. A scraped metrics endpoint needs a
 * Grafana Cloud account and an eighth blank in the deploy form, for a dashboard
 * somebody who wants stock alerts on their phone will never open.
 *
 * The data was all in the process already. What was missing was a screen.
 */

const HEALTHY: SystemStatus = {
  overall: 'ok',
  worker: { enabled: true, uptime_sec: 3600, last_loop_age_sec: 2, last_poll_age_sec: 3 },
  market_data: { consecutive_empty_polls: 0, stale_symbols: [] },
  assistant_available: false,
  notifications: {
    enabled: true,
    sent: 12,
    retrying: 0,
    deferred: 0,
    given_up: 0,
    reached_nobody: 0,
    window_hours: 24,
  },
}

function show(status: Partial<SystemStatus> = {}) {
  vi.mocked(api.get).mockResolvedValue({ ...HEALTHY, ...status } as never)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <SystemStatusPage />
    </QueryClientProvider>,
  )
}

beforeEach(() => vi.clearAllMocks())

// --- the one line that has to be readable without decoding the rest ---------

describe('一眼看得出來的結論', () => {
  it('都正常的時候就直接說正常', async () => {
    show()

    expect(await screen.findByText(/一切正常/)).toBeInTheDocument()
  })

  it('有東西壞掉的時候不要還說正常', async () => {
    show({ overall: 'fail' })

    expect(await screen.findByText(/停擺|有問題/)).toBeInTheDocument()
    expect(screen.queryByText(/一切正常/)).not.toBeInTheDocument()
  })

  it('讀不到狀態的時候要說，不要留一片空白', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('down'))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <SystemStatusPage />
      </QueryClientProvider>,
    )

    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
})

// --- the failures this product cannot survive --------------------------------

describe('警告會不會停擺', () => {
  it('通知被整個關掉要講出來 —— 那是最安靜的一種停擺', async () => {
    show({
      overall: 'fail',
      notifications: { ...HEALTHY.notifications, enabled: false },
    })

    expect(await screen.findByText(/通知功能.*關|關.*通知功能/)).toBeInTheDocument()
  })

  it('抓不到報價的代號要列出名字，不是只給一個數量', async () => {
    // 「有 1 個代號有問題」 sends somebody to work out which. The fix is to
    // correct or delete that one row, and they cannot do either from a count.
    show({
      overall: 'fail',
      market_data: {
        consecutive_empty_polls: 0,
        stale_symbols: [{ symbol: '2330.TW', gap_sec: 1800 }],
      },
    })

    expect(await screen.findByText('2330.TW')).toBeInTheDocument()
  })

  it('worker 停了要看得出來', async () => {
    show({
      overall: 'fail',
      worker: { enabled: true, uptime_sec: 9999, last_loop_age_sec: 9999, last_poll_age_sec: 9999 },
    })

    expect(await screen.findByText(/背景|worker/i)).toBeInTheDocument()
  })

  it('已經放棄的通知要單獨算，不要跟還在重試的混在一起', async () => {
    show({
      overall: 'warn',
      notifications: { ...HEALTHY.notifications, given_up: 3, retrying: 1 },
    })

    const givenUp = await screen.findByText(/放棄/)
    expect(givenUp).toBeInTheDocument()
    expect(screen.getByText(/重試/)).toBeInTheDocument()
  })

  it('沒送到任何管道的要另外講 —— 那是使用者自己修得掉的', async () => {
    show({
      overall: 'warn',
      notifications: { ...HEALTHY.notifications, reached_nobody: 2 },
    })

    expect(await screen.findByText(/沒有送到|沒送到/)).toBeInTheDocument()
  })
})

// --- and the numbers say what window they cover ------------------------------

describe('數字的範圍', () => {
  it('說清楚這些數字是多久以內的', async () => {
    // A lifetime total stops moving and stops meaning anything; without the
    // window, 「送出 12 則」 could be today or could be since March.
    show()

    expect(await screen.findByText(/24/)).toBeInTheDocument()
  })
})

// --- the assistant ------------------------------------------------------------
//
// The question a non-developer actually asks is never 「what does
// last_loop_age_sec mean」 -- it is 「something is wrong and I do not know
// what」. The backend answers that against this deployment's own numbers.
//
// AI_API_KEY is one more blank in a deploy form and is optional by design, so
// the box has to be ABSENT when there is no assistant, not present and
// answering every question with an error.

describe('看不懂的時候問一下', () => {
  it('沒設定 AI 的部署就不要出現這個框', async () => {
    show({ assistant_available: false })

    await screen.findByText(/一切正常/)
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('有設定就給他問', async () => {
    show({ assistant_available: true })

    expect(await screen.findByRole('textbox')).toBeInTheDocument()
  })

  it('問了就把答案顯示出來', async () => {
    show({ assistant_available: true })
    vi.mocked(api.post).mockResolvedValue({
      ok: true,
      reply: 'worker 停了，去 Render 按一次 Manual Deploy。',
      error: null,
    } as never)
    const user = userEvent.setup()

    await user.type(await screen.findByRole('textbox'), '為什麼收不到通知')
    await user.click(screen.getByRole('button', { name: /問/ }))

    expect(await screen.findByText(/Manual Deploy/)).toBeInTheDocument()
  })

  it('AI 回失敗的時候要說，不要留白', async () => {
    show({ assistant_available: true })
    vi.mocked(api.post).mockResolvedValue({
      ok: false,
      reply: null,
      error: 'AI 服務拒絕存取（HTTP 401）',
    } as never)
    const user = userEvent.setup()

    await user.type(await screen.findByRole('textbox'), '怎麼回事')
    await user.click(screen.getByRole('button', { name: /問/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/401/)
  })
})

describe('他這一份是不是舊的', () => {
  it('有新版的時候說出來，而且說得出是哪一版', async () => {
    // 他的副本是從我們的 repo 部署的，而我們每一次改動都是他機器上的一次更新。
    // 這一頁是他唯一看得到「我是不是落後了」的地方。
    show({ update: { running: 'aaaaaaa', latest: 'bbbbbbb', behind: true, why: null } })

    // closest('p')：findByText 命中的是 <strong>，而版本號在它的兄弟節點上。
    const notice = (await screen.findByText(/有新版可以更新/)).closest('p')
    // 只看後端那一格。前端有它自己的一格，而兩個都舊的時候兩個都會出現——
    // 那是對的（它們是兩個獨立的部署），但這一條問的是後端。
    expect(notice).toHaveTextContent('bbbbbbb')
  })

  it('「不知道」不可以畫成「已經是最新」', async () => {
    // 這是這個功能最重要的一條。抓不到 GitHub 的時候說成「已經是最新」，會讓他
    // 錯過安全修補——而那正是他打開這一頁想確認的事。
    show({
      update: {
        running: 'aaaaaaa',
        latest: null,
        behind: null,
        why: '問不到最新版本（ConnectError）。',
      },
    })

    expect(await screen.findByText(/問不到|不知道|查不到/)).toBeInTheDocument()
    expect(screen.queryByText(/已經是最新/)).not.toBeInTheDocument()
  })

  it('已經是最新的時候，不要在畫面上大聲嚷嚷', async () => {
    // 一個永遠有話要說的區塊會讓他學會不看它——而真的有新版的那一次，他也不會看。
    show({ update: { running: 'aaaaaaa', latest: 'aaaaaaa', behind: false, why: null } })

    await screen.findByText(/一切正常/)
    expect(screen.queryByText(/有新版/)).not.toBeInTheDocument()
  })

  it('落後不會把這一頁變成紅燈', async () => {
    // 這一頁的紅燈是給「提醒現在停擺了」用的。把「有新版」也算進去，那個紅燈就
    // 失去意義，而他會學會忽略它。
    show({ update: { running: 'aaaaaaa', latest: 'bbbbbbb', behind: true, why: null } })

    expect(await screen.findByText(/一切正常/)).toBeInTheDocument()
  })
})

vi.mock('../lib/buildInfo', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/buildInfo')>()),
  FRONTEND_COMMIT: 'aaaaaaa',
}))

describe('前端自己是不是舊的', () => {
  it('前端落後的時候要說出來 —— 就算後端是最新的', async () => {
    // Vercel 的 clone 會複製一份 repo，來源就斷了。我們加了每天同步的工作流程，
    // 但它可能不會發生（Actions 沒開、有衝突、他改過程式碼），而那些情況下畫面上
    // 什麼都不會變——他看到的是一個正常運作的 app，只是它是三個月前的。
    //
    // 後端會自己更新（#52），所以「後端最新、前端很舊」正是最可能發生的組合，
    // 也是最不容易被發現的。
    show({ update: { running: 'bbbbbbb', latest: 'bbbbbbb', behind: false, why: null } })

    expect(await screen.findByText(/畫面.*舊|前端.*舊/)).toBeInTheDocument()
  })

  it('前端跟得上的時候，不要在畫面上多說一句', async () => {
    show({ update: { running: 'aaaaaaa', latest: 'aaaaaaa', behind: false, why: null } })

    await screen.findByText(/一切正常/)
    expect(screen.queryByText(/畫面.*舊|前端.*舊/)).not.toBeInTheDocument()
  })

  it('不知道最新是哪一版的時候，不要說前端舊了', async () => {
    // 比不出來就不要下結論。誤報會讓他去重新部署一個其實沒有問題的東西，而重新
    // 部署有它自己的風險。
    show({ update: { running: 'aaaaaaa', latest: null, behind: null, why: '問不到。' } })

    await screen.findByText(/問不到/)
    expect(screen.queryByText(/畫面.*舊|前端.*舊/)).not.toBeInTheDocument()
  })
})

describe('這一版之後改了什麼', () => {
  function withChanges(changes: unknown[]) {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/api/system/updates') return { running: 'aaaaaaa', changes } as never
      return {
        ...HEALTHY,
        update: { running: 'aaaaaaa', latest: 'bbbbbbb', behind: true, why: null },
      } as never
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <SystemStatusPage />
      </QueryClientProvider>,
    )
  }

  it('落後的時候列出每一版做了什麼', async () => {
    // 「有新版」不夠。他要決定的是「值不值得現在更新」，而那個決定只有看得到改了
    // 什麼才做得出來——尤其是「這裡面有沒有安全修補」。
    withChanges([
      { sha: 'ccc1111', title: '修好圖表往前拉沒有資料', at: '2026-08-01T00:00:00Z' },
      { sha: 'ddd2222', title: '策略搬進子行程', at: '2026-08-02T00:00:00Z' },
    ])

    expect(await screen.findByText('修好圖表往前拉沒有資料')).toBeInTheDocument()
    expect(screen.getByText('策略搬進子行程')).toBeInTheDocument()
  })

  it('列不出來的時候不要假裝沒有更新', async () => {
    // 空清單有兩個原因：真的沒有更新，或者比不出來（分岔了、問不到）。畫成「已經
    // 是最新」會讓他錯過安全修補。
    withChanges([])

    expect(await screen.findByText(/列不出|查不到/)).toBeInTheDocument()
  })
})
