import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SetupPage } from './SetupPage'
import { ApiError, api } from '../lib/api'
import type { SetupStatus } from '../lib/types'

// The page reads ApiError to tell 「configured, so the endpoint is gone」 from
// a real failure, so the mock has to carry the real class -- a stubbed one
// would never match `instanceof`.
vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn(), post: vi.fn() },
}))

/**
 * The first screen a stranger sees after clicking 「Deploy to Render」.
 *
 * They are not a developer. render.yaml asks them for seven values, and the
 * instructions for two of them used to be 「run this Python script on your own
 * machine」 -- which for somebody who wants stock alerts on their phone is
 * where the story ends. The backend now stays up and reports what is missing;
 * this page is the half they actually look at.
 *
 * The rule it is built on: NEVER SEND THEM SOMEWHERE ELSE TO GET A VALUE. If
 * the app can produce it, there is a button. If it genuinely cannot -- the
 * database lives on somebody else's service -- the page says so plainly rather
 * than pretending.
 */

const STATUS: SetupStatus = {
  missing: [
    {
      name: 'SECRET_ENCRYPTION_KEY',
      why: '你的 Telegram 權杖、LINE 權杖、Email 密碼都是用這把金鑰加密後才存進資料庫的。',
      how: '按下面的「產生」，把產生出來的值貼回 Render。',
      generator: 'fernet',
    },
    {
      name: 'DATABASE_URL',
      why: '沒有資料庫，這個系統存不了任何東西。',
      how: '去 neon.tech 註冊一個免費帳號、建立一個資料庫。',
      generator: null,
    },
  ],
  where: 'Render 後台 → 你的服務 → 左邊選單 Environment → 找到同名的欄位貼上去。',
}

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <SetupPage />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.get).mockResolvedValue(STATUS as never)
})

// --- what is missing, and why it matters ------------------------------------

describe('還沒設定完的部署', () => {
  it('把每一個缺的欄位列出來', async () => {
    show()

    expect(await screen.findByText('SECRET_ENCRYPTION_KEY')).toBeInTheDocument()
    expect(screen.getByText('DATABASE_URL')).toBeInTheDocument()
  })

  it('每一個都說出「不填會怎樣」', async () => {
    // A list of variable names is what render.yaml already gave them. The
    // reason is the part that decides whether they bother.
    show()

    expect(await screen.findByText(/Telegram 權杖/)).toBeInTheDocument()
    expect(screen.getByText(/存不了任何東西/)).toBeInTheDocument()
  })

  it('說清楚填好的值要貼到哪裡去', async () => {
    // The entire audience has just met Render for the first time and does not
    // know that environment variables live under Settings → Environment.
    show()

    expect(await screen.findByText(/Environment/)).toBeInTheDocument()
  })
})

// --- the button that removes the Python step --------------------------------

describe('app 自己產生得出來的值', () => {
  it('可以產生的欄位有按鈕', async () => {
    show()

    const row = (await screen.findByText('SECRET_ENCRYPTION_KEY')).closest('li')!
    expect(within(row).getByRole('button', { name: /產生/ })).toBeInTheDocument()
  })

  it('產生不出來的欄位就不要給按鈕 —— 那會是騙人的', async () => {
    show()

    const row = (await screen.findByText('DATABASE_URL')).closest('li')!
    expect(within(row).queryByRole('button', { name: /產生/ })).not.toBeInTheDocument()
  })

  it('按下去就把值顯示出來讓他複製', async () => {
    vi.mocked(api.post).mockResolvedValue({ SECRET_ENCRYPTION_KEY: 'generated-key-value' } as never)
    const user = userEvent.setup()
    show()

    const row = (await screen.findByText('SECRET_ENCRYPTION_KEY')).closest('li')!
    await user.click(within(row).getByRole('button', { name: /產生/ }))

    expect(await screen.findByDisplayValue('generated-key-value')).toBeInTheDocument()
  })

  it('一次產生兩個值的（推播金鑰）兩個都要顯示', async () => {
    vi.mocked(api.get).mockResolvedValue({
      ...STATUS,
      missing: [
        {
          name: 'VAPID_PRIVATE_KEY',
          why: '手機推播用的一對金鑰。',
          how: '按「產生」會一次給你完整的一對。',
          generator: 'vapid',
        },
      ],
    } as never)
    vi.mocked(api.post).mockResolvedValue({
      VAPID_PRIVATE_KEY: 'priv-value',
      VAPID_PUBLIC_KEY: 'pub-value',
    } as never)
    const user = userEvent.setup()
    show()

    await user.click(await screen.findByRole('button', { name: /產生/ }))

    expect(await screen.findByDisplayValue('priv-value')).toBeInTheDocument()
    expect(screen.getByDisplayValue('pub-value')).toBeInTheDocument()
  })

  it('產生失敗要說出來，不要留一個沒反應的按鈕', async () => {
    vi.mocked(api.post).mockRejectedValue(new Error('boom'))
    const user = userEvent.setup()
    show()

    const row = (await screen.findByText('SECRET_ENCRYPTION_KEY')).closest('li')!
    await user.click(within(row).getByRole('button', { name: /產生/ }))

    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
})

// --- when it is finished -----------------------------------------------------

describe('設定完成之後', () => {
  it('後端回 404 就代表設定完了，畫面要說出來', async () => {
    // The endpoint disappears the moment there is nothing left to configure,
    // so 404 is the success signal rather than an error to report.
    vi.mocked(api.get).mockRejectedValue(new ApiError(404, '這個部署已經設定完成。'))
    show()

    expect(await screen.findByText(/設定完成/)).toBeInTheDocument()
  })

  it('沒有缺任何東西時也一樣', async () => {
    vi.mocked(api.get).mockResolvedValue({ ...STATUS, missing: [] } as never)
    show()

    expect(await screen.findByText(/設定完成/)).toBeInTheDocument()
  })

  it('讀不到狀態的時候要說，不要假裝設定完了', async () => {
    // 「configured」 is the one conclusion a failed request must never produce:
    // it would send somebody to a login page that cannot work.
    vi.mocked(api.get).mockRejectedValue(new Error('network down'))
    show()

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.queryByText(/設定完成/)).not.toBeInTheDocument()
  })
})
