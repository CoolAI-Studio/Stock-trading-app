import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AiSettingsPage } from './AiSettingsPage'
import { api } from '../lib/api'
import type { AiSettings } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: { get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() },
}))

/**
 * Turning the AI on, checking it works, and turning it off.
 *
 * AI was the only secret in this app configured through an environment
 * variable. Nothing on any screen said the feature existed; adding a key meant
 * Render's Environment page, which the app never mentions; and CHANGING one
 * meant a redeploy, because Render restarts the service on every environment
 * change. A minute of downtime to fix a typo in a model name.
 *
 * This page follows what the notification channels already do: write-only over
 * the API, a masked tail so you can tell which key it is, and a button that
 * finds out whether it actually works.
 */

const UNSET: AiSettings = {
  configured: false,
  source: 'none',
  provider: 'openai_compatible',
  base_url: 'https://openrouter.ai/api/v1',
  model: '',
  key_preview: null,
}

const FROM_DB: AiSettings = {
  configured: true,
  source: 'database',
  provider: 'openai_compatible',
  base_url: 'https://openrouter.ai/api/v1',
  model: 'anthropic/claude-sonnet-4.5',
  key_preview: '…mnop',
}

function show(settings: AiSettings = UNSET) {
  vi.mocked(api.get).mockResolvedValue(settings as never)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <AiSettingsPage />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.put).mockResolvedValue(FROM_DB as never)
})

// --- adding one -----------------------------------------------------------------

describe('還沒設定的時候', () => {
  it('說清楚沒設定就是沒有這個功能，其他一切照常', async () => {
    // An optional feature that reads as broken is worse than an absent one.
    show()

    // findAllByText: the sentence lives in a <p> with a <span> inside it, so
    // both nodes match. What is under test is that the page says it, not
    // which element it lands in.
    expect((await screen.findAllByText(/選填|不影響|照常/)).length).toBeGreaterThan(0)
  })

  it('說清楚金鑰是他自己的、發問花的是他自己的錢', async () => {
    // The app never spends money on the owner's behalf anywhere else, and this
    // is the one place it could look like it does.
    show()

    expect((await screen.findAllByText(/你自己|自費|費用/)).length).toBeGreaterThan(0)
  })

  it('存得起來', async () => {
    const user = userEvent.setup()
    show()

    await user.type(await screen.findByLabelText(/模型/), 'anthropic/claude-sonnet-4.5')
    await user.type(screen.getByLabelText(/金鑰/), 'sk-abcdefghijklmnop')
    await user.click(screen.getByRole('button', { name: '儲存' }))

    await vi.waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        '/api/ai-settings',
        expect.objectContaining({ model: 'anthropic/claude-sonnet-4.5', api_key: 'sk-abcdefghijklmnop' }),
      ),
    )
  })
})

// --- what it shows about an existing one -------------------------------------------

describe('已經設定好的時候', () => {
  it('看得出來是哪一把金鑰，但看不到金鑰', async () => {
    show(FROM_DB)

    expect(await screen.findByText(/…mnop/)).toBeInTheDocument()
  })

  it('說得出設定是從哪裡來的', async () => {
    // 「It works and I never set it here」 sends somebody hunting through
    // Render for a value they do not remember typing.
    show({ ...FROM_DB, source: 'env' })

    expect(await screen.findByText(/環境變數|Render/)).toBeInTheDocument()
  })

  it('改模型不用重打金鑰', async () => {
    // The commonest edit by far. Requiring the secret for it sends somebody to
    // a password manager to change a string that is not secret.
    const user = userEvent.setup()
    show(FROM_DB)

    const model = await screen.findByLabelText(/模型/)
    // Waited for the seed: clearing an input the server has not filled in yet
    // is a no-op, and the typed value would then be appended to the seeded one.
    await vi.waitFor(() => expect(model).toHaveValue('anthropic/claude-sonnet-4.5'))
    await user.clear(model)
    await user.type(model, 'openai/gpt-5')
    await user.click(screen.getByRole('button', { name: '儲存' }))

    await vi.waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        '/api/ai-settings',
        expect.objectContaining({ model: 'openai/gpt-5', api_key: null }),
      ),
    )
  })
})

// --- finding out whether it works ---------------------------------------------------

describe('測試按鈕', () => {
  it('成功就說成功', async () => {
    vi.mocked(api.post).mockResolvedValue({ ok: true, reply: 'ok', error: null } as never)
    const user = userEvent.setup()
    show(FROM_DB)

    await user.click(await screen.findByRole('button', { name: /測試/ }))

    expect(await screen.findByText(/可以用|成功/)).toBeInTheDocument()
  })

  it('失敗就把供應者自己的錯誤講出來', async () => {
    // Without this the only way to tell a working key from a wrong one is to
    // use a real feature and read its error, which is how somebody concludes
    // the app is broken rather than their key.
    vi.mocked(api.post).mockResolvedValue({
      ok: false,
      reply: null,
      error: 'AI 服務拒絕存取（HTTP 401）',
    } as never)
    const user = userEvent.setup()
    show(FROM_DB)

    await user.click(await screen.findByRole('button', { name: /測試/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/401/)
  })

  it('還沒設定就不要給測試按鈕', async () => {
    show()

    await screen.findByLabelText(/模型/)
    expect(screen.queryByRole('button', { name: /測試/ })).not.toBeInTheDocument()
  })
})

// --- turning it off --------------------------------------------------------------

describe('關掉', () => {
  it('設定好的才給關', async () => {
    show(FROM_DB)

    expect(await screen.findByRole('button', { name: /清除|停用/ })).toBeInTheDocument()
  })

  it('沒設定的時候沒有這顆按鈕', async () => {
    show()

    await screen.findByLabelText(/模型/)
    expect(screen.queryByRole('button', { name: /清除|停用/ })).not.toBeInTheDocument()
  })

  it('清除之後要重新讀狀態，不要留著舊的畫面', async () => {
    vi.mocked(api.delete).mockResolvedValue(undefined as never)
    const user = userEvent.setup()
    show(FROM_DB)

    await user.click(await screen.findByRole('button', { name: /清除|停用/ }))

    await vi.waitFor(() => expect(api.delete).toHaveBeenCalledWith('/api/ai-settings'))
    await vi.waitFor(() => expect(vi.mocked(api.get).mock.calls.length).toBeGreaterThan(1))
  })
})

// --- failures say so ---------------------------------------------------------------

describe('讀不到設定的時候', () => {
  it('要說，不要假裝沒設定過', async () => {
    // 「Not configured」 over a failed request would invite somebody to type a
    // key they already have, and then fail to save that too.
    vi.mocked(api.get).mockRejectedValue(new Error('down'))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <AiSettingsPage />
      </QueryClientProvider>,
    )

    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
})
