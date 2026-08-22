import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { TemplateAlertForm } from './TemplateAlertForm'
import { ApiError, api } from '../lib/api'
import type { StrategyTemplate } from '../lib/types'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn(), post: vi.fn() },
}))

const PRICE_ALERT: StrategyTemplate = {
  key: 'price_alert',
  title: '到價提醒',
  summary: '跌破或漲過你設的價位，就通知你。',
  good_for: '只想要「跌到我想買的價位叫我」的時候。',
  fields: [
    {
      key: 'buy_below',
      label: '跌破多少通知我',
      help: '例如 900。留 0 表示這一邊不用管。',
      kind: 'number',
      default: 0,
      minimum: 0,
    },
    {
      key: 'sell_above',
      label: '漲過多少通知我',
      help: '例如 1200。留 0 表示這一邊不用管。',
      kind: 'number',
      default: 0,
      minimum: 0,
    },
  ],
}

const MA_BREAK: StrategyTemplate = {
  key: 'ma_break',
  title: '跌破均線',
  summary: '收盤價從均線上方掉到下方的那一天，通知你。',
  good_for: '想知道走勢轉弱了，但不想每天自己看線。',
  fields: [
    {
      key: 'window',
      label: '幾日均線',
      help: '常見的是 20（月線）或 60（季線）。',
      kind: 'number',
      default: 20,
      minimum: 2,
    },
  ],
}

function renderForm(onCreated = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <TemplateAlertForm onCreated={onCreated} />
    </QueryClientProvider>,
  )
  return onCreated
}

describe('不用寫程式就設定得出一則提醒', () => {
  beforeEach(() => {
    // 每一條都要從零開始：mock 的呼叫紀錄不會自己清掉，而
    // 「這一次沒有送出」和「上一條測試送出過」在 mock.calls 上長得一模一樣。
    vi.clearAllMocks()
    vi.mocked(api.get).mockResolvedValue([PRICE_ALERT, MA_BREAK])
    vi.mocked(api.post).mockResolvedValue({ id: 7, name: '台積電到價', is_active: true })
  })

  it('先給他選一個現成的，每一個都說得出它是做什麼的', async () => {
    renderForm()

    expect(await screen.findByText('到價提醒')).toBeInTheDocument()
    expect(screen.getByText(/跌破或漲過你設的價位/)).toBeInTheDocument()
    expect(screen.getByText(/跌到我想買的價位叫我/)).toBeInTheDocument()
    expect(screen.getByText('跌破均線')).toBeInTheDocument()
  })

  it('選完之後看到的是表單，不是程式碼', async () => {
    // THE WHOLE POINT. 這一頁在這之前只有一個程式碼編輯器，而
    // 「改三個數字」和「不用寫程式」對這個使用者是兩件不同的事。
    const user = userEvent.setup()
    renderForm()

    await user.click(await screen.findByRole('button', { name: /到價提醒/ }))

    expect(await screen.findByLabelText('跌破多少通知我')).toBeInTheDocument()
    expect(screen.getByLabelText('漲過多少通知我')).toBeInTheDocument()
    expect(screen.queryByText(/class Strategy/)).not.toBeInTheDocument()
    expect(document.querySelector('textarea')).toBeNull()
  })

  it('每一格旁邊都寫著那個數字是什麼意思', async () => {
    const user = userEvent.setup()
    renderForm()

    await user.click(await screen.findByRole('button', { name: /到價提醒/ }))

    expect(await screen.findByText(/例如 900/)).toBeInTheDocument()
    expect(screen.getByText(/例如 1200/)).toBeInTheDocument()
  })

  it('送出的東西裡面沒有一行程式碼', async () => {
    const user = userEvent.setup()
    const onCreated = renderForm()

    await user.click(await screen.findByRole('button', { name: /到價提醒/ }))
    await user.type(screen.getByLabelText('股票代號'), '2330.TW')
    await user.clear(screen.getByLabelText('跌破多少通知我'))
    await user.type(screen.getByLabelText('跌破多少通知我'), '900')
    await user.click(screen.getByRole('button', { name: '建立提醒' }))

    await waitFor(() => expect(api.post).toHaveBeenCalled())
    const [path, body] = vi.mocked(api.post).mock.calls[0]
    expect(path).toBe('/api/strategies/from-template')
    expect(body).toMatchObject({
      template: 'price_alert',
      symbol: '2330.TW',
      params: { buy_below: 900, sell_above: 0 },
    })
    expect(body).not.toHaveProperty('source_code')
    await waitFor(() => expect(onCreated).toHaveBeenCalled())
  })

  it('沒填代號就不送出，並且說出缺什麼', async () => {
    const user = userEvent.setup()
    renderForm()

    await user.click(await screen.findByRole('button', { name: /到價提醒/ }))
    await user.click(screen.getByRole('button', { name: '建立提醒' }))

    expect(await screen.findByText(/請填股票代號/)).toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalled()
  })

  it('後端拒絕的時候，把後端的理由原話講出來', async () => {
    // 「失敗」不是訊息。後端已經寫好了一句他看得懂的話（例如
    // 「binance 上沒有 2330.TW」），這裡要做的是不要把它吃掉。
    const user = userEvent.setup()
    // 真的 ApiError，不是長得像的東西：元件靠型別判斷該不該把後端那句話原樣
    // 顯示出來，用一個假的來測等於沒測到那個判斷。
    vi.mocked(api.post).mockRejectedValue(new ApiError(422, 'binance 上沒有 2330.TW'))
    renderForm()

    await user.click(await screen.findByRole('button', { name: /到價提醒/ }))
    await user.type(screen.getByLabelText('股票代號'), '2330.TW')
    await user.click(screen.getByRole('button', { name: '建立提醒' }))

    expect(await screen.findByText(/binance 上沒有 2330.TW/)).toBeInTheDocument()
  })

  it('名字沒填就自己取一個，不要為了一個他不在乎的欄位卡住他', async () => {
    const user = userEvent.setup()
    renderForm()

    await user.click(await screen.findByRole('button', { name: /到價提醒/ }))
    await user.type(screen.getByLabelText('股票代號'), '2330.TW')
    await user.click(screen.getByRole('button', { name: '建立提醒' }))

    await waitFor(() => expect(api.post).toHaveBeenCalled())
    const [, body] = vi.mocked(api.post).mock.calls[0]
    expect((body as { name: string }).name).toContain('2330.TW')
  })

  it('可以退回去換一個範本', async () => {
    const user = userEvent.setup()
    renderForm()

    await user.click(await screen.findByRole('button', { name: /到價提醒/ }))
    await user.click(await screen.findByRole('button', { name: '換一個' }))

    expect(await screen.findByRole('button', { name: /跌破均線/ })).toBeInTheDocument()
  })
})
