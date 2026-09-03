import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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
  worker: {
    enabled: true,
    uptime_sec: 3600,
    last_loop_age_sec: 2,
    last_poll_age_sec: 3,
    slept_sec: null,
  },
  market_data: { consecutive_empty_polls: 0, stale_symbols: [], stale_bars: [] },
  assistant_available: false,
  database: {
    kind: 'postgres',
    ephemeral: false,
    status: 'ok',
    detail: '資料存在 Postgres 裡。',
  },
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
afterEach(() => vi.unstubAllGlobals())

/** 假裝這一份是從某個網域被送出來的。
 *
 * jsdom 預設是 localhost，也就是「在自己電腦上跑」那條路——而那條路上，叫他去外部
 * 監控服務填一個 localhost 網址是一個永遠不會成功的設定。兩條路要分開測。
 */
function atHost(hostname: string) {
  const origin = `https://${hostname}`
  vi.stubGlobal('location', { ...window.location, hostname, origin, href: `${origin}/` })
}

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
        stale_bars: [],
      },
    })

    expect(await screen.findByText('2330.TW')).toBeInTheDocument()
  })

  it('遷移沒跑成功的時候，這一頁要說得出來', async () => {
    // scripts/start.py 在已經有帳號的部署上刻意不鎖住（一次跑不動的遷移不該讓提醒
    // 全部停擺）。但「不鎖」不等於「不說」——而原本它就是不說：理由只留在容器的 log
    // 裡，而他不會打開那個地方。他會打開的是這一頁。
    //
    // 原因要原樣帶出來：「資料庫有問題」不是一個他可以拿去做事的句子。
    show({
      overall: 'warn',
      database: {
        kind: 'postgres',
        ephemeral: false,
        status: 'warn',
        detail: '上一次啟動時資料庫遷移沒有跑完…原因：column strategies.foo does not exist',
      },
    })

    expect(await screen.findByText(/遷移沒有跑完/)).toBeInTheDocument()
    expect(screen.getByText(/column strategies.foo does not exist/)).toBeInTheDocument()
  })

  it('資料庫好好的時候也要說一句，不然他不知道資料在哪裡', async () => {
    // 那個值是他幾週前填進一張表單的，也可能從來沒填——而「從來沒填」會落在預設的檔案
    // 資料庫上，看起來跟一個正常的 Postgres 一模一樣，直到重新部署把它清空。
    show({
      database: { kind: 'postgres', ephemeral: false, status: 'ok', detail: '資料存在 Postgres 裡。' },
    })

    expect(await screen.findByText(/資料存在 Postgres 裡/)).toBeInTheDocument()
  })

  it('抓不到 K 棒的那幾段也要列出來，而且跟報價分開講', async () => {
    // 報價和 K 棒走的是上游不同的端點，所以「報價正常、K 棒抓不到」是一個真的
    // 組合。兩邊混在一起講的話，他會看到「抓不到報價：沒有」然後以為行情都好好
    // 的，而他的週線策略一則提醒都發不出來。
    //
    // 具名，不是計數：要知道是哪一段，他才知道要去改哪一列。
    show({
      overall: 'fail',
      market_data: {
        consecutive_empty_polls: 0,
        stale_symbols: [],
        stale_bars: [{ series: 'AAPL 1wk', gap_sec: 1800 }],
      },
    })

    expect(await screen.findByText('AAPL 1wk')).toBeInTheDocument()
  })

  it('worker 停了要看得出來', async () => {
    show({
      overall: 'fail',
      worker: {
        enabled: true,
        uptime_sec: 9999,
        last_loop_age_sec: 9999,
        last_poll_age_sec: 9999,
        slept_sec: null,
      },
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

  // 「有新版」這句話分不出兩件差很多的事：更新流程好好的、只是剛好落後一版；還是
  // 更新流程壞掉了，而他已經半年沒收到任何東西（包括安全修補）。畫面上兩種長得一模
  // 一樣，而第二種是這個專案最怕的形狀——什麼都沒壞，只是安靜地停在那裡。
  describe('落後多久', () => {
    afterEach(() => vi.useRealTimers())

    function at(daysAgo: number): string {
      return new Date(Date.parse('2026-09-03T00:00:00Z') - daysAgo * 86_400_000).toISOString()
    }

    function frozenAt2026_09_03() {
      vi.useFakeTimers({ shouldAdvanceTime: true })
      vi.setSystemTime(new Date('2026-09-03T00:00:00Z'))
    }

    it('說得出落後幾天，不是只說「有新版」', async () => {
      frozenAt2026_09_03()
      withChanges([
        { sha: 'ccc1111', title: '最早那一個沒拿到的', at: at(45) },
        { sha: 'ddd2222', title: '後來又有一個', at: at(2) },
      ])

      // 45，不是 2：問的是「我從什麼時候開始沒跟上」，所以看最早那一個。
      expect(await screen.findByText(/落後 45 天/)).toBeInTheDocument()
    })

    it('落後很久的時候要說那多半是自動更新斷了，不是他還沒去按', async () => {
      // 這一句才是這一格真正的用途。他看到「有新版」會以為那是待辦事項；看到「落後
      // 一百多天」才會想到去看部署平台上有沒有一直失敗的 build——而那正是
      // DEPLOYMENT.md 第 8 節「情況 D」那一種：什麼都沒壞，只是再也不會更新了。
      frozenAt2026_09_03()
      withChanges([{ sha: 'ccc1111', title: '很久以前的', at: at(187) }])

      const hint = await screen.findByText(/自動更新/)
      // 兩個成因都要指得出來。只講一個，他就會去看一個其實沒有問題的地方，然後以為
      // 自己弄錯了——而真正關掉開關的那一個從頭到尾沒有被提起。
      expect(hint).toHaveTextContent(/情況 D|build/)
      expect(hint).toHaveTextContent(/情況 E|GitHub/)
    })

    it('只落後幾天的時候不要嚇他', async () => {
      // 平常就在喊的東西，真的出事那一次也不會有人看。
      frozenAt2026_09_03()
      withChanges([{ sha: 'ccc1111', title: '昨天的', at: at(3) }])

      expect(await screen.findByText(/落後 3 天/)).toBeInTheDocument()
      expect(screen.queryByText(/自動更新/)).not.toBeInTheDocument()
    })
  })
})

// --- 「你剛剛有八個小時沒有在盯盤」 ------------------------------------------

/**
 * Render 免費方案沒有外來流量 15 分鐘就休眠，而休眠期間一則提醒都不會送出。
 *
 * 醒來之後每一個探測都是綠的：行程剛起來，心跳是新的。看門狗也看不到——它去打
 * /healthz 的那一下就是把服務叫醒的那一下。所以那段空白只有一個地方講得出來，
 * 就是他自己會打開的這一頁。
 *
 * 後端從 market_quotes.fetched_at 回頭算（見 market_loop.note_downtime_since_last_run）。
 * 這裡守的是「算出來了，而畫面上真的看得到，而且看得懂是多久」。
 */
describe('這個行程起來之前的那段空白', () => {
  it('睡了很久就要說出來，而且要說多久', async () => {
    show({
      worker: { ...HEALTHY.worker, slept_sec: 8 * 3600 },
    })

    expect(await screen.findByText(/沒有在盯盤|沒有人在看|停過/)).toBeInTheDocument()
    expect(screen.getByText(/8 小時/)).toBeInTheDocument()
  })

  it('要說得出可以怎麼辦，不然他讀完只會擔心', async () => {
    atHost('alerts.onrender.com')
    show({ worker: { ...HEALTHY.worker, slept_sec: 8 * 3600 } })

    expect(await screen.findByText(/休眠|保持喚醒|閒置/)).toBeInTheDocument()
  })

  it('本機跑的那一份不可以叫他去設外部監控 —— 那個網址從外面打不到', async () => {
    // jsdom 的預設網址就是 localhost，所以這一條測的正是「在自己電腦上跑」那條路。
    // 叫他去 UptimeRobot 填 http://localhost:8000/healthz，對方永遠連不上，而他會
    // 花時間去弄一個不可能成功的設定。
    //
    // 本機那條路的真相不一樣，也更誠實：電腦關機或睡著的時候本來就沒有在盯盤，那
    // 不是設定問題，是這條路的性質。
    show({ worker: { ...HEALTHY.worker, slept_sec: 8 * 3600 } })

    expect(await screen.findByText(/沒有在盯盤/)).toBeInTheDocument()
    expect(screen.queryByText(/監控服務|UptimeRobot/)).not.toBeInTheDocument()
    expect(screen.queryByText(new RegExp('localhost.*/healthz'))).not.toBeInTheDocument()
    // 但要說得出這條路為什麼會這樣，以及想要 24 小時盯盤該怎麼辦。
    expect(screen.getByText(/關機|睡著|自己的電腦/)).toBeInTheDocument()
    // 而且說得出想要 24 小時盯盤該往哪走，不是只說「就是這樣」。
    expect(screen.getByText(/雲端/)).toBeInTheDocument()
  })

  it('雲端那一份要把該貼去監控服務的網址印出來，不是叫他去文件裡找', async () => {
    // CLAUDE.md：「永遠不要叫他去別的地方拿一個值」。而這個值只有這份部署自己
    // 知道——每個人的網域都不一樣，寫死一個上游的網址就是給錯的答案。
    atHost('alerts.onrender.com')
    show({ worker: { ...HEALTHY.worker, slept_sec: 8 * 3600 } })

    expect(await screen.findByText('https://alerts.onrender.com/healthz?deep=1')).toBeInTheDocument()
    expect(screen.getByText(/監控/)).toBeInTheDocument()
  })

  it('沒有那段空白的時候一個字都不要提', async () => {
    show()

    expect(await screen.findByText(/背景 worker/)).toBeInTheDocument()
    expect(screen.queryByText(/沒有在盯盤|休眠/)).not.toBeInTheDocument()
  })

  it('那段空白不會讓整頁變成「停擺」', async () => {
    // 它已經過去了。現在是醒著的，而把它算成故障會讓紅燈失去意義——免費方案
    // 本來就會反覆休眠。
    show({ worker: { ...HEALTHY.worker, slept_sec: 8 * 3600 } })

    expect(await screen.findByText(/一切正常/)).toBeInTheDocument()
  })
})

// --- 「你看到的畫面是舊的」後面該接什麼 --------------------------------------

/**
 * 有兩種部署形狀，而修法完全不同：
 *
 *   兩次部署（後端 Render、前端 Vercel）→ 前端那一份真的落後了 → 去那個平台重新部署
 *   一次部署（同一個映像檔，按鈕那條路）→ 兩半依建構為真同版，所以唯一可能的原因是
 *                                          瀏覽器手上那份 bundle 是舊的（#92）
 *                                          → 重新整理就好
 *
 * 對一次部署的人講第一種，他會去找一個不存在的平台，找不到，然後得到「這個 app 壞了
 * 而我修不好」——而真正有效的動作只要一次重新整理。
 */
describe('畫面比伺服器舊的時候', () => {
  const OLD = 'aaaaaaa'

  it('一次部署：說是快取，教他重新整理', async () => {
    show({
      update: {
        running: 'bbbbbbb',
        latest: 'bbbbbbb',
        behind: false,
        why: null,
        serves_its_own_frontend: true,
      },
    })

    expect(await screen.findByText(/伺服器上已經是新的/)).toBeInTheDocument()
    // 說得出可以怎麼辦，而且那個辦法真的有用。
    expect(screen.getAllByText(/重新整理/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/部署前端的平台/)).not.toBeInTheDocument()
  })

  it('兩次部署：說是前端那一份落後了，教他去那個平台', async () => {
    show({
      update: {
        running: 'bbbbbbb',
        latest: 'bbbbbbb',
        behind: false,
        why: null,
        serves_its_own_frontend: false,
      },
    })

    expect(await screen.findByText(/部署前端的平台/)).toBeInTheDocument()
  })

  it('兩邊同版就一個字都不要提', async () => {
    show({
      update: {
        running: OLD,
        latest: OLD,
        behind: false,
        why: null,
        serves_its_own_frontend: true,
      },
    })

    expect(await screen.findByText(/背景 worker/)).toBeInTheDocument()
    expect(screen.queryByText(/你看到的這個畫面是舊的/)).not.toBeInTheDocument()
  })
})
