/**
 * 右下角那一格：你這一份骨架是哪一版。
 *
 * ＊ 為什麼要一直在畫面上。
 *
 * 版本資訊本來只在系統狀態頁——而那是一個他不會主動打開的地方。他打開它的時候，通
 * 常是因為已經出事了。
 *
 * 而這個模型（骨架由上游修、使用者自己管他加的東西）成立的前提，是他**隨時知道自己
 * 在哪一版**。那件事只有一直看得到才算數。
 *
 * ＊ 但它不可以吵。
 *
 * 沒事的時候它就是角落一行灰字。一個平常就在閃的東西，會讓他在真的該看的那一次也不
 * 看——這跟系統狀態頁「已經是最新就什麼都不說」是同一條規則。
 *
 * ＊ 「不知道」不可以畫成「已經是最新」。
 *
 * 跟後端 build_info、update_check、系統狀態頁一樣的規則。這裡是它第四次出現，因為它
 * 每一次被違反的後果都一樣：他錯過安全修補。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { VersionBadge } from './VersionBadge'
import { ApiError, api } from '../lib/api'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

vi.mock('../lib/buildInfo', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/buildInfo')>()),
  FRONTEND_COMMIT: 'aaaaaaa',
}))

function show(update: unknown) {
  vi.mocked(api.get).mockResolvedValue({ update } as never)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  // 包 Router：這一格裡有一個連到 /system 的連結，而它實際上就是掛在 Layout
  // 裡面（Router 之內）。
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <VersionBadge signedIn />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => vi.clearAllMocks())

describe('右下角的版本', () => {
  it('平常就顯示現在這一版，不用他去翻', async () => {
    show({ running: 'aaaaaaa', latest: 'aaaaaaa', behind: false, why: null })

    expect(await screen.findByText(/aaaaaaa/)).toBeInTheDocument()
  })

  it('落後的時候說得出來，而且說得出最新是哪一版', async () => {
    show({ running: 'aaaaaaa', latest: 'bbbbbbb', behind: true, why: null })

    const badge = await screen.findByRole('status')
    expect(badge).toHaveTextContent('bbbbbbb')
    expect(badge).toHaveTextContent(/新版/)
  })

  it('沒事的時候不要用顏色吵他', async () => {
    // 一個平常就在閃的東西，會讓他在真的該看的那一次也不看。
    show({ running: 'aaaaaaa', latest: 'aaaaaaa', behind: false, why: null })

    const badge = await screen.findByRole('status')
    expect(badge.className).not.toMatch(/amber|rose|red/)
  })

  it('查不到的時候說查不到，不說「已經是最新」', async () => {
    show({ running: 'aaaaaaa', latest: null, behind: null, why: '問不到最新版本。' })

    const badge = await screen.findByRole('status')
    expect(badge).toHaveTextContent(/查不到|不知道/)
    expect(badge).not.toHaveTextContent(/最新/)
  })

  it('連自己是哪一版都不知道的時候，還是要說話', async () => {
    // 有些平台不告訴容器它建的是哪一個 commit。那時候沉默會讓他以為一切正常——
    // 而正確的訊息是「我不知道我是哪一版」。
    show({ running: null, latest: null, behind: null, why: '這個平台沒有告訴這個 app 它是哪一版。' })

    expect(await screen.findByRole('status')).toHaveTextContent(/不知道|查不到/)
  })
})

describe('改過骨架的副本', () => {
  it('把自己的 commit 帶上去問', async () => {
    // 前端的版本是建置期常數，後端不知道——所以那個問題只有前端問得出來。
    show({ running: 'aaaaaaa', latest: 'aaaaaaa', behind: false, why: null })

    await screen.findByRole('status')
    expect(api.get).toHaveBeenCalledWith('/api/system/status?frontend_commit=aaaaaaa')
  })

  it('分岔的時候說「你改過」，不說「有新版可以更新」', async () => {
    // **這是這一塊的全部意義。**
    //
    // 說成「有新版」的話，他照著做（重新部署）拿到的還是自己那一版，因為同步根本
    // 沒跑。重試幾次之後他會放棄，而真正該告訴他的那件事從頭到尾沒有說出口。
    show({
      running: 'aaaaaaa',
      latest: 'bbbbbbb',
      behind: true,
      why: null,
      frontend_from_upstream: false,
    })

    const badge = await screen.findByRole('status')
    expect(badge).toHaveTextContent(/改過|自己的版本|分岔/)
    expect(badge).not.toHaveTextContent(/有新版/)
  })

  it('落後但沒分岔的時候，照舊說有新版', async () => {
    show({
      running: 'aaaaaaa',
      latest: 'bbbbbbb',
      behind: true,
      why: null,
      frontend_from_upstream: true,
    })

    expect(await screen.findByRole('status')).toHaveTextContent(/有新版/)
  })

  it('問不到是不是分岔的時候，不要說他改過', async () => {
    // 誤判成分岔比誤判成落後更糟：那句話會告訴他「自動更新對你沒用」，而如果那是
    // 假的，他會從此不再期待更新——包括安全修補。
    show({
      running: 'aaaaaaa',
      latest: 'bbbbbbb',
      behind: true,
      why: null,
      frontend_from_upstream: null,
    })

    const badge = await screen.findByRole('status')
    expect(badge).not.toHaveTextContent(/改過/)
    expect(badge).toHaveTextContent(/有新版/)
  })
})

describe('一個驚嘆號就好', () => {
  // --- 一個驚嘆號就好 --------------------------------------------------------
  //
  // 使用者：「應該跟其他程式一樣，會有一個驚嘆號提醒就好，要不要裝隨便使用者。」
  //
  // 原本這一格全部都是一行字。字要讀才知道有沒有事，而他不會每次都讀角落——別的軟體
  // 用的是一個掃過去就看得到的記號。要不要更新仍然是他的事，這裡只負責讓他知道。

  it('有新版的時候掛一個驚嘆號', async () => {
    show({ running: 'aaaaaaa', latest: 'bbbbbbb', behind: true, why: null })

    expect(await screen.findByRole('status')).toHaveTextContent('!')
  })

  it('這一份被改過的時候也掛一個 —— 那一種更需要他知道', async () => {
    // 分岔的副本再也不會自動更新，包括安全修補。它比「落後一版」嚴重，不可以反而
    // 比較安靜。
    vi.mocked(api.get).mockResolvedValue({
      update: {
        running: 'aaaaaaa',
        latest: 'bbbbbbb',
        behind: true,
        why: null,
        frontend_from_upstream: false,
      },
    } as never)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <VersionBadge signedIn />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByRole('status')).toHaveTextContent('!')
  })

  it('沒事的時候不掛 —— 平常就在閃的東西，真的有事那一次也沒人看', async () => {
    show({ running: 'aaaaaaa', latest: 'aaaaaaa', behind: false, why: null })

    const badge = await screen.findByRole('status')
    expect(badge).toHaveTextContent(/aaaaaaa/)
    expect(badge).not.toHaveTextContent('!')
  })

  it('分岔的時候要說得出「有一個等你按的更新」', async () => {
    // 同步遇到分岔改成開 PR 之後（見 sync-from-upstream.yml），這件事就不再是
    // 「從此不會更新」而是「有一個等你決定的更新」。畫面上要跟著改口，不然他還是
    // 以為沒救了。
    vi.mocked(api.get).mockResolvedValue({
      update: {
        running: 'aaaaaaa',
        latest: 'bbbbbbb',
        behind: true,
        why: null,
        frontend_from_upstream: false,
      },
    } as never)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <VersionBadge signedIn />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByRole('status')).toHaveTextContent(/等你|PR|自己決定/)
  })

  it('查不到的時候也不給記號 —— 那不是他按得下去的東西', async () => {
    // 那多半是 GitHub 抖一下。一個會因為別人抖一下就亮起來的記號，兩天之後就沒有
    // 人看了——而「查不到」該說的話已經在那一行字裡。
    show({ running: 'aaaaaaa', latest: null, behind: null, why: '問不到 GitHub。' })

    const badge = await screen.findByRole('status')
    expect(badge).toHaveTextContent(/查不到|不知道/)
    expect(badge).not.toHaveTextContent('!')
  })
})

describe('還沒登入的時候', () => {

  it('登入頁也看得到版本 —— 那是他第一眼看到的畫面', async () => {
    // 版本只在登入之後才有的話，「隨時知道自己在哪一版」就少了一半：他第一眼看到
    // 的就是登入頁。
    //
    // 來源是 /healthz——它本來就是公開的（外部看門狗每五分鐘打一次），而且已經帶
    // 著 version.commit。
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/healthz') return { version: { commit: 'aaaaaaa' } } as never
      throw new ApiError(401, 'Unauthorized')
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <VersionBadge signedIn={false} />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    expect(await screen.findByRole('status')).toHaveTextContent('aaaaaaa')
  })

  it('沒登入的時候不去問上游 —— 那會變成一個不用登入就能觸發對外連線的路徑', async () => {
    // /api/system/status 會去打 GitHub。把那條路開給沒登入的人，等於讓任何人都能
    // 用我們的 IP 消耗 GitHub 的額度——而真的需要知道有沒有新版的時候就問不到了。
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === '/healthz') return { version: { commit: 'aaaaaaa' } } as never
      throw new ApiError(401, 'Unauthorized')
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <VersionBadge signedIn={false} />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await screen.findByRole('status')
    expect(api.get).not.toHaveBeenCalledWith(expect.stringContaining('/api/system/status'))
  })

})

describe('畫面比伺服器舊的時候', () => {
  /**
   * 這一格問的是「**伺服器**在哪一版」，所以瀏覽器手上那份 bundle 是舊的時候，它會
   * 一路顯示灰色的「沒事」——伺服器確實沒事。
   *
   * 但他看到的東西是舊的。這正是這個檔案開頭那條規則的另一種違反：「不知道」不可以
   * 畫成「已經是最新」，而這裡是「舊的」被畫成「已經是最新」，更糟。
   *
   * 一次部署（前端後端同一個映像檔）才判得出來：兩半依建構為真是同一個 commit，所以
   * 畫面的 commit 跟伺服器的不一樣，只可能是快取。兩次部署不能這樣比——前端那一份本
   * 來就可能比後端舊，那是另一句話（系統狀態頁在講）。
   */
  it('說出來，並且教他重新整理', async () => {
    show({
      running: 'bbbbbbb',
      latest: 'bbbbbbb',
      behind: false,
      why: null,
      serves_its_own_frontend: true,
    })

    const badge = await screen.findByRole('status')
    expect(badge).toHaveTextContent(/重新整理/)
  })

  it('要看得出來有事，不是一行灰字', async () => {
    show({
      running: 'bbbbbbb',
      latest: 'bbbbbbb',
      behind: false,
      why: null,
      serves_its_own_frontend: true,
    })

    const badge = await screen.findByRole('status')
    expect(badge.className).toMatch(/amber|rose|red|slate-600/)
  })

  it('兩次部署不能這樣比：前端本來就可能比後端舊', async () => {
    // 那是另一句話，系統狀態頁在講（去前端那個平台按重新部署）。這裡講的話會
    // 給錯的辦法。
    show({
      running: 'bbbbbbb',
      latest: 'bbbbbbb',
      behind: false,
      why: null,
      serves_its_own_frontend: false,
    })

    const badge = await screen.findByRole('status')
    expect(badge).not.toHaveTextContent(/重新整理/)
  })

  it('同版就什麼都不提', async () => {
    show({
      running: 'aaaaaaa',
      latest: 'aaaaaaa',
      behind: false,
      why: null,
      serves_its_own_frontend: true,
    })

    const badge = await screen.findByRole('status')
    expect(badge).not.toHaveTextContent(/重新整理/)
    expect(badge.className).not.toMatch(/amber|rose|red/)
  })

  it('落後於上游比快取更值得說', async () => {
    // 兩件事同時成立的時候（伺服器落後，而且瀏覽器的畫面又更舊），先說落後——
    // 那個要他做的事比較大，而且重新整理之後那句話還是會在。
    show({
      running: 'bbbbbbb',
      latest: 'ccccccc',
      behind: true,
      serves_its_own_frontend: true,
      why: null,
    })

    const badge = await screen.findByRole('status')
    expect(badge).toHaveTextContent(/新版/)
  })
})
